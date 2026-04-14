import re
from itertools import groupby
from types import SimpleNamespace

from django.core.cache import cache
from django.db.models import Prefetch

from src.football.models import Match, Player
from src.pool.models import PoolBet, PoolParticipant
from src.pool.services.projection import (
    build_projected_placeholder_map,
    load_assign_third_map,
    resolve_knockout_placeholder_team,
)
from src.pool.services.projection_queue import enqueue_projection_recalc, has_pending_projection_recalc
from src.pool.services.rules import PHASE_GROUP, PHASE_KNOCKOUT, phase_for_match

STAGE_R32 = "R32"
STAGE_R16 = "R16"
STAGE_QF = "QF"
STAGE_SF = "SF"
STAGE_FINAL = "FINAL"
STAGE_THIRD = "THIRD"

KNOCKOUT_STAGE_ORDER = [STAGE_R32, STAGE_R16, STAGE_QF, STAGE_SF]
KNOCKOUT_LABELS = {
    STAGE_R32: "32 Avos",
    STAGE_R16: "Oitavas",
    STAGE_QF: "Quartas",
    STAGE_SF: "Semifinal",
}

TOP_SCORER_OPTIONS_CACHE_TTL_SECONDS = 300
_WINNER_PLACEHOLDER_PATTERN = re.compile(r"^W(\d+)$")
_LOSER_PLACEHOLDER_PATTERN = re.compile(r"^RU(\d+)$")


def _ensure_participant_bets(participant, matches, *, can_bet, existing_bets=None):
    if not can_bet:
        return {}

    if existing_bets is None:
        existing = {bet.match_id: bet for bet in participant.bets.select_related("match").all()}
    else:
        existing = {bet.match_id: bet for bet in existing_bets}

    missing_rows = [
        PoolBet(participant=participant, match=match, is_active=False) for match in matches if match.id not in existing
    ]

    if missing_rows:
        PoolBet.objects.bulk_create(missing_rows)
        for bet in missing_rows:
            existing[bet.match_id] = bet

    return existing


def _build_winners_map(matches, bets_by_match_id):
    winners_map = {}
    for match in matches:
        bet = bets_by_match_id.get(match.id)
        if bet and bet.is_active and bet.winner_pred_id:
            winners_map[match.match_number] = bet.winner_pred
            continue
        if (
            bet
            and bet.is_active
            and bet.home_score_pred is not None
            and bet.away_score_pred is not None
            and match.home_team_id
            and match.away_team_id
        ):
            if bet.home_score_pred > bet.away_score_pred:
                winners_map[match.match_number] = match.home_team
                continue
            if bet.away_score_pred > bet.home_score_pred:
                winners_map[match.match_number] = match.away_team
                continue
        if match.winner_id:
            winners_map[match.match_number] = match.winner
    return winners_map


def _infer_advancing_team(match, bet, home_team, away_team):
    if match.winner_id and match.winner:
        return match.winner

    if not bet or not bet.is_active:
        return None

    if bet.winner_pred_id:
        return bet.winner_pred

    if home_team is None or away_team is None or bet.home_score_pred is None or bet.away_score_pred is None:
        return None

    if bet.home_score_pred > bet.away_score_pred:
        return home_team
    if bet.away_score_pred > bet.home_score_pred:
        return away_team

    return None


def _infer_losing_team(winner_team, home_team, away_team):
    if winner_team is None or home_team is None or away_team is None:
        return None
    if winner_team.id == home_team.id:
        return away_team
    if winner_team.id == away_team.id:
        return home_team
    return None


def _resolve_match_team_from_placeholder(placeholder, projected_slots, assign_third_map, winners_map, losers_map):
    normalized = (placeholder or "").replace(" ", "").upper()
    winner_match = _WINNER_PLACEHOLDER_PATTERN.match(normalized)
    if winner_match:
        return winners_map.get(int(winner_match.group(1)))
    loser_match = _LOSER_PLACEHOLDER_PATTERN.match(normalized)
    if loser_match:
        return losers_map.get(int(loser_match.group(1)))
    return resolve_knockout_placeholder_team(normalized, projected_slots, assign_third_map)


def _make_pairs(items):
    return [items[index : index + 2] for index in range(0, len(items), 2)]


def _normalize_stage_key(stage):
    if not stage:
        return ""

    stage_name = (stage.name or "").upper().replace("-", " ").strip()

    if "SEMI" in stage_name or "SF" in stage_name:
        return STAGE_SF
    if "QUART" in stage_name or "QF" in stage_name:
        return STAGE_QF
    if "R16" in stage_name or "OITAV" in stage_name or "ROUND OF 16" in stage_name:
        return STAGE_R16
    if "R32" in stage_name or "32 AVOS" in stage_name or "SEGUNDAS DE FINAL" in stage_name:
        return STAGE_R32
    if "DECIS" in stage_name and "3" in stage_name:
        return STAGE_THIRD
    if "TERCE" in stage_name and "LUGAR" in stage_name:
        return STAGE_THIRD
    if stage_name == "FINAL":
        return STAGE_FINAL
    if "FINAL" in stage_name and "SEMI" not in stage_name and "QUART" not in stage_name and "OITAV" not in stage_name:
        return STAGE_FINAL

    return ""


def _build_projected_knockout_payload(knockout_rows):
    grouped_matches = {stage_key: [] for stage_key in KNOCKOUT_STAGE_ORDER}
    final_match = None
    third_place_match = None
    knockout_by_number = {}

    for row in knockout_rows:
        stage_key = _normalize_stage_key(row["match"].stage)
        projected_match = SimpleNamespace(
            source_match_number=row["match"].match_number,
            source_stage=row["match"].stage,
            source_home_placeholder=row["match"].home_placeholder,
            source_away_placeholder=row["match"].away_placeholder,
            home_team=row["home_team"],
            away_team=row["away_team"],
            home_team_id=row["home_team"].id if row["home_team"] else None,
            away_team_id=row["away_team"].id if row["away_team"] else None,
            home_score=row["bet"].home_score_pred if row["bet"] else None,
            away_score=row["bet"].away_score_pred if row["bet"] else None,
            winner_id=row["bet"].winner_pred_id if row["bet"] else None,
            home_penalty_score=None,
            away_penalty_score=None,
        )

        if stage_key in grouped_matches:
            grouped_matches[stage_key].append(projected_match)
            knockout_by_number[row["match"].match_number] = projected_match
        elif stage_key == STAGE_FINAL and final_match is None:
            final_match = projected_match
        elif stage_key == STAGE_THIRD and third_place_match is None:
            third_place_match = projected_match

    def _winner_source(placeholder):
        normalized = (placeholder or "").replace(" ", "").upper()
        winner_match = _WINNER_PLACEHOLDER_PATTERN.match(normalized)
        if not winner_match:
            return None
        return int(winner_match.group(1))

    children_by_number = {}
    for number, match in knockout_by_number.items():
        children = []
        for placeholder in (match.source_home_placeholder, match.source_away_placeholder):
            child_number = _winner_source(placeholder)
            if child_number is not None and child_number in knockout_by_number:
                children.append(child_number)
        children_by_number[number] = tuple(children)

    def _collect_descendants(root_number):
        if root_number is None:
            return set()

        collected = set()
        stack = [root_number]
        while stack:
            current = stack.pop()
            if current in collected:
                continue
            if current not in knockout_by_number:
                continue
            collected.add(current)
            stack.extend(children_by_number.get(current, ()))
        return collected

    left_root = _winner_source(final_match.source_home_placeholder) if final_match else None
    right_root = _winner_source(final_match.source_away_placeholder) if final_match else None

    left_numbers = _collect_descendants(left_root)
    right_numbers = _collect_descendants(right_root)

    fallback_order = {
        match.source_match_number: idx
        for idx, match in enumerate(sorted(knockout_by_number.values(), key=lambda m: m.source_match_number))
    }

    def _sort_side(side_numbers, root_number):
        if not side_numbers:
            return []

        leaf_order = {}
        counter = [0]

        def _walk(number):
            if number in leaf_order:
                return leaf_order[number]

            children = [child for child in children_by_number.get(number, ()) if child in side_numbers]
            if not children:
                leaf_order[number] = counter[0]
                counter[0] += 1
                return leaf_order[number]

            child_positions = [_walk(child) for child in children]
            leaf_order[number] = min(child_positions)
            return leaf_order[number]

        if root_number in side_numbers:
            _walk(root_number)

        for number in sorted(side_numbers):
            if number not in leaf_order:
                leaf_order[number] = counter[0]
                counter[0] += 1

        sorted_numbers = sorted(
            side_numbers,
            key=lambda number: (leaf_order[number], fallback_order.get(number, 9999)),
        )
        return [knockout_by_number[number] for number in sorted_numbers]

    left_sorted = _sort_side(left_numbers, left_root)
    right_sorted = _sort_side(right_numbers, right_root)

    active_stages = [stage_key for stage_key in KNOCKOUT_STAGE_ORDER if grouped_matches[stage_key]]
    bracket_left = []
    bracket_right = []

    for stage_key in active_stages:
        stage_matches = grouped_matches[stage_key]
        left_matches = [match for match in left_sorted if _normalize_stage_key(match.source_stage) == stage_key]
        right_matches = [match for match in right_sorted if _normalize_stage_key(match.source_stage) == stage_key]

        if not left_matches and not right_matches:
            stage_matches_sorted = sorted(stage_matches, key=lambda match: match.source_match_number)
            half = len(stage_matches_sorted) // 2
            left_matches = stage_matches_sorted[:half]
            right_matches = stage_matches_sorted[half:]

        bracket_left.append(
            {
                "stage": stage_key,
                "label": KNOCKOUT_LABELS[stage_key],
                "pairs": _make_pairs(left_matches),
                "is_outermost": False,
            }
        )
        bracket_right.append(
            {
                "stage": stage_key,
                "label": KNOCKOUT_LABELS[stage_key],
                "pairs": _make_pairs(right_matches),
                "is_outermost": False,
            }
        )

    if bracket_left:
        bracket_left[0]["is_outermost"] = True

    bracket_right = list(reversed(bracket_right))
    if bracket_right:
        bracket_right[-1]["is_outermost"] = True

    max_matches_side = max((sum(len(pair) for pair in round_data["pairs"]) for round_data in bracket_left), default=2)
    bracket_height = max(max_matches_side * 78, 280)

    return {
        "bracket_left": bracket_left,
        "bracket_right": bracket_right,
        "final_match": final_match,
        "third_place_match": third_place_match,
        "bracket_height": bracket_height,
    }


def _top_scorer_options_payload_for_pool(pool):
    cache_key = f"pool:top_scorer_options:season:{pool.season_id}"
    cached_payload = cache.get(cache_key)
    if cached_payload is not None:
        return [
            SimpleNamespace(id=item[0], name=item[1], team=SimpleNamespace(name=item[2])) for item in cached_payload
        ]

    rows = list(
        Player.objects.filter(team__group__stage__season=pool.season)
        .select_related("team")
        .order_by("name")
        .distinct()
        .values_list("id", "name", "team__name")
    )

    cache.set(cache_key, rows, TOP_SCORER_OPTIONS_CACHE_TTL_SECONDS)
    return [SimpleNamespace(id=item[0], name=item[1], team=SimpleNamespace(name=item[2])) for item in rows]


def _hydrate_participant_for_context(pool, participant):
    return (
        PoolParticipant.objects.filter(pk=participant.pk)
        .select_related("pool", "projection_recalc")
        .prefetch_related(
            Prefetch(
                "bets",
                queryset=PoolBet.objects.filter(match__season=pool.season)
                .select_related("match", "winner_pred")
                .order_by("match__match_number"),
            ),
            Prefetch(
                "projected_standings",
                queryset=participant.projected_standings.select_related("group", "team").order_by(
                    "group__name", "position", "team__code"
                ),
            ),
            Prefetch(
                "projected_third_places",
                queryset=participant.projected_third_places.select_related("group", "team").order_by(
                    "position_global", "group__name", "team__code"
                ),
            ),
        )
        .get()
    )


def _projection_is_stale_from_prefetched(bets, projected_standings, projected_third_places):
    latest_group_bet_updated_at = None
    for bet in bets:
        if not bet.is_active:
            continue
        if bet.match and bet.match.group_id is None:
            continue
        if latest_group_bet_updated_at is None or bet.updated_at > latest_group_bet_updated_at:
            latest_group_bet_updated_at = bet.updated_at

    if latest_group_bet_updated_at is None:
        return False

    if not projected_standings or not projected_third_places:
        return True

    latest_standing_updated_at = max(row.updated_at for row in projected_standings)
    latest_third_updated_at = max(row.updated_at for row in projected_third_places)

    return (
        latest_standing_updated_at < latest_group_bet_updated_at
        or latest_third_updated_at < latest_group_bet_updated_at
    )


def _build_projected_groups_from_rows(projected_standings):
    return [
        {
            "group": group,
            "standings": list(rows),
        }
        for group, rows in groupby(projected_standings, key=lambda row: row.group)
    ]


def _build_third_rows_from_rows(projected_third_places):
    return [
        {
            "group": row.group,
            "line": row,
            "score": row.score,
            "position_global": row.position_global,
            "is_qualified": row.is_qualified,
        }
        for row in projected_third_places
    ]


def build_pool_participant_view_context(*, pool, participant, ensure_bets=True):
    participant = _hydrate_participant_for_context(pool=pool, participant=participant)

    matches = list(
        Match.objects.filter(season=pool.season)
        .select_related("stage", "group", "home_team", "away_team")
        .order_by("match_number", "match_date_brasilia")
    )

    participant_can_bet = participant.can_bet()
    preloaded_bets = list(participant.bets.all())

    if ensure_bets:
        bets_by_match_id = _ensure_participant_bets(
            participant=participant,
            matches=matches,
            can_bet=participant_can_bet,
            existing_bets=preloaded_bets,
        )
    else:
        bets_by_match_id = {bet.match_id: bet for bet in preloaded_bets}

    projected_standings = list(participant.projected_standings.all())
    projected_third_places = list(participant.projected_third_places.all())

    if _projection_is_stale_from_prefetched(
        bets=list(bets_by_match_id.values()),
        projected_standings=projected_standings,
        projected_third_places=projected_third_places,
    ):
        enqueue_projection_recalc(participant)

    projected_groups = _build_projected_groups_from_rows(projected_standings)
    third_rows = _build_third_rows_from_rows(projected_third_places)

    projected = build_projected_placeholder_map(projected_groups=projected_groups, third_rows=third_rows)
    qualified_groups = sorted([row["group"].name for row in third_rows if row["is_qualified"]])
    assign_third_map = load_assign_third_map(season=pool.season, qualified_groups=qualified_groups)
    winners_map = _build_winners_map(matches=matches, bets_by_match_id=bets_by_match_id)
    losers_map = {}

    match_rows = []
    group_rows = []
    knockout_rows = []
    for match in matches:
        phase = phase_for_match(match)
        home_team = match.home_team
        away_team = match.away_team
        bet = bets_by_match_id.get(match.id)

        if phase == PHASE_KNOCKOUT:
            if home_team is None:
                home_team = _resolve_match_team_from_placeholder(
                    placeholder=match.home_placeholder,
                    projected_slots=projected,
                    assign_third_map=assign_third_map,
                    winners_map=winners_map,
                    losers_map=losers_map,
                )
            if away_team is None:
                away_team = _resolve_match_team_from_placeholder(
                    placeholder=match.away_placeholder,
                    projected_slots=projected,
                    assign_third_map=assign_third_map,
                    winners_map=winners_map,
                    losers_map=losers_map,
                )

        row = {
            "match": match,
            "phase": phase,
            "home_team": home_team,
            "away_team": away_team,
            "bet": bet,
            "locked": pool.is_phase_locked(phase),
        }
        match_rows.append(row)

        if phase == PHASE_GROUP:
            group_rows.append(row)
        else:
            knockout_rows.append(row)

        advancing_team = _infer_advancing_team(
            match=match,
            bet=bet,
            home_team=home_team,
            away_team=away_team,
        )
        if advancing_team is not None:
            winners_map[match.match_number] = advancing_team
            losing_team = _infer_losing_team(
                winner_team=advancing_team,
                home_team=home_team,
                away_team=away_team,
            )
            if losing_team is not None:
                losers_map[match.match_number] = losing_team

    projected_knockout = _build_projected_knockout_payload(knockout_rows=knockout_rows)

    return {
        "match_rows": match_rows,
        "group_rows": group_rows,
        "knockout_rows": knockout_rows,
        "projected_groups": projected_groups,
        "can_bet": participant_can_bet,
        "group_locked": pool.is_phase_locked(PHASE_GROUP),
        "knockout_locked": pool.is_phase_locked(PHASE_KNOCKOUT),
        "projection_pending": has_pending_projection_recalc(participant),
        "top_scorer_options": _top_scorer_options_payload_for_pool(pool),
        "page_mode": "result",
        **projected_knockout,
    }

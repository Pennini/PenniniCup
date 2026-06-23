"""Per-match guesses view: pick the default game (live → next → last played),
list the season's games for the selector, and build the rows of every eligible
participant's guess for one game — revealed only after that game's phase locks.
"""

from datetime import timedelta

from django.utils import timezone

from src.football.models import Match
from src.pool.models import PoolBet
from src.pool.services.rules import normalize_stage_key, phase_for_match
from src.rankings.services.divisions import build_divisions
from src.rankings.services.leaderboard import build_pool_leaderboard

# A game counts as "live" while inside this window after kickoff.
LIVE_WINDOW = timedelta(hours=2)

_GROUP_LABEL = "Fase de Grupos"
_KNOCKOUT_STAGE_LABELS = {
    "R32": "32 Avos de Final",
    "R16": "Oitavas de Final",
    "QF": "Quartas de Final",
    "SF": "Semifinal",
    "FINAL": "Final",
    "THIRD": "3º Lugar",
}

_MATCH_RELATIONS = ("stage", "group", "home_team", "away_team")


def resolve_default_match(season, now=None):
    """Game pre-selected when the user opens the tab: a live game (within
    LIVE_WINDOW after kickoff), else the next upcoming game, else — once the
    tournament is over — the most recent game so the tab is never empty.
    """
    now = now or timezone.now()
    base = Match.objects.filter(season=season).select_related(*_MATCH_RELATIONS)

    live = (
        base.filter(match_date_brasilia__lte=now, match_date_brasilia__gt=now - LIVE_WINDOW)
        .order_by("-match_date_brasilia")
        .first()
    )
    if live:
        return live

    upcoming = base.filter(match_date_brasilia__gt=now).order_by("match_date_brasilia").first()
    if upcoming:
        return upcoming

    return base.order_by("-match_date_brasilia").first()


def stage_label(match):
    key = normalize_stage_key(match.stage)
    if key == "GROUP":
        return _GROUP_LABEL
    return _KNOCKOUT_STAGE_LABELS.get(key, match.stage.name)


def get_selectable_matches(season):
    """All season games in selector/carousel order (chronological, then match
    number as tiebreak). Returned as a list so the same fetch feeds both the
    <optgroup> selector and the prev/next carousel navigation.
    """
    return list(
        Match.objects.filter(season=season)
        .select_related(*_MATCH_RELATIONS)
        .order_by("match_date_brasilia", "match_number")
    )


def group_matches_by_phase(matches):
    """Bucket an ordered match list into <optgroup>s by phase label, preserving
    the incoming chronological order both across and within buckets.
    """
    groups = []
    index_by_label = {}
    for match in matches:
        label = stage_label(match)
        if label not in index_by_label:
            index_by_label[label] = len(groups)
            groups.append({"label": label, "matches": []})
        groups[index_by_label[label]]["matches"].append(match)
    return groups


def resolve_adjacent(matches, selected):
    """Previous/next game around `selected` within the ordered `matches` list,
    for the carousel arrows. Returns (None, None) when there is no selection or
    the selection is not in the list; None on either side marks an endpoint.
    """
    if selected is None:
        return None, None
    ids = [match.id for match in matches]
    try:
        index = ids.index(selected.id)
    except ValueError:
        return None, None
    prev_match = matches[index - 1] if index > 0 else None
    next_match = matches[index + 1] if index < len(matches) - 1 else None
    return prev_match, next_match


def resolve_selected_match(request, season):
    """The game chosen via ?match=<id> (validated against the pool's season),
    falling back to the default game for missing/invalid ids.
    """
    raw_id = request.GET.get("match")
    try:
        match_id = int(raw_id) if raw_id else None
    except (TypeError, ValueError):
        match_id = None

    if match_id is not None:
        match = Match.objects.filter(season=season, pk=match_id).select_related(*_MATCH_RELATIONS).first()
        if match:
            return match

    return resolve_default_match(season)


def _build_guess_rows(pool, match):
    """Rows in the same order as the ranking, each carrying the participant's
    leaderboard position (so the guesses table mirrors the ranking, #position
    and all). Reuses build_pool_leaderboard, the single source of order/eligibility.
    """
    ranking_rows = build_pool_leaderboard(pool)
    bets = {
        bet.participant_id: bet
        for bet in PoolBet.objects.filter(participant__pool=pool, match=match, is_active=True).select_related(
            "winner_pred", "score"
        )
    }
    return [
        {"position": row.position, "participant": row.participant, "bet": bets.get(row.participant.id)}
        for row in ranking_rows
    ]


def build_guess_aggregates(guess_rows):
    """Same guesses, grouped by scoreline for the "por palpite" view. Consumes
    the ranking-ordered guess_rows, so rows inside each group keep ranking order
    (and their #position) for free. Groups are sorted most-guessed first, ties
    broken by scoreline (home desc, away desc); the "sem palpite" group (rows
    without a bet) is always last, whatever its size.
    """
    groups = {}
    no_guess_rows = []
    for row in guess_rows:
        bet = row["bet"]
        if bet is None:
            no_guess_rows.append(row)
            continue
        key = (bet.home_score_pred, bet.away_score_pred)
        group = groups.get(key)
        if group is None:
            group = {
                "label": f"{bet.home_score_pred} x {bet.away_score_pred}",
                "home": bet.home_score_pred,
                "away": bet.away_score_pred,
                "count": 0,
                "is_no_guess": False,
                "rows": [],
            }
            groups[key] = group
        group["rows"].append(row)
        group["count"] += 1

    def sort_key(group):
        home = group["home"] if group["home"] is not None else -1
        away = group["away"] if group["away"] is not None else -1
        return (-group["count"], -home, -away)

    aggregates = sorted(groups.values(), key=sort_key)
    if no_guess_rows:
        aggregates.append(
            {
                "label": None,
                "home": None,
                "away": None,
                "count": len(no_guess_rows),
                "is_no_guess": True,
                "rows": no_guess_rows,
            }
        )
    return aggregates


def build_match_guesses_context(*, pool, request):
    matches = get_selectable_matches(pool.season)
    selected_match = resolve_selected_match(request, pool.season)
    prev_match, next_match = resolve_adjacent(matches, selected_match)
    context = {
        "selectable_match_groups": group_matches_by_phase(matches),
        "selected_match": selected_match,
        "prev_match": prev_match,
        "next_match": next_match,
        "selected_stage_label": None,
        "guesses_locked": False,
        "match_finished": False,
        "guess_rows": [],
        "guess_aggregates": [],
        "guess_divisions": [],
    }
    if selected_match is None:
        return context

    context["selected_stage_label"] = stage_label(selected_match)
    # Reveal the real result + points once both scores are known, even if the
    # match status hasn't flipped to FINISHED yet (e.g. the FIFA sync lagged).
    has_scores = selected_match.home_score is not None and selected_match.away_score is not None
    context["match_finished"] = selected_match.status == Match.STATUS_FINISHED or has_scores

    revealed = pool.is_phase_locked(phase_for_match(selected_match))
    context["guesses_locked"] = not revealed
    if revealed:
        context["guess_rows"] = _build_guess_rows(pool, selected_match)
        context["guess_aggregates"] = build_guess_aggregates(context["guess_rows"])
        context["guess_divisions"] = build_divisions(
            context["guess_rows"], position_getter=lambda row: row["position"]
        )

    return context

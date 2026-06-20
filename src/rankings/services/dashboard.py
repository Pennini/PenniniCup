"""Aggregation layer for the pool overview dashboard.

`build_dashboard_data` returns a JSON-serializable dict with everything the
dashboard page needs: championship progress, the logged user's KPIs, the
ranking-evolution series, the per-participant utilization ("aproveitamento")
and the Hall of Fame highlights. All heavy lifting happens here so the frontend
only draws — the view serves this dict as JSON.

Reuses the single sources of truth already in the codebase: `build_pool_leaderboard`
(order + eligibility), `PoolRankingHistory` (per-match snapshots), `resolve_default_match`
(live → next → last) and `phase_for_match` (group vs knockout).
"""

from django.db.models import Count, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

from src.football.models import Match
from src.pool.models import PoolBetScore
from src.pool.services.rules import PHASE_GROUP, phase_for_match
from src.rankings.models import PoolRankingHistory
from src.rankings.services.leaderboard import build_pool_leaderboard
from src.rankings.services.match_guesses import resolve_default_match, stage_label

# Top N caps requested by the spec.
EVOLUTION_TOP_N = 10
UTILIZATION_TOP_N = 10

# Default per-match maximums when a pool has no custom PoolScoringConfig row.
_DEFAULT_GROUP_MAX = 25
_DEFAULT_KNOCKOUT_MAX = 35


def build_dashboard_data(*, pool, participant):
    """Dashboard payload for a logged participant.

    Reads the cached pool-wide aggregate (`PoolDashboardSnapshot`) and overlays
    the cheap per-participant bits live. On a cache miss (brand-new pool, fresh
    deploy) it computes the heavy part once, stores it, then returns — the next
    visitor hits the cache. The signal/worker keeps the cache fresh after that.
    """
    payload = _get_or_build_pool_payload(pool)
    return _overlay_participant(pool, participant, payload)


def build_dashboard_pool_payload(*, pool):
    """Pool-wide (participant-independent) part of the dashboard.

    Everything here is identical for every participant, so it is computed once
    and cached. Per-participant data (KPIs, current-user flags) is added later in
    `_overlay_participant`. Maps are keyed by participant id; after a JSON round
    trip those keys come back as strings, so `_normalize_payload` re-ints them.
    """
    leaderboard = build_pool_leaderboard(pool)
    username_by_id = {row.participant.id: row.participant.user.username for row in leaderboard}
    eligible_ids = set(username_by_id)

    finished_matches = _finished_matches(pool.season)
    finished_ids = [match.id for match in finished_matches]
    max_points_by_id, denominator = _utilization_inputs(pool, finished_matches)

    selected_ids = [row.participant.id for row in leaderboard[:EVOLUTION_TOP_N]]

    return {
        "leader_points": leaderboard[0].participant.total_points if leaderboard else 0,
        "denominator": denominator,
        "positions": {row.participant.id: row.position for row in leaderboard},
        "username_by_id": username_by_id,
        "max_points_by_id": max_points_by_id,
        "selected_ids": selected_ids,
        "evolution_series": _evolution_series(pool, selected_ids, username_by_id),
        "utilization_rows": _utilization_rows(leaderboard, max_points_by_id, denominator),
        "hall_of_fame": _hall_of_fame(pool, eligible_ids, username_by_id, leaderboard, finished_ids),
    }


def _get_or_build_pool_payload(pool):
    from src.rankings.models import PoolDashboardSnapshot

    snapshot = PoolDashboardSnapshot.objects.filter(pool=pool).first()
    if snapshot is not None:
        return _normalize_payload(snapshot.payload)

    payload = build_dashboard_pool_payload(pool=pool)
    PoolDashboardSnapshot.objects.update_or_create(pool=pool, defaults={"payload": payload})
    return payload


def _normalize_payload(payload):
    """Re-key the id-keyed maps to ints after a JSON round trip."""
    return {
        **payload,
        "positions": {int(k): v for k, v in payload.get("positions", {}).items()},
        "username_by_id": {int(k): v for k, v in payload.get("username_by_id", {}).items()},
        "max_points_by_id": {int(k): v for k, v in payload.get("max_points_by_id", {}).items()},
    }


def _overlay_participant(pool, participant, payload):
    return {
        "progress": _progress(pool),
        "kpis": _kpis_from_payload(payload, participant),
        "evolution": _evolution_overlay(pool, payload, participant),
        "utilization": _utilization_overlay(payload, participant),
        "hall_of_fame": payload["hall_of_fame"],
    }


def _finished_matches(season):
    """Games that already have a result. We treat a game as finished when its
    status flipped to FINISHED *or* both scores are filled — mirroring the
    per-match guesses view, which reveals results as soon as scores land even if
    the FIFA sync hasn't flipped the status yet.
    """
    return [
        match
        for match in Match.objects.filter(season=season).select_related("stage")
        if match.status == Match.STATUS_FINISHED or (match.home_score is not None and match.away_score is not None)
    ]


def _match_max_points(match, scoring_config):
    if phase_for_match(match) == PHASE_GROUP:
        return scoring_config.group_exact_score if scoring_config else _DEFAULT_GROUP_MAX
    return scoring_config.knockout_exact_and_advancing if scoring_config else _DEFAULT_KNOCKOUT_MAX


def _utilization_inputs(pool, finished_matches):
    """(points_obtained_by_participant_id, shared_denominator).

    Denominator = sum of the best achievable points per finished game (same for
    everyone, so the comparison is fair). Numerator = sum of each participant's
    per-game points on those games — season-long bonuses (champion, top scorer,
    qualifiers) are excluded from both sides on purpose.
    """
    scoring_config = getattr(pool, "scoring_config", None)
    denominator = sum(_match_max_points(match, scoring_config) for match in finished_matches)

    finished_ids = [match.id for match in finished_matches]
    points_by_id = {}
    if finished_ids:
        points_by_id = {
            row["bet__participant_id"]: row["total"] or 0
            for row in PoolBetScore.objects.filter(
                bet__participant__pool=pool,
                bet__match_id__in=finished_ids,
            )
            .values("bet__participant_id")
            .annotate(total=Sum("points"))
        }
    return points_by_id, denominator


def _utilization_pct(points, denominator):
    if not denominator:
        return 0.0
    return round(points / denominator * 100, 1)


def _progress(pool):
    season = pool.season
    total = Match.objects.filter(season=season).count()
    finished = Match.objects.filter(season=season, status=Match.STATUS_FINISHED).count()
    percent = round(finished / total * 100, 1) if total else 0.0

    default_match = resolve_default_match(season)
    next_match = None
    current_phase_label = None
    if default_match is not None:
        current_phase_label = stage_label(default_match)
        next_match = {
            "label": _match_teams_label(default_match),
            "stage": current_phase_label,
            "kickoff": _isoformat(default_match.match_date_brasilia),
        }

    return {
        "total_matches": total,
        "finished_matches": finished,
        "percent": percent,
        "current_phase": current_phase_label,
        "next_match": next_match,
    }


def _kpis_from_payload(payload, participant):
    position = payload["positions"].get(participant.id)
    leader_points = payload["leader_points"]
    gap = max(leader_points - participant.total_points, 0)
    user_pct = _utilization_pct(payload["max_points_by_id"].get(participant.id, 0), payload["denominator"])

    return {
        "position": position,
        "points": participant.total_points,
        "gap_to_leader": gap,
        "is_leader": position == 1,
        "utilization": user_pct,
    }


def _series_for_ids(pool, ids, username_by_id):
    """Per-round ranking trajectory (no current-user flag) for the given ids."""
    series_by_id = {pid: [] for pid in ids}
    history = (
        PoolRankingHistory.objects.filter(pool=pool, participant_id__in=ids)
        .values("participant_id", "round_index", "position", "total_points")
        .order_by("round_index")
    )
    for snapshot in history:
        series_by_id[snapshot["participant_id"]].append(
            {
                "round": snapshot["round_index"],
                "position": snapshot["position"],
                "points": snapshot["total_points"],
            }
        )
    return [
        {
            "participant_id": pid,
            "label": username_by_id.get(pid, ""),
            "points": series_by_id[pid],
        }
        for pid in ids
        if series_by_id[pid]
    ]


def _evolution_series(pool, selected_ids, username_by_id):
    """Pool-wide trajectories for the top N current participants (cacheable)."""
    return _series_for_ids(pool, selected_ids, username_by_id)


def _evolution_overlay(pool, payload, participant):
    """Top N cached series + the logged user's own series when outside the top N,
    flagging which series is the current user. Mirrors the original behaviour.
    """
    selected_ids = payload["selected_ids"]
    series = [
        {**row, "is_current_user": row["participant_id"] == participant.id} for row in payload["evolution_series"]
    ]
    if participant.id in payload["username_by_id"] and participant.id not in selected_ids:
        own = _series_for_ids(pool, [participant.id], payload["username_by_id"])
        series.extend({**row, "is_current_user": True} for row in own)
    return {"series": series}


def _utilization_rows(leaderboard, max_points_by_id, denominator):
    rows = [
        {
            "participant_id": row.participant.id,
            "label": row.participant.user.username,
            "percent": _utilization_pct(max_points_by_id.get(row.participant.id, 0), denominator),
        }
        for row in leaderboard
    ]
    rows.sort(key=lambda item: item["percent"], reverse=True)
    return {"has_data": bool(denominator), "rows": rows[:UTILIZATION_TOP_N]}


def _utilization_overlay(payload, participant):
    util = payload["utilization_rows"]
    rows = [{**row, "is_current_user": row["participant_id"] == participant.id} for row in util["rows"]]
    return {"has_data": util["has_data"], "rows": rows}


def _hall_of_fame(pool, eligible_ids, username_by_id, leaderboard, finished_ids):
    return {
        "exact_scores": _king_of_scores(eligible_ids, username_by_id),
        "biggest_climb": _biggest_climb(pool, eligible_ids, username_by_id),
        "longest_streak": _longest_streak(pool, eligible_ids, username_by_id),
        "best_day": _best_day(pool, eligible_ids, username_by_id),
        "pe_frio": _pe_frio(eligible_ids, username_by_id, finished_ids),
        "lanterna": _lanterna(leaderboard, username_by_id),
        "maior_queda": _maior_queda(pool, eligible_ids, username_by_id),
        "ioio": _ioio(pool, eligible_ids, username_by_id),
    }


def _entry(username, value, *, extra=None):
    entry = {"username": username, "value": value}
    if extra:
        entry.update(extra)
    return entry


def _king_of_scores(eligible_ids, username_by_id):
    from src.pool.models import PoolParticipant

    best = (
        PoolParticipant.objects.filter(id__in=eligible_ids, exact_score_hits__gt=0)
        .order_by("-exact_score_hits", "joined_at")
        .values("id", "exact_score_hits")
        .first()
    )
    if not best:
        return None
    return _entry(username_by_id.get(best["id"], ""), best["exact_score_hits"])


def _biggest_climb(pool, eligible_ids, username_by_id):
    """Largest gain of positions between any earlier and later snapshot, per
    participant. For each snapshot the climb is (worst position seen so far) -
    (current position); we keep the global best across all participants.
    """
    history = (
        PoolRankingHistory.objects.filter(pool=pool, participant_id__in=eligible_ids)
        .values("participant_id", "round_index", "position")
        .order_by("participant_id", "round_index")
    )
    best_id = None
    best_climb = 0
    worst_seen = {}
    for snapshot in history:
        pid = snapshot["participant_id"]
        position = snapshot["position"]
        prev_worst = worst_seen.get(pid)
        if prev_worst is not None:
            climb = prev_worst - position
            if climb > best_climb:
                best_climb = climb
                best_id = pid
        worst_seen[pid] = position if prev_worst is None else max(prev_worst, position)

    if best_id is None or best_climb <= 0:
        return None
    return _entry(username_by_id.get(best_id, ""), best_climb)


def _longest_streak(pool, eligible_ids, username_by_id):
    """Longest run of consecutive games (chronological) where the participant
    scored more than zero points.
    """
    rows = (
        PoolBetScore.objects.filter(bet__participant_id__in=eligible_ids)
        .values("bet__participant_id", "points")
        .order_by("bet__participant_id", "bet__match__match_date_brasilia", "bet__match__match_number")
    )
    best_id = None
    best_streak = 0
    current_id = None
    current_streak = 0
    for row in rows:
        pid = row["bet__participant_id"]
        if pid != current_id:
            current_id = pid
            current_streak = 0
        if row["points"] and row["points"] > 0:
            current_streak += 1
        else:
            current_streak = 0
        if current_streak > best_streak:
            best_streak = current_streak
            best_id = pid

    if best_id is None or best_streak <= 0:
        return None
    return _entry(username_by_id.get(best_id, ""), best_streak)


def _best_day(pool, eligible_ids, username_by_id):
    """Highest points a participant scored on a single calendar day (Brasília)."""
    best = (
        PoolBetScore.objects.filter(bet__participant_id__in=eligible_ids, points__gt=0)
        .annotate(day=TruncDate("bet__match__match_date_brasilia"))
        .values("bet__participant_id", "day")
        .annotate(total=Sum("points"))
        .order_by("-total")
        .first()
    )
    if not best:
        return None
    return _entry(
        username_by_id.get(best["bet__participant_id"], ""),
        best["total"],
        extra={"day": best["day"].isoformat() if best["day"] else None},
    )


def _pe_frio(eligible_ids, username_by_id, finished_ids):
    """Maior número de jogos zerados (pontos <= 0) entre os finalizados. Considera
    só palpites efetivamente registrados — ausência de palpite não conta.
    """
    if not finished_ids:
        return None
    best = (
        PoolBetScore.objects.filter(
            bet__participant_id__in=eligible_ids,
            bet__match_id__in=finished_ids,
            points__lte=0,
        )
        .values("bet__participant_id")
        .annotate(total=Count("id"))
        .order_by("-total", "bet__participant__joined_at")
        .first()
    )
    if not best or not best["total"]:
        return None
    return _entry(username_by_id.get(best["bet__participant_id"], ""), best["total"])


def _lanterna(leaderboard, username_by_id):
    """Último colocado atual do ranking. Só faz sentido com mais de um participante."""
    if len(leaderboard) <= 1:
        return None
    last = leaderboard[-1]
    return _entry(username_by_id.get(last.participant.id, ""), last.position)


def _maior_queda(pool, eligible_ids, username_by_id):
    """Maior queda de posições: para cada snapshot, (posição atual) - (melhor posição
    vista até então); mantém o máximo global. Espelha `_biggest_climb` invertido.
    """
    history = (
        PoolRankingHistory.objects.filter(pool=pool, participant_id__in=eligible_ids)
        .values("participant_id", "round_index", "position")
        .order_by("participant_id", "round_index")
    )
    best_id = None
    best_drop = 0
    best_seen = {}
    for snapshot in history:
        pid = snapshot["participant_id"]
        position = snapshot["position"]
        prev_best = best_seen.get(pid)
        if prev_best is not None:
            drop = position - prev_best
            if drop > best_drop:
                best_drop = drop
                best_id = pid
        best_seen[pid] = position if prev_best is None else min(prev_best, position)

    if best_id is None or best_drop <= 0:
        return None
    return _entry(username_by_id.get(best_id, ""), best_drop)


def _ioio(pool, eligible_ids, username_by_id):
    """Maior 'balanço' no ranking: soma das variações absolutas de posição entre
    rodadas consecutivas. Quem mais subiu e desceu o campeonato inteiro.
    """
    history = (
        PoolRankingHistory.objects.filter(pool=pool, participant_id__in=eligible_ids)
        .values("participant_id", "round_index", "position")
        .order_by("participant_id", "round_index")
    )
    churn = {}
    prev = {}
    for snapshot in history:
        pid = snapshot["participant_id"]
        position = snapshot["position"]
        if pid in prev:
            churn[pid] = churn.get(pid, 0) + abs(position - prev[pid])
        prev[pid] = position

    if not churn:
        return None
    # Maior churn; empate -> menor participant_id (entrou antes).
    best_id = max(churn, key=lambda pid: (churn[pid], -pid))
    if churn[best_id] <= 0:
        return None
    return _entry(username_by_id.get(best_id, ""), churn[best_id])


def _match_teams_label(match):
    home = match.home_team.name if match.home_team else (match.home_placeholder or "A definir")
    away = match.away_team.name if match.away_team else (match.away_placeholder or "A definir")
    return f"{home} x {away}"


def _isoformat(value):
    if value is None:
        return None
    return timezone.localtime(value).isoformat()

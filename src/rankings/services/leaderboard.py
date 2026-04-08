from dataclasses import dataclass
from itertools import groupby

from src.pool.models import Pool, PoolParticipant
from src.rankings.models import RankingTieBreakOverride


@dataclass(frozen=True)
class RankingRow:
    position: int
    participant: PoolParticipant
    is_tied: bool
    tie_resolved_manually: bool


def _score_key(participant):
    return (
        participant.total_points,
        participant.champion_hit,
        participant.exact_score_hits,
        participant.top_scorer_hit,
        participant.winner_or_draw_hits,
        participant.knockout_points,
        participant.group_points,
    )


def _natural_key(participant):
    return (participant.joined_at, participant.user_id)


def _sort_tie_group(group_participants, override_map):
    group_ids = {participant.id for participant in group_participants}
    local_override_map = {
        participant_id: manual_position
        for participant_id, manual_position in override_map.items()
        if participant_id in group_ids
    }

    manual_rows = [participant for participant in group_participants if participant.id in local_override_map]
    manual_rows.sort(key=lambda participant: (local_override_map[participant.id],) + _natural_key(participant))

    natural_rows = [participant for participant in group_participants if participant.id not in local_override_map]
    natural_rows.sort(key=_natural_key)

    return manual_rows + natural_rows, bool(local_override_map)


def build_pool_leaderboard(pool: Pool):
    participants = list(
        PoolParticipant.objects.filter(pool=pool, is_active=True)
        .select_related("user")
        .order_by(
            "-total_points",
            "-champion_hit",
            "-exact_score_hits",
            "-top_scorer_hit",
            "-winner_or_draw_hits",
            "-knockout_points",
            "-group_points",
            "joined_at",
            "user_id",
        )
    )

    override_map = {
        row["participant_id"]: row["manual_position"]
        for row in RankingTieBreakOverride.objects.filter(pool=pool).values("participant_id", "manual_position")
    }

    ordered_participants = []
    manual_resolution_ids = set()
    tie_counts = {}
    for _, tie_group_iter in groupby(participants, key=_score_key):
        tie_group = list(tie_group_iter)
        if tie_group:
            tie_counts[_score_key(tie_group[0])] = len(tie_group)
        sorted_group, has_manual_resolution = _sort_tie_group(tie_group, override_map)
        ordered_participants.extend(sorted_group)
        if has_manual_resolution:
            manual_resolution_ids.update(participant.id for participant in tie_group if participant.id in override_map)

    rows = []
    for index, participant in enumerate(ordered_participants, start=1):
        score_key = _score_key(participant)
        rows.append(
            RankingRow(
                position=index,
                participant=participant,
                is_tied=tie_counts.get(score_key, 1) > 1,
                tie_resolved_manually=participant.id in manual_resolution_ids,
            )
        )

    return rows

from django.db import transaction

from src.pool.models import Pool, PoolBetScore, PoolParticipant
from src.pool.services.rules import PHASE_GROUP, phase_for_match
from src.pool.services.scoring import calculate_bet_points


def recalculate_participant_scores(participant):
    total_points = 0
    group_points = 0
    knockout_points = 0
    exact_score_hits = 0
    winner_or_draw_hits = 0

    bets = participant.bets.select_related("match", "match__stage").all()
    for bet in bets:
        score_data = calculate_bet_points(bet)

        PoolBetScore.objects.update_or_create(
            bet=bet,
            defaults={
                "points": score_data["points"],
                "exact_score": score_data["exact_score"],
                "winner_or_draw": score_data["winner_or_draw"],
                "winner_advancing": score_data["winner_advancing"],
                "one_team_score": score_data["one_team_score"],
            },
        )

        total_points += score_data["points"]
        if phase_for_match(bet.match) == PHASE_GROUP:
            group_points += score_data["points"]
        else:
            knockout_points += score_data["points"]

        if score_data["exact_score"]:
            exact_score_hits += 1
        if score_data["winner_or_draw"]:
            winner_or_draw_hits += 1

    participant.total_points = total_points
    participant.group_points = group_points
    participant.knockout_points = knockout_points
    participant.exact_score_hits = exact_score_hits
    participant.winner_or_draw_hits = winner_or_draw_hits
    participant.save(
        update_fields=[
            "total_points",
            "group_points",
            "knockout_points",
            "exact_score_hits",
            "winner_or_draw_hits",
        ]
    )


@transaction.atomic
def recalculate_pool_scores(pool):
    participants = PoolParticipant.objects.filter(pool=pool, is_active=True).all()
    for participant in participants:
        recalculate_participant_scores(participant)


def recalculate_all_pools(season=None):
    pools = Pool.objects.filter(is_active=True)
    if season is not None:
        pools = pools.filter(season=season)

    for pool in pools:
        recalculate_pool_scores(pool)


def recalculate_match_scores(match):
    participants = (
        PoolParticipant.objects.filter(pool__season=match.season, is_active=True, bets__match=match).distinct().all()
    )
    for participant in participants:
        recalculate_participant_scores(participant)

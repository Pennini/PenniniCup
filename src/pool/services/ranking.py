from django.db import transaction

from src.pool.models import Pool, PoolBetScore, PoolParticipant
from src.pool.services.rules import PHASE_GROUP, phase_for_match
from src.pool.services.scoring import calculate_bet_points


def _calculate_bonus(participant, scoring_config, official_result):
    bonus_points = 0
    champion_hit = bool(
        participant.champion_pred_id
        and official_result.champion_id
        and participant.champion_pred_id == official_result.champion_id
    )
    runner_up_hit = bool(
        participant.runner_up_pred_id
        and official_result.runner_up_id
        and participant.runner_up_pred_id == official_result.runner_up_id
    )
    third_place_hit = bool(
        participant.third_place_pred_id
        and official_result.third_place_id
        and participant.third_place_pred_id == official_result.third_place_id
    )
    top_scorer_hit = bool(
        participant.top_scorer_pred_id
        and official_result.top_scorer_id
        and participant.top_scorer_pred_id == official_result.top_scorer_id
    )

    if champion_hit:
        bonus_points += scoring_config.bonus_champion_points
    if runner_up_hit:
        bonus_points += scoring_config.bonus_runner_up_points
    if third_place_hit:
        bonus_points += scoring_config.bonus_third_place_points
    if top_scorer_hit:
        bonus_points += scoring_config.bonus_top_scorer_points

    return bonus_points, champion_hit, top_scorer_hit


@transaction.atomic
def recalculate_participant_scores(participant, scoring_config=None, official_result=None):
    scoring_config = scoring_config or participant.pool.get_scoring_config()
    official_result = official_result or participant.pool.get_official_results()

    total_points = 0
    group_points = 0
    knockout_points = 0
    exact_score_hits = 0
    winner_or_draw_hits = 0

    bets = participant.bets.select_related("match", "match__stage").all()
    scores_to_upsert = []

    for bet in bets:
        score_data = calculate_bet_points(bet, scoring_config=scoring_config)
        scores_to_upsert.append(
            PoolBetScore(
                bet=bet,
                points=score_data["points"],
                exact_score=score_data["exact_score"],
                winner_or_draw=score_data["winner_or_draw"],
                winner_advancing=score_data["winner_advancing"],
                one_team_score=score_data["one_team_score"],
            )
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

    if scores_to_upsert:
        PoolBetScore.objects.bulk_create(
            scores_to_upsert,
            update_conflicts=True,
            update_fields=[
                "points",
                "exact_score",
                "winner_or_draw",
                "winner_advancing",
                "one_team_score",
                "updated_at",
            ],
            unique_fields=["bet"],
        )

    bonus_points, champion_hit, top_scorer_hit = _calculate_bonus(
        participant=participant,
        scoring_config=scoring_config,
        official_result=official_result,
    )

    total_points += bonus_points

    participant.total_points = total_points
    participant.group_points = group_points
    participant.knockout_points = knockout_points
    participant.bonus_points = bonus_points
    participant.exact_score_hits = exact_score_hits
    participant.winner_or_draw_hits = winner_or_draw_hits
    participant.champion_hit = champion_hit
    participant.top_scorer_hit = top_scorer_hit
    participant.save(
        update_fields=[
            "total_points",
            "group_points",
            "knockout_points",
            "bonus_points",
            "exact_score_hits",
            "winner_or_draw_hits",
            "champion_hit",
            "top_scorer_hit",
        ]
    )


@transaction.atomic
def recalculate_pool_scores(pool):
    scoring_config = pool.get_scoring_config()
    official_result = pool.get_official_results()
    participants = PoolParticipant.objects.filter(pool=pool, is_active=True).all()
    for participant in participants:
        recalculate_participant_scores(
            participant,
            scoring_config=scoring_config,
            official_result=official_result,
        )


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

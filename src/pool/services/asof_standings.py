from dataclasses import dataclass

from src.pool.models import PoolParticipant
from src.pool.services.rules import PHASE_GROUP, phase_for_match
from src.pool.services.scoring import calculate_bet_points
from src.rankings.services.leaderboard import eligible_participants


@dataclass
class AsOfStanding:
    participant: PoolParticipant
    total_points: int = 0
    group_points: int = 0
    knockout_points: int = 0
    exact_score_hits: int = 0
    advancing_hits: int = 0
    champion_hit: bool = False
    top_scorer_hit: bool = False


def compute_asof_standings(pool, allowed_match_ids, scoring_config, official_result):
    """Standings do bolão considerando só os jogos em allowed_match_ids.

    Não toca o banco: retorna uma lista de AsOfStanding (uma por participante
    elegível). Espelha recalculate_participant_scores, mas restrito ao conjunto
    de jogos permitidos. Bônus são adicionados na Task 2.
    """
    allowed_match_ids = set(allowed_match_ids)
    pool_type = pool.pool_type
    participants = list(eligible_participants(pool).select_related("user"))

    rows = []
    for participant in participants:
        total_points = 0
        group_points = 0
        knockout_points = 0
        exact_score_hits = 0
        advancing_hits = 0

        bets = participant.bets.select_related("match", "match__stage").all()
        for bet in bets:
            if bet.match_id not in allowed_match_ids:
                continue
            score_data = calculate_bet_points(bet, scoring_config=scoring_config, pool_type=pool_type)
            total_points += score_data["points"]
            if phase_for_match(bet.match) == PHASE_GROUP:
                group_points += score_data["points"]
            else:
                knockout_points += score_data["points"]
            if score_data["exact_score"]:
                exact_score_hits += 1
            if score_data["advancing_correct"]:
                advancing_hits += 1

        rows.append(
            AsOfStanding(
                participant=participant,
                total_points=total_points,
                group_points=group_points,
                knockout_points=knockout_points,
                exact_score_hits=exact_score_hits,
                advancing_hits=advancing_hits,
            )
        )

    return rows

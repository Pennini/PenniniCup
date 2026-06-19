from django.db.models import Max

from src.pool.models import Pool
from src.rankings.models import PoolRankingHistory
from src.rankings.services.leaderboard import build_pool_leaderboard

_SNAPSHOT_FIELDS = [
    "round_index",
    "position",
    "total_points",
    "group_points",
    "knockout_points",
    "exact_score_hits",
    "advancing_hits",
    "champion_hit",
    "top_scorer_hit",
]


def snapshot_round_for_match(match):
    """Grava (ou atualiza) o histórico de ranking de uma rodada.

    Uma rodada = um jogo encerrado, isto é, um Match que já possui placar
    (home_score e away_score não nulos). Para cada bolão afetado (ativo, da
    season do jogo, com participante que apostou nesse jogo) grava uma linha por
    participante com a posição e os dados de ranking pós-recálculo. Re-chamar para
    o mesmo match (correção de placar) atualiza as linhas mantendo o round_index.
    """
    if match.home_score is None or match.away_score is None:
        return

    affected_pools = Pool.objects.filter(
        season=match.season,
        is_active=True,
        participants__bets__match=match,
    ).distinct()

    for pool in affected_pools:
        existing_round = (
            PoolRankingHistory.objects.filter(pool=pool, match=match).values_list("round_index", flat=True).first()
        )
        if existing_round is not None:
            round_index = existing_round
        else:
            max_round = PoolRankingHistory.objects.filter(pool=pool).aggregate(value=Max("round_index"))["value"]
            round_index = (max_round or 0) + 1

        history_rows = [
            PoolRankingHistory(
                pool=pool,
                participant=row.participant,
                match=match,
                round_index=round_index,
                position=row.position,
                total_points=row.participant.total_points,
                group_points=row.participant.group_points,
                knockout_points=row.participant.knockout_points,
                exact_score_hits=row.participant.exact_score_hits,
                advancing_hits=row.participant.advancing_hits,
                champion_hit=row.participant.champion_hit,
                top_scorer_hit=row.participant.top_scorer_hit,
            )
            for row in build_pool_leaderboard(pool=pool)
        ]
        if history_rows:
            PoolRankingHistory.objects.bulk_create(
                history_rows,
                update_conflicts=True,
                unique_fields=["pool", "participant", "match"],
                update_fields=_SNAPSHOT_FIELDS,
            )

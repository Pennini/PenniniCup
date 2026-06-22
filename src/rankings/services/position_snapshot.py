from src.pool.models import Pool
from src.rankings.services.history_backfill import backfill_pool_history


def snapshot_round_for_match(match):
    """Reconstrói o histórico de ranking dos bolões afetados por um jogo encerrado.

    Antes este caminho carimbava os agregados *atuais* da temporada em cada rodada,
    o que corrompia o histórico em correções de placar, bônus de fim de torneio e
    processamento fora de ordem. Agora delega ao motor as-of (`backfill_pool_history`),
    idempotente e cronológico — um único caminho correto. Retorna os bolões afetados
    para a fila enfileirar o rebuild da dashboard de cada um.
    """
    if match.home_score is None or match.away_score is None:
        return []

    affected_pools = list(
        Pool.objects.filter(
            season=match.season,
            is_active=True,
            participants__bets__match=match,
        ).distinct()
    )

    for pool in affected_pools:
        backfill_pool_history(pool)

    return affected_pools

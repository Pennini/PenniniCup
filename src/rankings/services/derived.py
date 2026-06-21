from src.rankings.services.dashboard_queue import enqueue_dashboard_snapshot
from src.rankings.services.history_backfill import backfill_pool_history


def refresh_pool_derived_data(pool):
    """Atualiza os dados derivados de um bolão após os agregados mudarem.

    Ponto de entrada único para o caminho de recálculo (sync e comandos), onde o
    `bulk_create` de placares não dispara `post_save`. Reconstrói o histórico
    as-of e *depois* enfileira o rebuild do payload pesado da dashboard — nessa
    ordem, pois a dashboard lê `PoolRankingHistory`.
    """
    backfill_pool_history(pool)
    enqueue_dashboard_snapshot(pool)

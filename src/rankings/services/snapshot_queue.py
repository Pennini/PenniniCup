import logging

from django.db import transaction
from django.db.utils import NotSupportedError
from django.utils import timezone

from src.rankings.models import PoolRankingSnapshotJob
from src.rankings.services.dashboard_queue import enqueue_dashboard_snapshot
from src.rankings.services.position_snapshot import snapshot_round_for_match

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 5
PROCESSING_TIMEOUT_MINUTES = 30


def _recover_stale_processing_jobs():
    """Recoloca em PENDING jobs presos em PROCESSING após crash do worker."""
    stale_cutoff = timezone.now() - timezone.timedelta(minutes=PROCESSING_TIMEOUT_MINUTES)
    PoolRankingSnapshotJob.objects.filter(
        status=PoolRankingSnapshotJob.STATUS_PROCESSING,
        last_started_at__lt=stale_cutoff,
        attempts__lt=MAX_ATTEMPTS,
    ).update(
        status=PoolRankingSnapshotJob.STATUS_PENDING,
        last_error="Recovered from stale PROCESSING state",
    )


def enqueue_ranking_snapshot(match):
    """Enfileira (ou rearma) o snapshot de ranking de um jogo encerrado.

    Cada save de Match com placar = dado novo: sempre rearma e zera as tentativas,
    inclusive jobs FAILED que estouraram o limite (mesma lógica do enqueue de
    projeção). MAX_ATTEMPTS protege contra loops do worker dentro de um pedido,
    não deve travar o histórico após uma correção de placar.
    """
    now = timezone.now()
    job, created = PoolRankingSnapshotJob.objects.get_or_create(
        match=match,
        defaults={"status": PoolRankingSnapshotJob.STATUS_PENDING, "requested_at": now},
    )
    if not created:
        job.status = PoolRankingSnapshotJob.STATUS_PENDING
        job.requested_at = now
        job.attempts = 0
        job.last_error = ""
        job.save(update_fields=["status", "requested_at", "attempts", "last_error"])
    return job


def process_next_ranking_snapshot_job():
    """Reivindica e processa o próximo snapshot pendente. Retorna o job ou None."""
    _recover_stale_processing_jobs()
    with transaction.atomic():
        PoolRankingSnapshotJob.objects.filter(
            status=PoolRankingSnapshotJob.STATUS_PENDING,
            attempts__gte=MAX_ATTEMPTS,
        ).update(
            status=PoolRankingSnapshotJob.STATUS_FAILED,
            last_finished_at=timezone.now(),
            last_error=f"Max retries reached ({MAX_ATTEMPTS})",
        )

        pending = PoolRankingSnapshotJob.objects.select_related("match").filter(
            status=PoolRankingSnapshotJob.STATUS_PENDING,
            attempts__lt=MAX_ATTEMPTS,
        )
        try:
            job = pending.select_for_update(skip_locked=True).order_by("requested_at").first()
        except NotSupportedError:
            job = pending.select_for_update().order_by("requested_at").first()

        if job is None:
            return None

        job.status = PoolRankingSnapshotJob.STATUS_PROCESSING
        job.last_started_at = timezone.now()
        job.attempts += 1
        job.last_error = ""
        job.save(update_fields=["status", "last_started_at", "attempts", "last_error"])

    try:
        affected_pools = snapshot_round_for_match(job.match)
        # Histórico já gravado: agora rearma a dashboard de cada bolão afetado.
        for pool in affected_pools:
            enqueue_dashboard_snapshot(pool)
        PoolRankingSnapshotJob.objects.filter(id=job.id).update(
            status=PoolRankingSnapshotJob.STATUS_IDLE,
            last_finished_at=timezone.now(),
            last_error="",
        )
    except Exception as exc:  # pragma: no cover
        logger.exception(
            "Erro ao snapshotar rodada: match_id=%s attempts=%s",
            job.match_id,
            job.attempts,
        )
        new_status = (
            PoolRankingSnapshotJob.STATUS_FAILED
            if job.attempts >= MAX_ATTEMPTS
            else PoolRankingSnapshotJob.STATUS_PENDING
        )
        PoolRankingSnapshotJob.objects.filter(id=job.id).update(
            status=new_status,
            last_finished_at=timezone.now(),
            last_error=str(exc),
        )

    return job

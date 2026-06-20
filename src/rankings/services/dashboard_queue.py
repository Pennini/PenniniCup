import logging

from django.db import transaction
from django.db.utils import NotSupportedError
from django.utils import timezone

from src.rankings.models import PoolDashboardSnapshot, PoolDashboardSnapshotJob
from src.rankings.services.dashboard import build_dashboard_pool_payload

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 5
PROCESSING_TIMEOUT_MINUTES = 30


def _recover_stale_processing_jobs():
    """Recoloca em PENDING jobs presos em PROCESSING após crash do worker."""
    stale_cutoff = timezone.now() - timezone.timedelta(minutes=PROCESSING_TIMEOUT_MINUTES)
    PoolDashboardSnapshotJob.objects.filter(
        status=PoolDashboardSnapshotJob.STATUS_PROCESSING,
        last_started_at__lt=stale_cutoff,
        attempts__lt=MAX_ATTEMPTS,
    ).update(
        status=PoolDashboardSnapshotJob.STATUS_PENDING,
        last_error="Recovered from stale PROCESSING state",
    )


def enqueue_dashboard_snapshot(pool):
    """Enfileira (ou rearma) o recálculo da dashboard de um bolão.

    Idempotente por bolão: re-enfileirar reusa a linha, volta para PENDING e zera
    as tentativas (inclusive jobs FAILED). Mesma lógica do enqueue de snapshot de
    ranking — MAX_ATTEMPTS protege contra loops do worker, não trava o histórico.
    """
    now = timezone.now()
    job, created = PoolDashboardSnapshotJob.objects.get_or_create(
        pool=pool,
        defaults={"status": PoolDashboardSnapshotJob.STATUS_PENDING, "requested_at": now},
    )
    if not created:
        job.status = PoolDashboardSnapshotJob.STATUS_PENDING
        job.requested_at = now
        job.attempts = 0
        job.last_error = ""
        job.save(update_fields=["status", "requested_at", "attempts", "last_error"])
    return job


def process_next_dashboard_snapshot_job():
    """Reivindica e processa o próximo recálculo de dashboard pendente."""
    _recover_stale_processing_jobs()
    with transaction.atomic():
        PoolDashboardSnapshotJob.objects.filter(
            status=PoolDashboardSnapshotJob.STATUS_PENDING,
            attempts__gte=MAX_ATTEMPTS,
        ).update(
            status=PoolDashboardSnapshotJob.STATUS_FAILED,
            last_finished_at=timezone.now(),
            last_error=f"Max retries reached ({MAX_ATTEMPTS})",
        )

        pending = PoolDashboardSnapshotJob.objects.select_related("pool").filter(
            status=PoolDashboardSnapshotJob.STATUS_PENDING,
            attempts__lt=MAX_ATTEMPTS,
        )
        try:
            job = pending.select_for_update(skip_locked=True).order_by("requested_at").first()
        except NotSupportedError:
            job = pending.select_for_update().order_by("requested_at").first()

        if job is None:
            return None

        job.status = PoolDashboardSnapshotJob.STATUS_PROCESSING
        job.last_started_at = timezone.now()
        job.attempts += 1
        job.last_error = ""
        job.save(update_fields=["status", "last_started_at", "attempts", "last_error"])

    try:
        payload = build_dashboard_pool_payload(pool=job.pool)
        PoolDashboardSnapshot.objects.update_or_create(pool=job.pool, defaults={"payload": payload})
        PoolDashboardSnapshotJob.objects.filter(id=job.id).update(
            status=PoolDashboardSnapshotJob.STATUS_IDLE,
            last_finished_at=timezone.now(),
            last_error="",
        )
    except Exception as exc:  # pragma: no cover
        logger.exception(
            "Erro ao recalcular dashboard: pool_id=%s attempts=%s",
            job.pool_id,
            job.attempts,
        )
        new_status = (
            PoolDashboardSnapshotJob.STATUS_FAILED
            if job.attempts >= MAX_ATTEMPTS
            else PoolDashboardSnapshotJob.STATUS_PENDING
        )
        PoolDashboardSnapshotJob.objects.filter(id=job.id).update(
            status=new_status,
            last_finished_at=timezone.now(),
            last_error=str(exc),
        )

    return job

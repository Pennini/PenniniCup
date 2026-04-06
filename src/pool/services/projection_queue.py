from django.db import transaction
from django.db.utils import NotSupportedError
from django.utils import timezone

from src.pool.models import PoolProjectionRecalc
from src.pool.services.projection import sync_persisted_group_standings, sync_persisted_third_places

PENDING_STATUSES = {
    PoolProjectionRecalc.STATUS_PENDING,
    PoolProjectionRecalc.STATUS_PROCESSING,
}


def projection_is_stale(participant):
    latest_group_bet_updated_at = (
        participant.bets.filter(match__group__isnull=False, is_active=True)
        .order_by("-updated_at")
        .values_list("updated_at", flat=True)
        .first()
    )
    if latest_group_bet_updated_at is None:
        return False

    latest_standing_updated_at = (
        participant.projected_standings.order_by("-updated_at").values_list("updated_at", flat=True).first()
    )
    latest_third_updated_at = (
        participant.projected_third_places.order_by("-updated_at").values_list("updated_at", flat=True).first()
    )

    if latest_standing_updated_at is None or latest_third_updated_at is None:
        return True

    return (
        latest_standing_updated_at < latest_group_bet_updated_at
        or latest_third_updated_at < latest_group_bet_updated_at
    )


def enqueue_projection_recalc(participant):
    now = timezone.now()
    job, created = PoolProjectionRecalc.objects.get_or_create(
        participant=participant,
        defaults={
            "status": PoolProjectionRecalc.STATUS_PENDING,
            "requested_at": now,
        },
    )

    if not created:
        job.status = PoolProjectionRecalc.STATUS_PENDING
        job.requested_at = now
        job.save(update_fields=["status", "requested_at"])

    return job


def has_pending_projection_recalc(participant):
    job = getattr(participant, "projection_recalc", None)
    if job is None:
        return False
    return job.status in PENDING_STATUSES


def process_next_projection_recalc_job():
    with transaction.atomic():
        pending = PoolProjectionRecalc.objects.select_related("participant").filter(
            status=PoolProjectionRecalc.STATUS_PENDING
        )

        try:
            job = pending.select_for_update(skip_locked=True).order_by("requested_at").first()
        except NotSupportedError:
            job = pending.select_for_update().order_by("requested_at").first()

        if job is None:
            return None

        job.status = PoolProjectionRecalc.STATUS_PROCESSING
        job.last_started_at = timezone.now()
        job.attempts += 1
        job.last_error = ""
        job.save(update_fields=["status", "last_started_at", "attempts", "last_error"])

    participant = job.participant
    try:
        projected_groups = sync_persisted_group_standings(participant=participant)
        sync_persisted_third_places(participant=participant, projected_groups=projected_groups)

        job.status = PoolProjectionRecalc.STATUS_IDLE
        job.last_finished_at = timezone.now()
        job.last_error = ""
        job.save(update_fields=["status", "last_finished_at", "last_error"])
    except Exception as exc:  # pragma: no cover
        job.status = PoolProjectionRecalc.STATUS_FAILED
        job.last_finished_at = timezone.now()
        job.last_error = str(exc)
        job.save(update_fields=["status", "last_finished_at", "last_error"])
        raise

    return job

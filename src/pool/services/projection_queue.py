import logging

from django.db import transaction
from django.db.utils import NotSupportedError
from django.utils import timezone

from src.pool.models import PoolParticipant, PoolProjectionRecalc
from src.pool.services.projection import sync_persisted_group_standings, sync_persisted_third_places

logger = logging.getLogger(__name__)

PENDING_STATUSES = {
    PoolProjectionRecalc.STATUS_PENDING,
    PoolProjectionRecalc.STATUS_PROCESSING,
}
MAX_ATTEMPTS = 5

PROCESSING_TIMEOUT_MINUTES = 30  # mais seguro para cálculos lentos


def _recover_stale_processing_jobs():
    """Recoloca em PENDING jobs presos em PROCESSING após crash.

    Só recupera se há apenas 1 worker rodando (evita reprocessamento duplo).
    """
    stale_cutoff = timezone.now() - timezone.timedelta(minutes=PROCESSING_TIMEOUT_MINUTES)
    PoolProjectionRecalc.objects.filter(
        status=PoolProjectionRecalc.STATUS_PROCESSING,
        last_started_at__lt=stale_cutoff,
        attempts__lt=MAX_ATTEMPTS,
    ).update(
        status=PoolProjectionRecalc.STATUS_PENDING,
        last_error="Recovered from stale PROCESSING state",
    )


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

    # Não rearma jobs que já excederam o limite de tentativas.
    if not created and not (job.status == PoolProjectionRecalc.STATUS_FAILED and job.attempts >= MAX_ATTEMPTS):
        job.status = PoolProjectionRecalc.STATUS_PENDING
        job.requested_at = now
        job.save(update_fields=["status", "requested_at"])

    return job


def enqueue_projection_recalc_for_season(season):
    now = timezone.now()
    participants = list(
        PoolParticipant.objects.filter(
            pool__season=season,
            pool__is_active=True,
            is_active=True,
        ).values_list("id", flat=True)
    )

    if not participants:
        return 0

    existing_ids = set(
        PoolProjectionRecalc.objects.filter(participant_id__in=participants).values_list("participant_id", flat=True)
    )

    new_jobs = [
        PoolProjectionRecalc(
            participant_id=participant_id,
            status=PoolProjectionRecalc.STATUS_PENDING,
            requested_at=now,
        )
        for participant_id in participants
        if participant_id not in existing_ids
    ]

    if new_jobs:
        PoolProjectionRecalc.objects.bulk_create(new_jobs)

    if existing_ids:
        PoolProjectionRecalc.objects.filter(participant_id__in=existing_ids).exclude(
            status=PoolProjectionRecalc.STATUS_FAILED,
            attempts__gte=MAX_ATTEMPTS,
        ).update(
            status=PoolProjectionRecalc.STATUS_PENDING,
            requested_at=now,
        )

    return len(participants)


def has_pending_projection_recalc(participant):
    job = getattr(participant, "projection_recalc", None)
    if job is None:
        return False
    return job.status in PENDING_STATUSES


def process_next_projection_recalc_job():
    _recover_stale_processing_jobs()
    with transaction.atomic():
        # Compatibilidade com dados antigos: jobs pendentes acima do limite são finalizados como FAILED.
        PoolProjectionRecalc.objects.filter(
            status=PoolProjectionRecalc.STATUS_PENDING,
            attempts__gte=MAX_ATTEMPTS,
        ).update(
            status=PoolProjectionRecalc.STATUS_FAILED,
            last_finished_at=timezone.now(),
            last_error=f"Max retries reached ({MAX_ATTEMPTS})",
        )

        pending = PoolProjectionRecalc.objects.select_related("participant").filter(
            status=PoolProjectionRecalc.STATUS_PENDING,
            attempts__lt=MAX_ATTEMPTS,
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
        with transaction.atomic():
            projected_groups = sync_persisted_group_standings(participant=participant)
            sync_persisted_third_places(participant=participant, projected_groups=projected_groups)

            # CAS: só atualiza para IDLE se o job ainda está em PROCESSING.
            # Um enqueue() concorrente pode ter voltado o status para PENDING enquanto
            # o cálculo corria. Nesse caso preservamos o PENDING para o próximo ciclo.
            updated = PoolProjectionRecalc.objects.filter(
                id=job.id,
                status=PoolProjectionRecalc.STATUS_PROCESSING,
            ).update(
                status=PoolProjectionRecalc.STATUS_IDLE,
                last_finished_at=timezone.now(),
                last_error="",
            )

        if not updated:
            logger.info(
                "Job re-enfileirado durante processamento, status PENDING preservado: participant_id=%s",
                participant.id,
            )

    except Exception as exc:  # pragma: no cover
        logger.exception(
            "Erro ao calcular projeção: participant_id=%s attempts=%s",
            participant.id,
            job.attempts,
        )
        new_status = (
            PoolProjectionRecalc.STATUS_FAILED if job.attempts >= MAX_ATTEMPTS else PoolProjectionRecalc.STATUS_PENDING
        )
        # CAS idêntico: se re-enqueue já voltou para PENDING e ainda não atingiu
        # o limite, preservamos o PENDING; se atingiu, o loop de limpeza do próximo
        # ciclo marca como FAILED via attempts__gte=MAX_ATTEMPTS.
        PoolProjectionRecalc.objects.filter(
            id=job.id,
            status=PoolProjectionRecalc.STATUS_PROCESSING,
        ).update(
            status=new_status,
            last_finished_at=timezone.now(),
            last_error=str(exc),
        )

    return job

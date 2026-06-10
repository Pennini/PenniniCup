import logging
from collections import defaultdict

from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand
from django.template.loader import render_to_string
from django.utils.html import strip_tags

from src.football.models import Match
from src.pool.models import Pool, PoolBet, PoolParticipant
from src.pool.services.rules import PHASE_GROUP, PHASE_KNOCKOUT, POOL_TYPE_2, phase_for_match

logger = logging.getLogger(__name__)

_PHASE_LABELS = {
    PHASE_GROUP: "Fase de Grupos",
    PHASE_KNOCKOUT: "Mata-mata",
}


def _current_active_phase(pool):
    """Return the open phase to notify, or None if all phases are locked."""
    if pool.pool_type == POOL_TYPE_2:
        if not pool.is_phase_locked(PHASE_GROUP):
            return PHASE_GROUP
        if not pool.is_phase_locked(PHASE_KNOCKOUT):
            return PHASE_KNOCKOUT
        return None
    # Type 1: single lock covers all matches
    return PHASE_GROUP if not pool.is_phase_locked(PHASE_GROUP) else None


def _get_participants_with_missing_bets(pool_ids=None):
    pools = Pool.objects.filter(is_active=True)
    if pool_ids:
        pools = pools.filter(id__in=pool_ids)

    # user_id -> {pool_name -> {count, deadline, phase_label}}
    result = defaultdict(dict)
    user_map = {}

    for pool in pools.select_related("season"):
        current_phase = _current_active_phase(pool)
        if current_phase is None:
            continue

        deadline = pool.get_phase_lock_time(current_phase)

        matches = Match.objects.filter(season=pool.season, status=Match.STATUS_SCHEDULED).select_related("stage")

        if pool.pool_type == POOL_TYPE_2:
            matches = [m for m in matches if phase_for_match(m) == current_phase]
        else:
            matches = list(matches)

        if not matches:
            continue

        match_ids = [m.id for m in matches]

        active_participants = PoolParticipant.objects.filter(pool=pool, is_active=True).select_related("user")

        existing_bets = set(
            PoolBet.objects.filter(
                participant__pool=pool,
                match_id__in=match_ids,
                is_active=True,
            ).values_list("participant__user_id", "match_id")
        )

        phase_label = _PHASE_LABELS.get(current_phase) if pool.pool_type == POOL_TYPE_2 else None

        for pp in active_participants:
            missing = sum(1 for m in matches if (pp.user_id, m.id) not in existing_bets)
            if missing > 0:
                if pool.name not in result[pp.user_id]:
                    result[pp.user_id][pool.name] = {
                        "count": 0,
                        "deadline": deadline,
                        "phase_label": phase_label,
                    }
                result[pp.user_id][pool.name]["count"] += missing
                user_map[pp.user_id] = pp.user

    return result, user_map


class Command(BaseCommand):
    help = "Notifica por e-mail participantes com palpites pendentes"

    def add_arguments(self, parser):
        parser.add_argument(
            "--pool-id",
            type=int,
            nargs="+",
            help="IDs dos boloes (padrao: todos ativos)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Mostra quem seria notificado sem enviar e-mails",
        )
        parser.add_argument(
            "--site-url",
            default=getattr(settings, "SITE_URL", "https://penninicup.com.br"),
            help="URL base do site para o botao do e-mail",
        )

    def handle(self, *args, **options):
        pool_ids = options.get("pool_id")
        dry_run = options["dry_run"]
        site_url = options["site_url"]

        missing_by_user, user_map = _get_participants_with_missing_bets(pool_ids)

        if not missing_by_user:
            self.stdout.write("Nenhum participante com palpites pendentes.")
            return

        sent = 0
        skipped = 0

        for user_id, pools_missing in missing_by_user.items():
            user = user_map[user_id]
            pools_list = sorted(
                [(name, data["count"], data["deadline"], data["phase_label"]) for name, data in pools_missing.items()],
                key=lambda x: x[0],
            )
            total_missing = sum(data["count"] for data in pools_missing.values())
            earliest_deadline = min(
                (data["deadline"] for data in pools_missing.values() if data["deadline"]),
                default=None,
            )

            self.stdout.write(
                f"{'[DRY-RUN] ' if dry_run else ''}Notificando {user.username} <{user.email}> "
                f"— {total_missing} palpites em {len(pools_missing)} bolao(es)"
            )

            if dry_run:
                skipped += 1
                continue

            if not user.email:
                logger.warning("Sem e-mail para usuario %s, pulando.", user.username)
                skipped += 1
                continue

            html_body = render_to_string(
                "pool/emails/missing_bets_email.html",
                {
                    "user": user,
                    "pools": pools_list,
                    "site_url": site_url,
                    "earliest_deadline": earliest_deadline,
                },
            )

            try:
                send_mail(
                    subject="⚽ Você tem palpites pendentes no PenniniCup!",
                    message=strip_tags(html_body),
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[user.email],
                    html_message=html_body,
                    fail_silently=False,
                )
                sent += 1
                logger.info("E-mail enviado para %s (%s)", user.username, user.email)
            except Exception as exc:
                logger.error("Falha ao enviar e-mail para %s: %s", user.username, exc)
                skipped += 1

        if dry_run:
            self.stdout.write(self.style.WARNING(f"[DRY-RUN] {skipped} e-mails seriam enviados."))
        else:
            self.stdout.write(self.style.SUCCESS(f"{sent} e-mail(s) enviado(s). {skipped} pulado(s)."))

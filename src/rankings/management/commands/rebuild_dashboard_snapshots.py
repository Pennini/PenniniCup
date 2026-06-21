import logging

from django.core.management.base import BaseCommand, CommandError

from src.pool.models import Pool
from src.rankings.models import PoolDashboardSnapshot
from src.rankings.services.dashboard import build_dashboard_pool_payload

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Reconstrói (síncrono) o cache da dashboard de visão geral (PoolDashboardSnapshot)."

    def add_arguments(self, parser):
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument("--pool", type=str, help="Slug do bolão.")
        group.add_argument("--season", type=int, help="ID da season: todos os bolões ativos dela.")
        group.add_argument("--all", action="store_true", help="Todos os bolões ativos.")

    def handle(self, *args, **options):
        if options.get("pool"):
            pool = Pool.objects.filter(slug=options["pool"]).first()
            if not pool:
                raise CommandError(f"Bolão '{options['pool']}' não encontrado.")
            pools = [pool]
        elif options.get("season"):
            pools = list(Pool.objects.filter(season_id=options["season"], is_active=True))
        else:  # --all
            pools = list(Pool.objects.filter(is_active=True))

        for pool in pools:
            payload = build_dashboard_pool_payload(pool=pool)
            PoolDashboardSnapshot.objects.update_or_create(pool=pool, defaults={"payload": payload})
            self.stdout.write(f"{pool.slug}: dashboard reconstruída")
        self.stdout.write(f"Concluído: {len(pools)} bolões")
        logger.info("Rebuild dashboard snapshots: %s bolões", len(pools))

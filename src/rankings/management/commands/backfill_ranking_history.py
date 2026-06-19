import logging

from django.core.management.base import BaseCommand, CommandError

from src.pool.models import Pool
from src.rankings.services.history_backfill import backfill_pool_history

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Reconstrói o histórico de ranking (PoolRankingHistory) dos bolões."

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
            rounds = backfill_pool_history(pool)
            self.stdout.write(f"{pool.slug}: {rounds} rodadas")
            logger.info("Backfill ranking history pool=%s rounds=%s", pool.slug, rounds)
            return

        if options.get("season"):
            pools = list(Pool.objects.filter(season_id=options["season"], is_active=True))
        else:  # --all
            pools = list(Pool.objects.filter(is_active=True))

        for pool in pools:
            rounds = backfill_pool_history(pool)
            self.stdout.write(f"{pool.slug}: {rounds} rodadas")
        total = len(pools)
        self.stdout.write(f"Concluído: {total} bolões")
        logger.info("Backfill ranking history em massa: %s bolões", total)

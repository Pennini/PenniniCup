import logging

from django.core.management.base import BaseCommand

from src.pool.models import Pool
from src.pool.services.ranking import recalculate_all_pools, recalculate_pool_scores

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Recalcula pontuacao dos participantes e ranking dos boloes"

    def add_arguments(self, parser):
        parser.add_argument("--pool-id", type=int, help="ID do bolao para recalcualo pontual")

    def handle(self, *args, **options):
        pool_id = options.get("pool_id")
        if pool_id:
            pool = Pool.objects.filter(id=pool_id).first()
            if not pool:
                self.stdout.write(f"Bolao {pool_id} nao encontrado")
                return
            recalculate_pool_scores(pool)
            self.stdout.write(f"Pontuacoes recalculadas para bolao {pool_id}")
            logger.info("Pontuacoes recalculadas para bolao %s", pool_id)
            return

        recalculate_all_pools()
        self.stdout.write("Pontuacoes recalculadas para todos os boloes")
        logger.info("Pontuacoes recalculadas para todos os boloes")

import logging

from django.core.management.base import BaseCommand

from src.football.services.sync_knockout import sync_knockout

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Sincroniza Fases, Grupos e Estádios da Copa via API FIFA"

    def handle(self, *args, **options):
        sync_knockout()
        self.stdout.write("Fases, Grupos e Estádios criados")
        logger.info("Fases, Grupos e Estádios criados")

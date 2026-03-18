import logging

from django.core.management.base import BaseCommand

from src.football.services.sync_groups import sync_groups

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Sincroniza grupos da Copa via API FIFA"

    def handle(self, *args, **options):
        sync_groups()
        self.stdout.write("Grupos sincronizados")
        logger.info("Grupos sincronizados")

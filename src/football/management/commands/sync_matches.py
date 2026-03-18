import logging

from django.core.management.base import BaseCommand

from src.football.services.sync_matches import sync_matches

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Sincroniza partidas da Copa via API FIFA"

    def handle(self, *args, **options):
        sync_matches()
        self.stdout.write("Partidas sincronizadas")
        logger.info("Partidas sincronizadas")

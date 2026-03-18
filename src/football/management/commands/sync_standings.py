import logging

from django.core.management.base import BaseCommand

from src.football.services.sync_standings import sync_standings

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Sincroniza classificações da Copa via API FIFA"

    def handle(self, *args, **options):
        sync_standings()
        self.stdout.write("Classificações sincronizadas")
        logger.info("Classificações sincronizadas")

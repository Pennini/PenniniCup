import logging

from django.core.management.base import BaseCommand

from src.football.services.sync_teams import sync_teams

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Sincroniza times da Copa via API FIFA"

    def handle(self, *args, **options):
        sync_teams()
        self.stdout.write("Times sincronizados")
        logger.info("Times sincronizados")

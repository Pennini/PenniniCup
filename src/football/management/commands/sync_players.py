import logging

from django.core.management.base import BaseCommand

from src.football.services.sync_players import sync_players

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Sincroniza jogadores e comissão técnica via API FIFA"

    def handle(self, *args, **options):
        sync_players()
        self.stdout.write("Jogadores e comissão técnica sincronizados")
        logger.info("Jogadores e comissão técnica sincronizados")

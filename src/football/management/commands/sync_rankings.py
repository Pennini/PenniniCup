import logging

from django.core.management.base import BaseCommand

from src.football.services.sync_rankings import sync_rankings

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Sincroniza o ranking mundial (FIFA/Coca-Cola) dos times via API FIFA"

    def handle(self, *args, **options):
        updated = sync_rankings()
        self.stdout.write(f"Ranking mundial sincronizado ({updated} times)")
        logger.info("Ranking mundial sincronizado: %s times", updated)

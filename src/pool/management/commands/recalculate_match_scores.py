import logging

from django.core.management.base import BaseCommand

from src.football.models import Match
from src.pool.services.ranking import recalculate_match_scores

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Recalcula pontuacao apenas dos participantes impactados por uma partida"

    def add_arguments(self, parser):
        parser.add_argument("--match-id", type=int, required=True, help="ID da partida")

    def handle(self, *args, **options):
        match = Match.objects.get(id=options["match_id"])
        recalculate_match_scores(match)
        self.stdout.write(f"Pontuacoes recalculadas para a partida {match.id}")
        logger.info("Pontuacoes recalculadas para a partida %s", match.id)

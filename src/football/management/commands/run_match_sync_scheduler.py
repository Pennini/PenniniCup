import logging
import time

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import close_old_connections
from django.utils import timezone

from src.football.models import Season
from src.football.services.sync_matches import sync_matches
from src.football.services.sync_scheduler import should_run_sync

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Roda sync_matches apenas na janela de fim dos jogos."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Executa uma iteração e sai")

    def handle(self, *args, **options):
        if not getattr(settings, "MATCH_SYNC_SCHEDULER_ENABLED", True):
            self.stdout.write("Agendador desabilitado (MATCH_SYNC_SCHEDULER_ENABLED=False).")
            return

        window_hours = getattr(settings, "MATCH_SYNC_WINDOW_HOURS", 3)
        poll_interval = getattr(settings, "MATCH_SYNC_POLL_INTERVAL", 180)
        idle_interval = getattr(settings, "MATCH_SYNC_IDLE_INTERVAL", 300)
        once = options["once"]

        self.stdout.write("Agendador de sync iniciado.")
        while True:
            close_old_connections()
            try:
                season = Season.objects.filter(fifa_id=settings.FIFA_API_SEASON).first()
                in_window = season is not None and should_run_sync(season, timezone.now(), window_hours)
                if in_window:
                    sync_matches()
                    logger.info("Sync executado pelo agendador (jogo na janela).")
                    sleep_for = poll_interval
                else:
                    sleep_for = idle_interval
            except Exception:
                logger.exception("Erro no agendador de sync")
                close_old_connections()
                sleep_for = idle_interval

            if once:
                break
            time.sleep(sleep_for)

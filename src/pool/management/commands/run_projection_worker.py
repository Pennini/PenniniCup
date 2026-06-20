import logging
import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections

from src.pool.services.projection_queue import process_next_projection_recalc_job
from src.rankings.services.dashboard_queue import process_next_dashboard_snapshot_job
from src.rankings.services.snapshot_queue import process_next_ranking_snapshot_job

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Processa jobs de recálculo de projeção em loop contínuo"

    def add_arguments(self, parser):
        parser.add_argument(
            "--sleep",
            type=float,
            default=1.0,
            help="Segundos de espera quando não há jobs pendentes",
        )

    def handle(self, *args, **options):
        sleep_seconds = options["sleep"]
        self.stdout.write("Worker de projeção iniciado.")

        while True:
            close_old_connections()
            try:
                job = process_next_projection_recalc_job()
                if job is not None:
                    logger.info("Job projeção processado: participant=%s status=%s", job.participant_id, job.status)

                snapshot_job = process_next_ranking_snapshot_job()
                if snapshot_job is not None:
                    logger.info(
                        "Job snapshot processado: match=%s status=%s", snapshot_job.match_id, snapshot_job.status
                    )

                dashboard_job = process_next_dashboard_snapshot_job()
                if dashboard_job is not None:
                    logger.info(
                        "Job dashboard processado: pool=%s status=%s", dashboard_job.pool_id, dashboard_job.status
                    )

                if job is None and snapshot_job is None and dashboard_job is None:
                    time.sleep(sleep_seconds)
            except Exception:
                logger.exception("Erro inesperado no worker de projeção")
                close_old_connections()
                time.sleep(sleep_seconds)

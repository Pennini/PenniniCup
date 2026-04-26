import logging
import time

from django.core.management.base import BaseCommand

from src.pool.services.projection_queue import process_next_projection_recalc_job

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
            try:
                job = process_next_projection_recalc_job()
                if job is None:
                    time.sleep(sleep_seconds)
                else:
                    logger.info("Job processado: participant=%s status=%s", job.participant_id, job.status)
            except Exception:
                logger.exception("Erro inesperado no worker de projeção")
                time.sleep(sleep_seconds)

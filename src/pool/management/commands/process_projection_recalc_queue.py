from django.core.management.base import BaseCommand

from src.pool.services.projection_queue import process_next_projection_recalc_job


class Command(BaseCommand):
    help = "Processa fila de recálculo de projeções de mata-mata por participante"

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=100, help="Quantidade maxima de jobs a processar")

    def handle(self, *args, **options):
        limit = options["limit"]
        processed = 0

        for _ in range(limit):
            job = process_next_projection_recalc_job()
            if job is None:
                break
            processed += 1

        self.stdout.write(self.style.SUCCESS(f"Jobs processados: {processed}"))

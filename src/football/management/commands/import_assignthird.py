import re
from importlib import import_module
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from src.football.models import AssignThird, Season

PLACEHOLDER_PATTERN = re.compile(r"^3[-\s]?[A-Z]+$")
GROUP_LETTERS_PATTERN = re.compile(r"[A-L]")


class Command(BaseCommand):
    help = "Importa tabela AssignThird a partir de planilha Excel (.xlsx)."

    def add_arguments(self, parser):
        parser.add_argument("--file", required=True, help="Caminho do arquivo .xlsx")
        parser.add_argument("--season-id", required=True, type=int, help="ID da temporada")
        parser.add_argument("--sheet", default=None, help="Nome da aba (opcional)")
        parser.add_argument("--header-search-rows", default=10, type=int, help="Linhas para buscar cabecalho")
        parser.add_argument(
            "--replace", action="store_true", help="Apaga regras existentes da temporada antes de importar"
        )
        parser.add_argument("--dry-run", action="store_true", help="Valida e mostra resumo sem gravar no banco")

    def handle(self, *args, **options):
        try:
            openpyxl_module = import_module("openpyxl")
            load_workbook = openpyxl_module.load_workbook
        except ModuleNotFoundError as exc:
            raise CommandError(
                "Pacote openpyxl nao encontrado. Instale dependencias antes de rodar o comando."
            ) from exc

        file_path = Path(options["file"]).expanduser().resolve()
        if not file_path.exists():
            raise CommandError(f"Arquivo nao encontrado: {file_path}")

        if file_path.suffix.lower() != ".xlsx":
            raise CommandError("Formato nao suportado. Use arquivo .xlsx")

        season = Season.objects.filter(id=options["season_id"]).first()
        if season is None:
            raise CommandError(f"Temporada nao encontrada: id={options['season_id']}")

        wb = load_workbook(filename=file_path, data_only=True)
        ws = wb[options["sheet"]] if options["sheet"] else wb.active

        header_row, mapping = self._extract_placeholder_columns(ws, search_rows=options["header_search_rows"])
        data_rows = self._extract_rows(ws, start_row=header_row + 1, mapping=mapping)

        if not data_rows:
            raise CommandError("Nenhuma linha valida de combinacao encontrada na planilha.")

        total_rows = len(data_rows)
        total_rules = sum(len(row["rules"]) for row in data_rows)

        self.stdout.write(self.style.NOTICE(f"Aba: {ws.title}"))
        self.stdout.write(self.style.NOTICE(f"Linhas de combinacao detectadas: {total_rows}"))
        self.stdout.write(self.style.NOTICE(f"Regras AssignThird detectadas: {total_rules}"))

        if options["dry_run"]:
            self.stdout.write(self.style.SUCCESS("Dry-run concluido sem gravar dados."))
            return

        with transaction.atomic():
            if options["replace"]:
                deleted, _ = AssignThird.objects.filter(season=season).delete()
                self.stdout.write(self.style.WARNING(f"Regras antigas removidas: {deleted}"))

            saved = 0
            for row in data_rows:
                groups_key = row["groups_key"]
                for placeholder, third_group in row["rules"].items():
                    AssignThird.objects.update_or_create(
                        season=season,
                        groups_key=groups_key,
                        placeholder=placeholder,
                        defaults={"third_group": third_group},
                    )
                    saved += 1

        self.stdout.write(self.style.SUCCESS(f"Importacao concluida. Regras gravadas/atualizadas: {saved}"))

    def _extract_placeholder_columns(self, ws, search_rows):
        max_row = min(search_rows, ws.max_row)
        for row_idx in range(1, max_row + 1):
            mapping = {}
            for col_idx in range(2, ws.max_column + 1):
                value = ws.cell(row=row_idx, column=col_idx).value
                if value is None:
                    continue
                normalized = str(value).strip().upper().replace(" ", "")
                if PLACEHOLDER_PATTERN.match(normalized):
                    normalized = normalized.replace(" ", "")
                    mapping[col_idx] = normalized

            if mapping:
                return row_idx, mapping

        raise CommandError(
            "Nao foi possivel localizar placeholders na cabecalho. Esperado algo como: 3-CEFHI, 3-EFGIJ, ..."
        )

    def _extract_rows(self, ws, start_row, mapping):
        data_rows = []
        for row_idx in range(start_row, ws.max_row + 1):
            raw_group_combination = ws.cell(row=row_idx, column=1).value
            if raw_group_combination is None:
                continue

            group_token = str(raw_group_combination).strip().upper().replace(" ", "")
            letters = GROUP_LETTERS_PATTERN.findall(group_token)
            unique_letters = sorted(set(letters))
            if len(unique_letters) != 8:
                continue

            groups_key = ",".join(unique_letters)
            rules = {}
            for col_idx, placeholder in mapping.items():
                value = ws.cell(row=row_idx, column=col_idx).value
                if value is None:
                    continue
                third_group = str(value).strip().upper().replace(" ", "")
                if not third_group or len(third_group) != 1 or third_group < "A" or third_group > "L":
                    raise CommandError(
                        f"Valor invalido na linha {row_idx}, coluna {col_idx}: '{value}'. Esperado um grupo de A a L."
                    )
                rules[placeholder] = third_group

            if not rules:
                continue

            data_rows.append(
                {
                    "groups_key": groups_key,
                    "rules": rules,
                }
            )

        return data_rows

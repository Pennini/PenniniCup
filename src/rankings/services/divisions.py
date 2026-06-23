"""Agrupa as rows do ranking (já ordenadas por posição) em divisões para a UI:
Liga dos Campeões (top 3), Série A/B/C… (o meio) e Zona de Rebaixamento (últimos 4).

Função pura, sem acesso ao banco: recebe a lista ordenada e um getter de posição,
para servir tanto o leaderboard (RankingRow, .position) quanto a visão de palpites
por participante (dicts, ["position"]).
"""

import math
from dataclasses import dataclass

LIGA_LABEL = "Liga dos Campeões"
ZONA_LABEL = "Zona de Rebaixamento"
LIGA_SIZE = 3
ZONA_SIZE = 4
# Cores das séries do meio, em ciclo (Série A=blue, B=gray, C=yellow, …).
SERIES_COLORS = ["blue", "gray", "yellow", "purple", "green", "teal", "orange"]


@dataclass(frozen=True)
class Division:
    key: str  # "plain" | "liga" | "serie" | "zona"
    label: str  # "" para plain
    color: str  # "plain" | "gold" | "red" | uma de SERIES_COLORS
    position_range: str  # "top 3" | "últimos 4" | "4º–6º" | "" para plain
    rows: list


def _middle_division_count(middle: int) -> int:
    if middle <= 2:
        return 1
    return max(3, math.ceil(middle / 10))


def _middle_sizes(middle: int, count: int) -> list[int]:
    base, rem = divmod(middle, count)
    return [base + (1 if i < rem else 0) for i in range(count)]


def _range_label(rows, position_getter) -> str:
    start = position_getter(rows[0])
    end = position_getter(rows[-1])
    if start == end:
        return f"{start}º"
    return f"{start}º–{end}º"


def build_divisions(rows: list, *, position_getter=lambda row: row.position) -> list[Division]:
    total = len(rows)
    if total <= LIGA_SIZE + ZONA_SIZE:
        return [Division(key="plain", label="", color="plain", position_range="", rows=list(rows))]

    liga_rows = rows[:LIGA_SIZE]
    zona_rows = rows[total - ZONA_SIZE :]
    middle_rows = rows[LIGA_SIZE : total - ZONA_SIZE]

    divisions = [
        Division(
            key="liga",
            label=LIGA_LABEL,
            color="gold",
            position_range=f"top {LIGA_SIZE}",
            rows=liga_rows,
        )
    ]

    count = _middle_division_count(len(middle_rows))
    cursor = 0
    for index, size in enumerate(_middle_sizes(len(middle_rows), count)):
        chunk = middle_rows[cursor : cursor + size]
        cursor += size
        divisions.append(
            Division(
                key="serie",
                label=f"Série {chr(65 + index)}",
                color=SERIES_COLORS[index % len(SERIES_COLORS)],
                position_range=_range_label(chunk, position_getter),
                rows=chunk,
            )
        )

    divisions.append(
        Division(
            key="zona",
            label=ZONA_LABEL,
            color="red",
            position_range=f"últimos {ZONA_SIZE}",
            rows=zona_rows,
        )
    )
    return divisions

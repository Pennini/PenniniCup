from django.db import transaction

from src.football.models import Match
from src.pool.services.asof_standings import compute_asof_standings
from src.rankings.models import PoolRankingHistory, RankingTieBreakOverride
from src.rankings.services.leaderboard import _natural_key, _score_key


def _round_matches(pool):
    """Jogos encerrados da season em que ≥1 participante ativo apostou, em ordem."""
    return list(
        Match.objects.filter(
            season=pool.season,
            home_score__isnull=False,
            away_score__isnull=False,
            pool_bets__participant__pool=pool,
            pool_bets__participant__is_active=True,
        )
        .distinct()
        .order_by("match_date_utc", "match_number", "id")
    )


def _assign_positions(rows, override_map):
    """Ordena AsOfStanding pela chave de score (desc) + overrides + chave natural.

    Espelha a ordenação do leaderboard: maior score primeiro; empates resolvidos
    por override manual (quando houver) e depois por (joined_at, user_id).
    """
    rows = sorted(rows, key=lambda r: _natural_key(r.participant))
    rows.sort(key=lambda r: _score_key(r), reverse=True)

    # Aplica overrides dentro de cada grupo de empate.
    ordered = []
    i = 0
    while i < len(rows):
        j = i
        while j < len(rows) and _score_key(rows[j]) == _score_key(rows[i]):
            j += 1
        group = rows[i:j]
        manual = [r for r in group if r.participant.id in override_map]
        manual.sort(key=lambda r: (override_map[r.participant.id],) + _natural_key(r.participant))
        natural = [r for r in group if r.participant.id not in override_map]
        natural.sort(key=lambda r: _natural_key(r.participant))
        ordered.extend(manual + natural)
        i = j
    return ordered


@transaction.atomic
def backfill_pool_history(pool):
    """Reconstrói PoolRankingHistory do bolão (rebuild total, idempotente)."""
    PoolRankingHistory.objects.filter(pool=pool).delete()

    matches = _round_matches(pool)
    if not matches:
        return 0

    scoring_config = pool.get_scoring_config()
    official_result = pool.get_official_results()
    override_map = {
        row["participant_id"]: row["manual_position"]
        for row in RankingTieBreakOverride.objects.filter(pool=pool).values("participant_id", "manual_position")
    }

    allowed_ids = set()
    history_rows = []
    for round_index, match in enumerate(matches, start=1):
        allowed_ids.add(match.id)
        rows = compute_asof_standings(
            pool, allowed_ids, scoring_config=scoring_config, official_result=official_result
        )
        ordered = _assign_positions(rows, override_map)
        for position, row in enumerate(ordered, start=1):
            history_rows.append(
                PoolRankingHistory(
                    pool=pool,
                    participant=row.participant,
                    match=match,
                    round_index=round_index,
                    position=position,
                    total_points=row.total_points,
                    group_points=row.group_points,
                    knockout_points=row.knockout_points,
                    exact_score_hits=row.exact_score_hits,
                    advancing_hits=row.advancing_hits,
                    champion_hit=row.champion_hit,
                    top_scorer_hit=row.top_scorer_hit,
                )
            )

    if history_rows:
        PoolRankingHistory.objects.bulk_create(history_rows)
    return len(matches)


def backfill_pools(pools):
    """Backfill de vários bolões. Retorna o total de rodadas gravadas."""
    total = 0
    for pool in pools:
        total += backfill_pool_history(pool)
    return total

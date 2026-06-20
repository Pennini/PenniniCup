# Histórico de rodadas e mudança de posição — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Mostrar ao lado da posição de cada participante no ranking do bolão quantas posições ele subiu/desceu desde a rodada anterior, sustentado por uma tabela de histórico de pontos/dados por rodada.

**Architecture:** Novo modelo `PoolRankingHistory` grava o estado pós-recálculo (posição + dados de ranking) de cada participante a cada jogo encerrado (= jogo com placar). Um serviço `snapshot_round_for_match` é chamado pelo signal `post_save` de `Match`, logo após o recálculo de pontos. `build_pool_leaderboard` passa a expor `RankingRow.movement` comparando a posição atual com a da rodada anterior; o template renderiza um badge `▲N`/`▼N`.

**Tech Stack:** Python 3.12, Django 6, PostgreSQL (SQLite em teste), TailwindCSS, Poetry.

## Global Constraints

- Timezone-aware sempre (`django.utils.timezone`); nunca `datetime.now()`.
- "Jogo encerrado" = `match.home_score is not None and match.away_score is not None` (NÃO usar `status`).
- Granularidade: 1 rodada por jogo encerrado.
- Badge só aparece quando `movement` é truthy (≠ 0 e ≠ None).
- Correção de placar de um jogo já snapshotado faz upsert (mantém `round_index`).
- Sem baseline (< 2 rodadas, ou participante sem registro anterior) → `movement = None`.
- Ruff: target py312, line-length 119.
- Comando de teste (Git Bash):
  `DJANGO_SETTINGS_PROFILE=test poetry run python -m src.manage test <dotted.path> -v 2`

## File Structure

- Create: `src/rankings/services/position_snapshot.py` — serviço `snapshot_round_for_match`.
- Create: `src/rankings/migrations/0002_poolrankinghistory.py` — gerada via `makemigrations`.
- Modify: `src/rankings/models.py` — modelo `PoolRankingHistory`.
- Modify: `src/rankings/services/leaderboard.py` — `RankingRow.movement` + cálculo.
- Modify: `src/football/signals.py` — hook do snapshot.
- Modify: `src/rankings/templates/rankings/pool_dashboard.html` — badge nas duas visões.
- Modify: `src/rankings/tests.py` — testes de todas as tarefas.

______________________________________________________________________

### Task 1: Modelo `PoolRankingHistory` + migration

**Files:**

- Modify: `src/rankings/models.py`
- Create: `src/rankings/migrations/0002_poolrankinghistory.py` (via makemigrations)
- Test: `src/rankings/tests.py`

**Interfaces:**

- Produces: modelo `PoolRankingHistory` com campos `pool`, `participant`, `match`, `round_index`, `position`, `total_points`, `group_points`, `knockout_points`, `exact_score_hits`, `advancing_hits`, `champion_hit`, `top_scorer_hit`, `created_at`; `unique_together = ("pool", "participant", "match")`.

- [ ] **Step 1: Write the failing test**

Adicionar ao final de `src/rankings/tests.py`:

```python
from django.db import IntegrityError

from src.football.models import Match
from src.rankings.models import PoolRankingHistory


class PoolRankingHistoryModelTest(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="hist-owner", email="ho@example.com", password="123456Aa!")
        self.member = User.objects.create_user(username="hist-member", email="hm@example.com", password="123456Aa!")
        competition = Competition.objects.create(fifa_id=940, name="Copa Hist")
        self.season = Season.objects.create(
            fifa_id=940,
            competition=competition,
            name="Temporada Hist",
            year=2026,
            start_date="2026-06-01",
            end_date="2026-07-30",
        )
        self.stage = Stage.objects.create(fifa_id="ST940G", season=self.season, name="Group Stage", order=1)
        self.pool = Pool.objects.create(
            name="Pool Hist", slug="pool-hist", season=self.season, created_by=self.owner, requires_payment=False
        )
        self.participant = PoolParticipant.objects.create(pool=self.pool, user=self.member, is_active=True)
        self.match = _make_match(self.season, self.stage, number=1, kickoff=timezone.now())

    def test_history_row_persists_ranking_snapshot(self):
        row = PoolRankingHistory.objects.create(
            pool=self.pool,
            participant=self.participant,
            match=self.match,
            round_index=1,
            position=3,
            total_points=42,
            group_points=20,
            knockout_points=22,
            exact_score_hits=4,
            advancing_hits=6,
            champion_hit=True,
            top_scorer_hit=False,
        )
        row.refresh_from_db()
        self.assertEqual(row.round_index, 1)
        self.assertEqual(row.position, 3)
        self.assertEqual(row.total_points, 42)
        self.assertTrue(row.champion_hit)

    def test_history_unique_per_pool_participant_match(self):
        PoolRankingHistory.objects.create(
            pool=self.pool, participant=self.participant, match=self.match, round_index=1, position=1
        )
        with self.assertRaises(IntegrityError):
            PoolRankingHistory.objects.create(
                pool=self.pool, participant=self.participant, match=self.match, round_index=2, position=2
            )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `DJANGO_SETTINGS_PROFILE=test poetry run python -m src.manage test src.rankings.tests.PoolRankingHistoryModelTest -v 2`
Expected: FAIL — `ImportError`/`cannot import name 'PoolRankingHistory'`.

- [ ] **Step 3: Write the model**

Adicionar ao final de `src/rankings/models.py` (imports `settings`, `models`, `Pool`, `PoolParticipant` já existem no topo do arquivo):

```python
class PoolRankingHistory(models.Model):
    pool = models.ForeignKey(Pool, on_delete=models.CASCADE, related_name="ranking_history")
    participant = models.ForeignKey(PoolParticipant, on_delete=models.CASCADE, related_name="ranking_history")
    match = models.ForeignKey("football.Match", on_delete=models.CASCADE, related_name="ranking_history")
    round_index = models.PositiveIntegerField()
    position = models.PositiveIntegerField()

    total_points = models.IntegerField(default=0)
    group_points = models.IntegerField(default=0)
    knockout_points = models.IntegerField(default=0)
    exact_score_hits = models.IntegerField(default=0)
    advancing_hits = models.IntegerField(default=0)
    champion_hit = models.BooleanField(default=False)
    top_scorer_hit = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = (("pool", "participant", "match"),)
        indexes = [
            models.Index(fields=["pool", "round_index"], name="pool_rank_hist_round_idx"),
        ]
        ordering = ["pool", "round_index", "position"]

    def __str__(self):
        return f"{self.pool.slug} r{self.round_index} #{self.position} {self.participant.user}"
```

- [ ] **Step 4: Generate the migration**

Run: `poetry run python -m src.manage makemigrations rankings`
Expected: cria `src/rankings/migrations/0002_poolrankinghistory.py` com `Create model PoolRankingHistory`.

- [ ] **Step 5: Run test to verify it passes**

Run: `DJANGO_SETTINGS_PROFILE=test poetry run python -m src.manage test src.rankings.tests.PoolRankingHistoryModelTest -v 2`
Expected: PASS (2 testes).

- [ ] **Step 6: Commit**

```bash
git add src/rankings/models.py src/rankings/migrations/0002_poolrankinghistory.py src/rankings/tests.py
git commit -m "feat(rankings): modelo PoolRankingHistory para historico por rodada"
```

______________________________________________________________________

### Task 2: Serviço `snapshot_round_for_match`

**Files:**

- Create: `src/rankings/services/position_snapshot.py`
- Test: `src/rankings/tests.py`

**Interfaces:**

- Consumes: `PoolRankingHistory` (Task 1); `build_pool_leaderboard(pool=...) -> list[RankingRow]` (existente, cada `row` tem `.position` e `.participant`).

- Produces: `snapshot_round_for_match(match) -> None` — grava/atualiza 1 linha de histórico por participante dos bolões afetados, quando `match` tem placar.

- [ ] **Step 1: Write the failing test**

Adicionar ao final de `src/rankings/tests.py`:

```python
from src.rankings.services.position_snapshot import snapshot_round_for_match


class SnapshotRoundForMatchTest(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="snap-owner", email="so@example.com", password="123456Aa!")
        self.u_high = User.objects.create_user(username="snap-high", email="sh@example.com", password="123456Aa!")
        self.u_low = User.objects.create_user(username="snap-low", email="sl@example.com", password="123456Aa!")
        competition = Competition.objects.create(fifa_id=941, name="Copa Snap")
        self.season = Season.objects.create(
            fifa_id=941,
            competition=competition,
            name="Temporada Snap",
            year=2026,
            start_date="2026-06-01",
            end_date="2026-07-30",
        )
        self.stage = Stage.objects.create(fifa_id="ST941G", season=self.season, name="Group Stage", order=1)
        self.pool = Pool.objects.create(
            name="Pool Snap", slug="pool-snap", season=self.season, created_by=self.owner, requires_payment=False
        )
        self.p_high = PoolParticipant.objects.create(pool=self.pool, user=self.u_high, is_active=True, total_points=30)
        self.p_low = PoolParticipant.objects.create(pool=self.pool, user=self.u_low, is_active=True, total_points=10)
        self.match = _make_match(self.season, self.stage, number=1, kickoff=timezone.now())
        PoolBet.objects.create(
            participant=self.p_high, match=self.match, home_score_pred=1, away_score_pred=0, is_active=True
        )

    def _finish(self, match, home=1, away=0):
        match.home_score = home
        match.away_score = away
        match.save(update_fields=["home_score", "away_score"])

    def test_no_score_writes_nothing(self):
        snapshot_round_for_match(self.match)
        self.assertEqual(PoolRankingHistory.objects.count(), 0)

    def test_finished_match_writes_one_row_per_participant(self):
        self._finish(self.match)
        snapshot_round_for_match(self.match)
        rows = PoolRankingHistory.objects.filter(pool=self.pool, match=self.match)
        self.assertEqual(rows.count(), 2)
        by_pid = {r.participant_id: r for r in rows}
        self.assertEqual(by_pid[self.p_high.id].position, 1)
        self.assertEqual(by_pid[self.p_high.id].total_points, 30)
        self.assertEqual(by_pid[self.p_low.id].position, 2)
        self.assertTrue(all(r.round_index == 1 for r in rows))

    def test_only_affected_pools_are_snapshotted(self):
        other_pool = Pool.objects.create(
            name="Pool Outro",
            slug="pool-outro",
            season=self.season,
            created_by=self.owner,
            requires_payment=False,
        )
        PoolParticipant.objects.create(pool=other_pool, user=self.u_low, is_active=True, total_points=5)
        self._finish(self.match)
        snapshot_round_for_match(self.match)
        self.assertEqual(PoolRankingHistory.objects.filter(pool=other_pool).count(), 0)

    def test_re_snapshot_same_match_updates_in_place(self):
        self._finish(self.match)
        snapshot_round_for_match(self.match)
        # Correção: inverte a liderança e re-snapshota.
        self.p_low.total_points = 99
        self.p_low.save(update_fields=["total_points"])
        snapshot_round_for_match(self.match)
        rows = PoolRankingHistory.objects.filter(pool=self.pool, match=self.match)
        self.assertEqual(rows.count(), 2)
        by_pid = {r.participant_id: r for r in rows}
        self.assertEqual(by_pid[self.p_low.id].position, 1)
        self.assertTrue(all(r.round_index == 1 for r in rows))

    def test_second_match_increments_round_index(self):
        self._finish(self.match)
        snapshot_round_for_match(self.match)
        match2 = _make_match(self.season, self.stage, number=2, kickoff=timezone.now())
        PoolBet.objects.create(
            participant=self.p_high, match=match2, home_score_pred=2, away_score_pred=2, is_active=True
        )
        self._finish(match2)
        snapshot_round_for_match(match2)
        self.assertEqual(
            set(PoolRankingHistory.objects.filter(pool=self.pool).values_list("round_index", flat=True)),
            {1, 2},
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `DJANGO_SETTINGS_PROFILE=test poetry run python -m src.manage test src.rankings.tests.SnapshotRoundForMatchTest -v 2`
Expected: FAIL — `ModuleNotFoundError: src.rankings.services.position_snapshot`.

- [ ] **Step 3: Write the service**

Criar `src/rankings/services/position_snapshot.py`:

```python
from django.db.models import Max

from src.pool.models import Pool
from src.rankings.models import PoolRankingHistory
from src.rankings.services.leaderboard import build_pool_leaderboard

_SNAPSHOT_FIELDS = [
    "round_index",
    "position",
    "total_points",
    "group_points",
    "knockout_points",
    "exact_score_hits",
    "advancing_hits",
    "champion_hit",
    "top_scorer_hit",
]


def snapshot_round_for_match(match):
    """Grava (ou atualiza) o histórico de ranking de uma rodada.

    Uma rodada = um jogo encerrado, isto é, um Match que já possui placar
    (home_score e away_score não nulos). Para cada bolão afetado (ativo, da
    season do jogo, com participante que apostou nesse jogo) grava uma linha por
    participante com a posição e os dados de ranking pós-recálculo. Re-chamar para
    o mesmo match (correção de placar) atualiza as linhas mantendo o round_index.
    """
    if match.home_score is None or match.away_score is None:
        return

    affected_pools = Pool.objects.filter(
        season=match.season,
        is_active=True,
        participants__bets__match=match,
    ).distinct()

    for pool in affected_pools:
        existing_round = (
            PoolRankingHistory.objects.filter(pool=pool, match=match).values_list("round_index", flat=True).first()
        )
        if existing_round is not None:
            round_index = existing_round
        else:
            max_round = PoolRankingHistory.objects.filter(pool=pool).aggregate(value=Max("round_index"))["value"]
            round_index = (max_round or 0) + 1

        history_rows = [
            PoolRankingHistory(
                pool=pool,
                participant=row.participant,
                match=match,
                round_index=round_index,
                position=row.position,
                total_points=row.participant.total_points,
                group_points=row.participant.group_points,
                knockout_points=row.participant.knockout_points,
                exact_score_hits=row.participant.exact_score_hits,
                advancing_hits=row.participant.advancing_hits,
                champion_hit=row.participant.champion_hit,
                top_scorer_hit=row.participant.top_scorer_hit,
            )
            for row in build_pool_leaderboard(pool=pool)
        ]
        if history_rows:
            PoolRankingHistory.objects.bulk_create(
                history_rows,
                update_conflicts=True,
                unique_fields=["pool", "participant", "match"],
                update_fields=_SNAPSHOT_FIELDS,
            )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `DJANGO_SETTINGS_PROFILE=test poetry run python -m src.manage test src.rankings.tests.SnapshotRoundForMatchTest -v 2`
Expected: PASS (5 testes).

- [ ] **Step 5: Commit**

```bash
git add src/rankings/services/position_snapshot.py src/rankings/tests.py
git commit -m "feat(rankings): servico snapshot_round_for_match por jogo encerrado"
```

______________________________________________________________________

### Task 3: Hook no signal de `Match`

**Files:**

- Modify: `src/football/signals.py`
- Test: `src/rankings/tests.py`

**Interfaces:**

- Consumes: `snapshot_round_for_match(match)` (Task 2).

- Produces: efeito colateral — salvar um `Match` com placar gera/atualiza histórico após o recálculo de pontos.

- [ ] **Step 1: Write the failing test**

Adicionar ao final de `src/rankings/tests.py`:

```python
class SnapshotSignalTest(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="sig-owner", email="sigo@example.com", password="123456Aa!")
        self.member = User.objects.create_user(username="sig-mem", email="sigm@example.com", password="123456Aa!")
        competition = Competition.objects.create(fifa_id=942, name="Copa Sig")
        self.season = Season.objects.create(
            fifa_id=942,
            competition=competition,
            name="Temporada Sig",
            year=2026,
            start_date="2026-06-01",
            end_date="2026-07-30",
        )
        self.stage = Stage.objects.create(fifa_id="ST942G", season=self.season, name="Group Stage", order=1)
        self.pool = Pool.objects.create(
            name="Pool Sig", slug="pool-sig", season=self.season, created_by=self.owner, requires_payment=False
        )
        self.participant = PoolParticipant.objects.create(pool=self.pool, user=self.member, is_active=True)
        self.match = _make_match(self.season, self.stage, number=1, kickoff=timezone.now())
        PoolBet.objects.create(
            participant=self.participant, match=self.match, home_score_pred=1, away_score_pred=0, is_active=True
        )

    def test_saving_match_with_score_creates_history(self):
        self.match.home_score = 1
        self.match.away_score = 0
        self.match.save(update_fields=["home_score", "away_score"])
        self.assertEqual(PoolRankingHistory.objects.filter(pool=self.pool, match=self.match).count(), 1)

    def test_saving_match_without_score_creates_no_history(self):
        self.match.match_number = 99
        self.match.save(update_fields=["match_number"])
        self.assertEqual(PoolRankingHistory.objects.filter(pool=self.pool).count(), 0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `DJANGO_SETTINGS_PROFILE=test poetry run python -m src.manage test src.rankings.tests.SnapshotSignalTest -v 2`
Expected: FAIL — `test_saving_match_with_score_creates_history` espera 1, obtém 0.

- [ ] **Step 3: Wire the hook**

Em `src/football/signals.py`, adicionar o import no topo (junto aos outros imports de serviço):

```python
from src.rankings.services.position_snapshot import snapshot_round_for_match
```

Substituir o bloco `if score_should_recalc:` (atualmente linhas 48-52) por:

```python
    if score_should_recalc:
        try:
            recalculate_match_scores(match=instance)
        except Exception:
            logger.exception("Falha ao recalcular pontuacoes do bolao apos salvar partida: match_id=%s", instance.id)

        if instance.home_score is not None and instance.away_score is not None:
            try:
                snapshot_round_for_match(instance)
            except Exception:
                logger.exception("Falha ao snapshotar rodada apos salvar partida: match_id=%s", instance.id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `DJANGO_SETTINGS_PROFILE=test poetry run python -m src.manage test src.rankings.tests.SnapshotSignalTest -v 2`
Expected: PASS (2 testes).

- [ ] **Step 5: Commit**

```bash
git add src/football/signals.py src/rankings/tests.py
git commit -m "feat(rankings): snapshota rodada no signal de Match com placar"
```

______________________________________________________________________

### Task 4: `RankingRow.movement` em `build_pool_leaderboard`

**Files:**

- Modify: `src/rankings/services/leaderboard.py`
- Test: `src/rankings/tests.py`

**Interfaces:**

- Consumes: `PoolRankingHistory` (Task 1).

- Produces: `RankingRow.movement: int | None` — `posição_rodada_anterior - posição_atual`; positivo = subiu, negativo = desceu, 0 = igual, `None` = sem baseline.

- [ ] **Step 1: Write the failing test**

Adicionar ao final de `src/rankings/tests.py`:

```python
class LeaderboardMovementTest(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="mov-owner", email="mvo@example.com", password="123456Aa!")
        self.u_a = User.objects.create_user(username="mov-a", email="mva@example.com", password="123456Aa!")
        self.u_b = User.objects.create_user(username="mov-b", email="mvb@example.com", password="123456Aa!")
        competition = Competition.objects.create(fifa_id=943, name="Copa Mov")
        self.season = Season.objects.create(
            fifa_id=943,
            competition=competition,
            name="Temporada Mov",
            year=2026,
            start_date="2026-06-01",
            end_date="2026-07-30",
        )
        self.stage = Stage.objects.create(fifa_id="ST943G", season=self.season, name="Group Stage", order=1)
        self.pool = Pool.objects.create(
            name="Pool Mov", slug="pool-mov", season=self.season, created_by=self.owner, requires_payment=False
        )
        # Estado atual: A líder (1º), B (2º).
        self.p_a = PoolParticipant.objects.create(pool=self.pool, user=self.u_a, is_active=True, total_points=50)
        self.p_b = PoolParticipant.objects.create(pool=self.pool, user=self.u_b, is_active=True, total_points=30)
        self.match1 = _make_match(self.season, self.stage, number=1, kickoff=timezone.now())
        self.match2 = _make_match(self.season, self.stage, number=2, kickoff=timezone.now())

    def _round(self, match, round_index, positions):
        # positions: {participant: position}
        for participant, position in positions.items():
            PoolRankingHistory.objects.create(
                pool=self.pool,
                participant=participant,
                match=match,
                round_index=round_index,
                position=position,
            )

    def test_movement_none_when_single_round(self):
        self._round(self.match1, 1, {self.p_a: 1, self.p_b: 2})
        rows = build_pool_leaderboard(pool=self.pool)
        self.assertTrue(all(row.movement is None for row in rows))

    def test_movement_up_down_and_equal(self):
        # Rodada anterior (round 1): B 1º, A 2º. Atual: A 1º, B 2º.
        self._round(self.match1, 1, {self.p_b: 1, self.p_a: 2})
        self._round(self.match2, 2, {self.p_a: 1, self.p_b: 2})
        rows = {row.participant_id: row for row in build_pool_leaderboard(pool=self.pool)}
        self.assertEqual(rows[self.p_a.id].movement, 1)  # 2 -> 1, subiu 1
        self.assertEqual(rows[self.p_b.id].movement, -1)  # 1 -> 2, caiu 1

    def test_movement_none_for_participant_without_previous_round(self):
        # Round anterior só tem A; B entrou depois.
        self._round(self.match1, 1, {self.p_a: 1})
        self._round(self.match2, 2, {self.p_a: 1, self.p_b: 2})
        rows = {row.participant_id: row for row in build_pool_leaderboard(pool=self.pool)}
        self.assertEqual(rows[self.p_a.id].movement, 0)
        self.assertIsNone(rows[self.p_b.id].movement)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `DJANGO_SETTINGS_PROFILE=test poetry run python -m src.manage test src.rankings.tests.LeaderboardMovementTest -v 2`
Expected: FAIL — `RankingRow` não tem campo `movement` (`TypeError`/`AttributeError`).

- [ ] **Step 3: Add `movement` to the dataclass and compute it**

Em `src/rankings/services/leaderboard.py`:

Adicionar campo ao dataclass (atual nas linhas 11-16):

```python
@dataclass(frozen=True)
class RankingRow:
    position: int
    participant: PoolParticipant
    is_tied: bool
    tie_resolved_manually: bool
    movement: int | None = None
```

Adicionar import no topo (junto aos outros imports do módulo):

```python
from django.db.models import Exists, Max, OuterRef
```

(substitui o import atual `from django.db.models import Exists, OuterRef`)

Adicionar helper antes de `build_pool_leaderboard`:

```python
def _previous_round_positions(pool: Pool):
    """Mapa {participant_id: position} da rodada anterior do bolão.

    Rodada anterior = o segundo maior round_index gravado (o maior == estado
    atual). Retorna {} se houver menos de duas rodadas.
    """
    from src.rankings.models import PoolRankingHistory

    round_indexes = list(
        PoolRankingHistory.objects.filter(pool=pool)
        .values_list("round_index", flat=True)
        .distinct()
        .order_by("-round_index")[:2]
    )
    if len(round_indexes) < 2:
        return {}

    previous_round = round_indexes[1]
    return dict(
        PoolRankingHistory.objects.filter(pool=pool, round_index=previous_round).values_list(
            "participant_id", "position"
        )
    )
```

Substituir o loop final que monta `rows` (atual nas linhas 104-114) por:

```python
    previous_positions = _previous_round_positions(pool)

    rows = []
    for index, participant in enumerate(ordered_participants, start=1):
        score_key = _score_key(participant)
        previous_position = previous_positions.get(participant.id)
        movement = previous_position - index if previous_position is not None else None
        rows.append(
            RankingRow(
                position=index,
                participant=participant,
                is_tied=tie_counts.get(score_key, 1) > 1,
                tie_resolved_manually=participant.id in manual_resolution_ids,
                movement=movement,
            )
        )

    return rows
```

- [ ] **Step 4: Run test to verify it passes**

Run: `DJANGO_SETTINGS_PROFILE=test poetry run python -m src.manage test src.rankings.tests.LeaderboardMovementTest -v 2`
Expected: PASS (3 testes).

- [ ] **Step 5: Run the full rankings suite (regressão)**

Run: `DJANGO_SETTINGS_PROFILE=test poetry run python -m src.manage test src.rankings.tests -v 2`
Expected: PASS (todos).

- [ ] **Step 6: Commit**

```bash
git add src/rankings/services/leaderboard.py src/rankings/tests.py
git commit -m "feat(rankings): RankingRow.movement vs rodada anterior"
```

______________________________________________________________________

### Task 5: Badge no template `pool_dashboard.html`

**Files:**

- Modify: `src/rankings/templates/rankings/pool_dashboard.html`
- Test: `src/rankings/tests.py`

**Interfaces:**

- Consumes: `row.movement` (Task 4) exposto em `leaderboard_rows`.

- Produces: badge `▲N` (verde) / `▼N` (vermelho) ao lado do `#pos` no card mobile e na tabela desktop; nada quando `movement` é 0/None.

- [ ] **Step 1: Write the failing test**

Adicionar ao final de `src/rankings/tests.py`:

```python
class RankingBadgeTemplateTest(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="bdg-owner", email="bdo@example.com", password="123456Aa!")
        self.u_a = User.objects.create_user(username="bdg-a", email="bda@example.com", password="123456Aa!")
        self.u_b = User.objects.create_user(username="bdg-b", email="bdb@example.com", password="123456Aa!")
        competition = Competition.objects.create(fifa_id=944, name="Copa Bdg")
        self.season = Season.objects.create(
            fifa_id=944,
            competition=competition,
            name="Temporada Bdg",
            year=2026,
            start_date="2026-06-01",
            end_date="2026-07-30",
        )
        self.stage = Stage.objects.create(fifa_id="ST944G", season=self.season, name="Group Stage", order=1)
        self.pool = Pool.objects.create(
            name="Pool Bdg", slug="pool-bdg", season=self.season, created_by=self.owner, requires_payment=False
        )
        # Atual: A 1º (50), B 2º (30).
        self.p_a = PoolParticipant.objects.create(pool=self.pool, user=self.u_a, is_active=True, total_points=50)
        self.p_b = PoolParticipant.objects.create(pool=self.pool, user=self.u_b, is_active=True, total_points=30)
        self.match1 = _make_match(self.season, self.stage, number=1, kickoff=timezone.now())
        self.match2 = _make_match(self.season, self.stage, number=2, kickoff=timezone.now())
        # Rodada anterior: B 1º, A 2º -> A subiu 1 (▲1), B caiu 1 (▼1).
        for participant, position in {self.p_b: 1, self.p_a: 2}.items():
            PoolRankingHistory.objects.create(
                pool=self.pool, participant=participant, match=self.match1, round_index=1, position=position
            )
        for participant, position in {self.p_a: 1, self.p_b: 2}.items():
            PoolRankingHistory.objects.create(
                pool=self.pool, participant=participant, match=self.match2, round_index=2, position=position
            )

    def test_dashboard_renders_movement_badges(self):
        self.client.force_login(self.u_a)
        response = self.client.get(reverse("pool:ranking", kwargs={"slug": self.pool.slug}))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("▲1", body)
        self.assertIn("▼1", body)

    def test_dashboard_omits_badge_when_no_movement(self):
        # Sem rodada anterior distinta -> sem badge.
        PoolRankingHistory.objects.filter(pool=self.pool, round_index=1).delete()
        self.client.force_login(self.u_a)
        response = self.client.get(reverse("pool:ranking", kwargs={"slug": self.pool.slug}))
        body = response.content.decode()
        self.assertNotIn("▲", body)
        self.assertNotIn("▼", body)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `DJANGO_SETTINGS_PROFILE=test poetry run python -m src.manage test src.rankings.tests.RankingBadgeTemplateTest -v 2`
Expected: FAIL — `test_dashboard_renders_movement_badges` não encontra `▲1`.

- [ ] **Step 3: Add the badge in both views**

Em `src/rankings/templates/rankings/pool_dashboard.html`.

**Mobile card** — substituir a linha do `#pos` (atual linha 103):

```html
                        <p class="shrink-0 text-lg font-bold {% if row.position == 1 %}text-yellow-200{% elif row.position == 2 %}text-slate-100{% elif row.position == 3 %}text-amber-200{% else %}text-orange-300{% endif %}">#{{ row.position }}</p>
```

por:

```html
                        <p class="shrink-0 flex items-center gap-1.5 text-lg font-bold {% if row.position == 1 %}text-yellow-200{% elif row.position == 2 %}text-slate-100{% elif row.position == 3 %}text-amber-200{% else %}text-orange-300{% endif %}">
                            <span>#{{ row.position }}</span>
                            {% if row.movement %}{% if row.movement > 0 %}<span class="text-xs font-semibold text-emerald-400">▲{{ row.movement }}</span>{% elif row.movement < 0 %}<span class="text-xs font-semibold text-red-400">▼{{ row.movement|cut:"-" }}</span>{% endif %}{% endif %}
                        </p>
```

**Desktop table** — substituir a célula do `#pos` (atual linha 141):

```html
                            <td class="px-4 py-3 font-semibold {% if row.position == 1 %}text-yellow-200{% elif row.position == 2 %}text-slate-100{% elif row.position == 3 %}text-amber-200{% endif %}">#{{ row.position }}</td>
```

por:

```html
                            <td class="px-4 py-3 font-semibold {% if row.position == 1 %}text-yellow-200{% elif row.position == 2 %}text-slate-100{% elif row.position == 3 %}text-amber-200{% endif %}">
                                <span class="inline-flex items-center gap-1.5">
                                    <span>#{{ row.position }}</span>
                                    {% if row.movement %}{% if row.movement > 0 %}<span class="text-xs font-semibold text-emerald-400">▲{{ row.movement }}</span>{% elif row.movement < 0 %}<span class="text-xs font-semibold text-red-400">▼{{ row.movement|cut:"-" }}</span>{% endif %}{% endif %}
                                </span>
                            </td>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `DJANGO_SETTINGS_PROFILE=test poetry run python -m src.manage test src.rankings.tests.RankingBadgeTemplateTest -v 2`
Expected: PASS (2 testes).

- [ ] **Step 5: Commit**

```bash
git add src/rankings/templates/rankings/pool_dashboard.html src/rankings/tests.py
git commit -m "feat(rankings): badge de mudanca de posicao no ranking"
```

______________________________________________________________________

### Task 6: Verificação final e lint

**Files:** nenhum novo.

- [ ] **Step 1: Run the full rankings + football suites**

Run:

```bash
DJANGO_SETTINGS_PROFILE=test poetry run python -m src.manage test src.rankings.tests src.football.tests -v 2
```

Expected: PASS (todos).

- [ ] **Step 2: Lint**

Run: `poetry run pre-commit run --all-files`
Expected: ruff/format passam; corrigir o que o ruff apontar e re-rodar.

- [ ] **Step 3: Commit (se o lint alterou algo)**

```bash
git add -A
git commit -m "chore(rankings): lint apos feature de mudanca de posicao"
```

______________________________________________________________________

## Self-Review

**Spec coverage:**

- Tabela de histórico (pontos/dados por rodada) → Task 1.
- Snapshot por jogo encerrado (= com placar), só bolões afetados, upsert em correção, round_index incremental → Task 2.
- Hook no signal após recálculo, gate por placar → Task 3.
- Delta vs rodada anterior, sem baseline → None, formato sinal/cor → Tasks 4 (lógica) e 5 (visual).
- Badge só quando ≠0, mobile + desktop → Task 5.
- Plano de testes do spec coberto pelas classes de teste de cada task.

**Placeholder scan:** nenhum TBD/TODO; todo passo tem código/comando concretos.

**Type consistency:** `snapshot_round_for_match(match)` (Task 2 e 3); `RankingRow.movement: int | None` (Task 4 e 5); `_previous_round_positions(pool) -> dict[int,int]` (Task 4). Campos do modelo usados no serviço (Task 2) batem com os definidos na Task 1.

## Fora de escopo

- Gráficos de evolução, backfill histórico, agrupamento por dia/fase.

# Backfill as-of do histórico de ranking — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reconstruir `PoolRankingHistory` fielmente (standings "as-of" por rodada) para bolões em andamento, disparável por admin action e comando de gestão.

**Architecture:** Um agregador as-of isolado (`asof_standings.py`) recalcula standings de um bolão considerando só um conjunto de jogos permitidos, sem tocar o banco nem o fluxo de pontuação live. Um service de backfill itera as rodadas (jogos encerrados em ordem cronológica), chama o agregador com prefixos crescentes, ordena com a chave de desempate do leaderboard e grava as linhas de histórico. Admin e comando apenas chamam o service.

**Tech Stack:** Django 6, Python 3.12, PostgreSQL (SQLite em teste).

## Global Constraints

- Testes rodam com: `PENNINICUP_SETTINGS_PROFILE=test poetry run python -m src.manage test <caminho.pontilhado> --settings=src.config.settings -v 1` (o `DJANGO_SETTINGS_PROFILE` do CLAUDE.md NÃO ativa o profile de teste — use `PENNINICUP_SETTINGS_PROFILE`).
- Timezone: tudo America/Sao_Paulo, datetimes aware; use `django.utils.timezone`.
- Jogo encerrado = `home_score is not None and away_score is not None`.
- Rodada = jogo encerrado da season do bolão em que ≥1 participante ativo apostou, ordenado por `(match_date_utc, match_number, id)`.
- Rebuild total por bolão (apaga e reconstrói) → idempotente.
- Elegibilidade e tie-break overrides usam o estado atual (mesma regra do leaderboard live).
- Lint: ruff (line-length 119, py312). Commits passam por pre-commit; ao commitar arquivos sob `docs/` use `-f` (gitignored) e isole mudanças não relacionadas em `src/` com `git stash push -- src/` antes / `git stash pop` depois.

______________________________________________________________________

## File Structure

- `src/pool/services/asof_standings.py` (Create) — `AsOfStanding` dataclass + `compute_asof_standings(...)`. Agregação as-of isolada; reusa helpers leaf read-only de `ranking.py` (`_real_qualifier_position_map`, `_match_winner_loser`) e `calculate_bet_points`.
- `src/rankings/services/history_backfill.py` (Create) — `backfill_pool_history(pool)` e `backfill_pools(pools)`. Monta rodadas, chama o agregador, ordena, grava `PoolRankingHistory`.
- `src/rankings/management/commands/backfill_ranking_history.py` (Create) — comando `--pool/--season/--all`.
- `src/rankings/management/__init__.py`, `src/rankings/management/commands/__init__.py` (Create) — pacotes do comando.
- `src/rankings/admin.py` (Modify) — admin action "Reprocessar histórico de ranking" no `Pool`.
- `src/pool/tests.py` (Modify/Test) — testes do agregador as-of.
- `src/rankings/tests.py` (Modify/Test) — testes do service, comando e admin action.

______________________________________________________________________

## Task 1: Agregador as-of — agregação por aposta (sem bônus)

**Files:**

- Create: `src/pool/services/asof_standings.py`
- Test: `src/pool/tests.py`

**Interfaces:**

- Consumes: `calculate_bet_points(bet, scoring_config, pool_type)` de `src/pool/services/scoring.py`; `phase_for_match`, `PHASE_GROUP` de `src/pool/services/rules.py`; `eligible_participants(pool)` de `src/rankings/services/leaderboard.py`.

- Produces: dataclass `AsOfStanding(participant, total_points, group_points, knockout_points, exact_score_hits, advancing_hits, champion_hit, top_scorer_hit)` e `compute_asof_standings(pool, allowed_match_ids, scoring_config, official_result) -> list[AsOfStanding]`. Nesta task os campos de bônus (`champion_hit`, `top_scorer_hit`) ficam `False` e `total_points` cobre só pontos por aposta.

- [ ] **Step 1: Write the failing test**

Em `src/pool/tests.py`, adicione no topo (junto aos imports existentes):

```python
from src.pool.services.asof_standings import AsOfStanding, compute_asof_standings
```

Adicione a classe de teste (use os mesmos factories/utilitários já usados por outras classes de `src/pool/tests.py` para criar season, stage de grupo, pool tipo 2, participante e partidas; se não houver helper compartilhado, crie os objetos inline como as classes vizinhas fazem):

```python
class ComputeAsOfStandingsBetsTest(TestCase):
    def setUp(self):
        # Reutilize o padrão de setup das classes de pontuação existentes neste
        # arquivo: uma season, um stage de grupo (normalize_stage_key -> "GROUP"),
        # um pool tipo 2 e um participante. Crie DOIS jogos de grupo finalizados.
        self.season = _make_season()
        self.group_stage = _make_group_stage(self.season)
        self.pool = _make_pool(self.season, pool_type=2)
        self.participant = _make_participant(self.pool)
        self.match1 = _make_finished_group_match(
            self.season, self.group_stage, match_number=1, home_score=1, away_score=0
        )
        self.match2 = _make_finished_group_match(
            self.season, self.group_stage, match_number=2, home_score=2, away_score=2
        )
        # Palpite exato no match1 (1-0) e errado no match2.
        _make_bet(self.participant, self.match1, home=1, away=0)
        _make_bet(self.participant, self.match2, home=0, away=0)
        self.scoring_config = self.pool.get_scoring_config()
        self.official_result = self.pool.get_official_results()

    def test_only_allowed_matches_count(self):
        rows = compute_asof_standings(
            self.pool,
            allowed_match_ids={self.match1.id},
            scoring_config=self.scoring_config,
            official_result=self.official_result,
        )
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertIsInstance(row, AsOfStanding)
        self.assertEqual(row.participant.id, self.participant.id)
        # Só o match1 (placar exato) conta; match2 fora do conjunto é ignorado.
        self.assertEqual(row.total_points, self.scoring_config.group_exact_score)
        self.assertEqual(row.group_points, self.scoring_config.group_exact_score)
        self.assertEqual(row.knockout_points, 0)
        self.assertEqual(row.exact_score_hits, 1)
        # Bônus ainda não implementado nesta task.
        self.assertFalse(row.champion_hit)
        self.assertFalse(row.top_scorer_hit)
```

> Nota ao implementador: os nomes `_make_*` acima são marcadores do padrão de setup. Use os helpers/inline que as classes existentes de `src/pool/tests.py` já empregam (procure por `Season.objects.create`, `Stage.objects.create`, `Pool.objects.create`, `PoolParticipant.objects.create`, `PoolBet.objects.create`). Não crie um factory novo se o arquivo já tem um padrão.

- [ ] **Step 2: Run test to verify it fails**

Run: `PENNINICUP_SETTINGS_PROFILE=test poetry run python -m src.manage test src.pool.tests.ComputeAsOfStandingsBetsTest --settings=src.config.settings -v 1`
Expected: FAIL com `ModuleNotFoundError: No module named 'src.pool.services.asof_standings'`.

- [ ] **Step 3: Write minimal implementation**

Crie `src/pool/services/asof_standings.py`:

```python
from dataclasses import dataclass

from src.pool.models import PoolParticipant
from src.pool.services.rules import PHASE_GROUP, phase_for_match
from src.pool.services.scoring import calculate_bet_points
from src.rankings.services.leaderboard import eligible_participants


@dataclass
class AsOfStanding:
    participant: PoolParticipant
    total_points: int = 0
    group_points: int = 0
    knockout_points: int = 0
    exact_score_hits: int = 0
    advancing_hits: int = 0
    champion_hit: bool = False
    top_scorer_hit: bool = False


def compute_asof_standings(pool, allowed_match_ids, scoring_config, official_result):
    """Standings do bolão considerando só os jogos em allowed_match_ids.

    Não toca o banco: retorna uma lista de AsOfStanding (uma por participante
    elegível). Espelha recalculate_participant_scores, mas restrito ao conjunto
    de jogos permitidos. Bônus são adicionados na Task 2.
    """
    allowed_match_ids = set(allowed_match_ids)
    pool_type = pool.pool_type
    participants = list(eligible_participants(pool).select_related("user"))

    rows = []
    for participant in participants:
        total_points = 0
        group_points = 0
        knockout_points = 0
        exact_score_hits = 0
        advancing_hits = 0

        bets = participant.bets.select_related("match", "match__stage").all()
        for bet in bets:
            if bet.match_id not in allowed_match_ids:
                continue
            score_data = calculate_bet_points(bet, scoring_config=scoring_config, pool_type=pool_type)
            total_points += score_data["points"]
            if phase_for_match(bet.match) == PHASE_GROUP:
                group_points += score_data["points"]
            else:
                knockout_points += score_data["points"]
            if score_data["exact_score"]:
                exact_score_hits += 1
            if score_data["advancing_correct"]:
                advancing_hits += 1

        rows.append(
            AsOfStanding(
                participant=participant,
                total_points=total_points,
                group_points=group_points,
                knockout_points=knockout_points,
                exact_score_hits=exact_score_hits,
                advancing_hits=advancing_hits,
            )
        )

    return rows
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PENNINICUP_SETTINGS_PROFILE=test poetry run python -m src.manage test src.pool.tests.ComputeAsOfStandingsBetsTest --settings=src.config.settings -v 1`
Expected: PASS (1 test OK).

- [ ] **Step 5: Commit**

```bash
git add src/pool/services/asof_standings.py src/pool/tests.py
git commit -m "feat(rankings): agregador as-of de standings por aposta"
```

______________________________________________________________________

## Task 2: Agregador as-of — bônus com gating temporal

**Files:**

- Modify: `src/pool/services/asof_standings.py`
- Test: `src/pool/tests.py`

**Interfaces:**

- Consumes: `_real_qualifier_position_map(season)`, `_match_winner_loser(match)` de `src/pool/services/ranking.py`; `normalize_stage_key`, `POOL_TYPE_1` de `src/pool/services/rules.py`; `Match` de `src/football/models.py`.

- Produces: `compute_asof_standings(...)` agora soma bônus de avanço de time (tipo 1), classificados de grupo e pódio, e seta `champion_hit`/`top_scorer_hit`, todos restritos a `allowed_match_ids`.

- [ ] **Step 1: Write the failing test**

Em `src/pool/tests.py`, adicione:

```python
class ComputeAsOfStandingsBonusTest(TestCase):
    def setUp(self):
        # Pool tipo 2; participante com palpite de classificados de grupo que
        # acerta um qualificador real. Cria todos os jogos de grupo finalizados.
        self.season = _make_season()
        self.group_stage = _make_group_stage(self.season)
        self.pool = _make_pool(self.season, pool_type=2)
        self.participant = _make_participant(self.pool)
        self.group_matches = _make_full_group_stage_finished(self.season, self.group_stage)
        _make_projected_qualifier_hit(self.participant)  # 1 classificado correto na posição correta
        self.scoring_config = self.pool.get_scoring_config()
        self.official_result = self.pool.get_official_results()

    def test_group_bonus_zero_when_group_not_complete_in_set(self):
        partial = {self.group_matches[0].id}
        rows = compute_asof_standings(self.pool, partial, self.scoring_config, self.official_result)
        # Fase de grupos NÃO completa dentro do conjunto -> sem bônus de classificados.
        qualifier_points = (
            self.scoring_config.group_qualifier_points + self.scoring_config.group_qualifier_position_bonus
        )
        self.assertEqual(rows[0].group_points, _expected_bet_group_points_partial(self.participant, partial))
        self.assertNotIn(qualifier_points, [rows[0].group_points])  # bônus ausente

    def test_group_bonus_applied_when_group_complete_in_set(self):
        all_ids = {m.id for m in self.group_matches}
        rows = compute_asof_standings(self.pool, all_ids, self.scoring_config, self.official_result)
        # Fase de grupos completa no conjunto -> bônus de classificados entra em group_points.
        self.assertGreaterEqual(
            rows[0].group_points,
            self.scoring_config.group_qualifier_points,
        )
```

> Nota ao implementador: `_make_full_group_stage_finished` cria todos os jogos de grupo da season finalizados e retorna a lista; `_make_projected_qualifier_hit` cria as predições de standing do participante (`participant.projected_standings`) batendo com o `Standing` real para ≥1 grupo. Modele a partir de como `src/pool/tests.py` já testa `_calculate_group_qualifier_bonus` (procure por `projected_standings` / `Standing.objects.create`). Se já houver tais helpers/cenários, reutilize. O helper `_expected_bet_group_points_partial` apenas soma os pontos por aposta dos jogos em `partial`; se for trivial no seu cenário (0), asserte `== 0`.

- [ ] **Step 2: Run test to verify it fails**

Run: `PENNINICUP_SETTINGS_PROFILE=test poetry run python -m src.manage test src.pool.tests.ComputeAsOfStandingsBonusTest --settings=src.config.settings -v 1`
Expected: FAIL (bônus de classificados ainda não somado; `group_points` menor que o esperado em `test_group_bonus_applied_when_group_complete_in_set`).

- [ ] **Step 3: Write minimal implementation**

Em `src/pool/services/asof_standings.py`, troque os imports do topo por:

```python
from dataclasses import dataclass

from src.football.models import Match
from src.pool.models import PoolParticipant
from src.pool.services.ranking import _match_winner_loser, _real_qualifier_position_map
from src.pool.services.rules import PHASE_GROUP, POOL_TYPE_1, normalize_stage_key, phase_for_match
from src.pool.services.scoring import calculate_bet_points
from src.rankings.services.leaderboard import eligible_participants
```

Adicione, abaixo da dataclass, os helpers de gating:

```python
def _group_match_ids(season):
    return {
        m.id
        for m in Match.objects.filter(season=season).select_related("stage")
        if normalize_stage_key(m.stage) == "GROUP"
    }


def _asof_team_advancement_bonus(participant, allowed_match_ids, scoring_config):
    """Tipo 1: bônus por time previsto que avançou, só de jogos no conjunto."""
    total = 0
    stage_winners_cache = {}
    for bet in participant.bets.select_related("match", "match__stage").all():
        if bet.match_id not in allowed_match_ids:
            continue
        if phase_for_match(bet.match) == PHASE_GROUP:
            continue
        stage_id = bet.match.stage_id
        if stage_id not in stage_winners_cache:
            stage_winners_cache[stage_id] = set(
                Match.objects.filter(stage_id=stage_id, winner_id__isnull=False, id__in=allowed_match_ids).values_list(
                    "winner_id", flat=True
                )
            )
        if bet.winner_pred_id and bet.winner_pred_id in stage_winners_cache[stage_id]:
            total += scoring_config.knockout_team_advancement_bonus
    return total


def _asof_group_qualifier_bonus(participant, season, allowed_match_ids, scoring_config):
    """Classificados de grupo: só quando todos os jogos de grupo estão no conjunto."""
    group_ids = _group_match_ids(season)
    if not group_ids or not group_ids <= allowed_match_ids:
        return 0

    real_qualifiers_by_group, _ = _real_qualifier_position_map(season)
    if not real_qualifiers_by_group:
        return 0

    proj_positions_by_group = {}
    for s in participant.projected_standings.filter(position__lte=3).values("group_id", "position", "team_id"):
        proj_positions_by_group.setdefault(s["group_id"], {})[s["position"]] = s["team_id"]

    total = 0
    for group_id, real_positions in real_qualifiers_by_group.items():
        proj_positions = proj_positions_by_group.get(group_id, {})
        real_qualifier_ids = set(real_positions.values())
        for position, team_id in proj_positions.items():
            if team_id in real_qualifier_ids:
                total += scoring_config.group_qualifier_points
                if real_positions.get(position) == team_id:
                    total += scoring_config.group_qualifier_position_bonus
    return total


def _asof_podium(season, allowed_match_ids):
    """(champion_id, runner_up_id, third_id) derivados só dos jogos no conjunto."""
    champion_id = runner_up_id = third_id = None
    final_match = (
        Match.objects.filter(season=season, stage__order=7).select_related("home_team", "away_team", "winner").first()
    )
    third_match = (
        Match.objects.filter(season=season, stage__order=6).select_related("home_team", "away_team", "winner").first()
    )
    if final_match and final_match.id in allowed_match_ids:
        champion, runner_up = _match_winner_loser(final_match)
        champion_id = champion.id if champion else None
        runner_up_id = runner_up.id if runner_up else None
    if third_match and third_match.id in allowed_match_ids:
        third, _ = _match_winner_loser(third_match)
        third_id = third.id if third else None
    return champion_id, runner_up_id, third_id


def _asof_podium_bonus(participant, podium, official_result, scoring_config):
    """Bônus de pódio/artilheiro. Retorna (points, champion_hit, top_scorer_hit)."""
    champion_id, runner_up_id, third_id = podium
    points = 0
    champion_hit = bool(participant.champion_pred_id and participant.champion_pred_id == champion_id)
    runner_up_hit = bool(participant.runner_up_pred_id and participant.runner_up_pred_id == runner_up_id)
    third_place_hit = bool(participant.third_place_pred_id and participant.third_place_pred_id == third_id)

    top_scorer_tied_ids = list(official_result.top_scorers.values_list("id", flat=True))
    if top_scorer_tied_ids:
        top_scorer_hit = bool(participant.top_scorer_pred_id and participant.top_scorer_pred_id in top_scorer_tied_ids)
    else:
        top_scorer_hit = bool(
            participant.top_scorer_pred_id
            and official_result.top_scorer_id
            and participant.top_scorer_pred_id == official_result.top_scorer_id
        )

    if champion_hit:
        points += scoring_config.bonus_champion_points
    if runner_up_hit:
        points += scoring_config.bonus_runner_up_points
    if third_place_hit:
        points += scoring_config.bonus_third_place_points
    if top_scorer_hit:
        points += scoring_config.bonus_top_scorer_points

    return points, champion_hit, top_scorer_hit
```

Dentro de `compute_asof_standings`, depois do loop de apostas e antes de montar `AsOfStanding`, some os bônus. Substitua o bloco que cria o `AsOfStanding` por:

```python
        if pool_type == POOL_TYPE_1:
            advancement_bonus = _asof_team_advancement_bonus(participant, allowed_match_ids, scoring_config)
            knockout_points += advancement_bonus
            total_points += advancement_bonus

        qualifier_bonus = _asof_group_qualifier_bonus(
            participant, pool.season, allowed_match_ids, scoring_config
        )
        group_points += qualifier_bonus
        total_points += qualifier_bonus

        podium_points, champion_hit, top_scorer_hit = _asof_podium_bonus(
            participant, podium, official_result, scoring_config
        )
        total_points += podium_points

        rows.append(
            AsOfStanding(
                participant=participant,
                total_points=total_points,
                group_points=group_points,
                knockout_points=knockout_points,
                exact_score_hits=exact_score_hits,
                advancing_hits=advancing_hits,
                champion_hit=champion_hit,
                top_scorer_hit=top_scorer_hit,
            )
        )
```

E, logo após `participants = list(...)` (antes do `for participant`), calcule o pódio uma vez:

```python
    podium = _asof_podium(pool.season, allowed_match_ids)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PENNINICUP_SETTINGS_PROFILE=test poetry run python -m src.manage test src.pool.tests.ComputeAsOfStandingsBetsTest src.pool.tests.ComputeAsOfStandingsBonusTest --settings=src.config.settings -v 1`
Expected: PASS (3 tests OK — Task 1 continua passando).

- [ ] **Step 5: Commit**

```bash
git add src/pool/services/asof_standings.py src/pool/tests.py
git commit -m "feat(rankings): bonus com gating temporal no agregador as-of"
```

______________________________________________________________________

## Task 3: Service de backfill

**Files:**

- Create: `src/rankings/services/history_backfill.py`
- Test: `src/rankings/tests.py`

**Interfaces:**

- Consumes: `compute_asof_standings(...)`, `AsOfStanding` da Task 1/2; `_score_key` de `src/rankings/services/leaderboard.py`; `RankingTieBreakOverride`, `PoolRankingHistory` de `src/rankings/models.py`; `Match` de `src/football/models.py`.

- Produces: `backfill_pool_history(pool) -> int` (nº de rodadas gravadas) e `backfill_pools(pools) -> int` (soma).

- [ ] **Step 1: Write the failing test**

Em `src/rankings/tests.py`, adicione import no topo:

```python
from src.rankings.services.history_backfill import backfill_pool_history, backfill_pools
```

Adicione a classe (reutilize o padrão de setup já presente em `src/rankings/tests.py` — as classes existentes como `LeaderboardMovementTest` já criam pool, participantes, matches e bets; espelhe esse setup):

```python
class BackfillPoolHistoryTest(TestCase):
    def setUp(self):
        # 3 participantes, 3 jogos de grupo finalizados em datas crescentes,
        # com palpites que fazem a classificação mudar de uma rodada para outra.
        self.pool, self.participants, self.matches = _build_pool_with_3_rounds()

    def test_backfill_creates_one_round_per_finished_match(self):
        count = backfill_pool_history(self.pool)
        self.assertEqual(count, 3)
        round_indexes = sorted(
            PoolRankingHistory.objects.filter(pool=self.pool).values_list("round_index", flat=True).distinct()
        )
        self.assertEqual(round_indexes, [1, 2, 3])
        # Cada rodada tem uma linha por participante.
        for r in (1, 2, 3):
            self.assertEqual(
                PoolRankingHistory.objects.filter(pool=self.pool, round_index=r).count(),
                len(self.participants),
            )
        # Posições de cada rodada são 1..N sem buracos.
        for r in (1, 2, 3):
            positions = sorted(
                PoolRankingHistory.objects.filter(pool=self.pool, round_index=r).values_list("position", flat=True)
            )
            self.assertEqual(positions, list(range(1, len(self.participants) + 1)))

    def test_backfill_is_idempotent(self):
        first = backfill_pool_history(self.pool)
        rows_first = sorted(
            PoolRankingHistory.objects.filter(pool=self.pool).values_list(
                "round_index", "participant_id", "position", "total_points"
            )
        )
        second = backfill_pool_history(self.pool)
        rows_second = sorted(
            PoolRankingHistory.objects.filter(pool=self.pool).values_list(
                "round_index", "participant_id", "position", "total_points"
            )
        )
        self.assertEqual(first, second)
        self.assertEqual(rows_first, rows_second)

    def test_backfill_pools_sums_rounds(self):
        total = backfill_pools([self.pool])
        self.assertEqual(total, 3)
```

> Nota ao implementador: `_build_pool_with_3_rounds` deve criar um pool tipo 2 com 3 participantes ativos elegíveis, 3 jogos de grupo finalizados com `match_date_utc` crescente, e bets de cada participante em todos os 3 jogos com placares que produzam pontuações diferentes por rodada (para que `position` varie entre rodadas). Espelhe o setup de `LeaderboardMovementTest` em `src/rankings/tests.py`.

- [ ] **Step 2: Run test to verify it fails**

Run: `PENNINICUP_SETTINGS_PROFILE=test poetry run python -m src.manage test src.rankings.tests.BackfillPoolHistoryTest --settings=src.config.settings -v 1`
Expected: FAIL com `ModuleNotFoundError: No module named 'src.rankings.services.history_backfill'`.

- [ ] **Step 3: Write minimal implementation**

Crie `src/rankings/services/history_backfill.py`:

```python
from django.db import transaction

from src.football.models import Match
from src.pool.services.asof_standings import compute_asof_standings
from src.rankings.models import PoolRankingHistory, RankingTieBreakOverride
from src.rankings.services.leaderboard import _natural_key, _score_key

_HISTORY_FIELDS = (
    "total_points",
    "group_points",
    "knockout_points",
    "exact_score_hits",
    "advancing_hits",
    "champion_hit",
    "top_scorer_hit",
)


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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PENNINICUP_SETTINGS_PROFILE=test poetry run python -m src.manage test src.rankings.tests.BackfillPoolHistoryTest --settings=src.config.settings -v 1`
Expected: PASS (3 tests OK).

- [ ] **Step 5: Commit**

```bash
git add src/rankings/services/history_backfill.py src/rankings/tests.py
git commit -m "feat(rankings): service de backfill as-of do historico de ranking"
```

______________________________________________________________________

## Task 4: Comando de gestão

**Files:**

- Create: `src/rankings/management/__init__.py`
- Create: `src/rankings/management/commands/__init__.py`
- Create: `src/rankings/management/commands/backfill_ranking_history.py`
- Test: `src/rankings/tests.py`

**Interfaces:**

- Consumes: `backfill_pool_history`, `backfill_pools` da Task 3; `Pool` de `src/pool/models.py`.

- Produces: comando `backfill_ranking_history` com flags mutuamente exclusivas `--pool SLUG`, `--season ID`, `--all`.

- [ ] **Step 1: Write the failing test**

Em `src/rankings/tests.py`, adicione imports no topo:

```python
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
```

Adicione a classe:

```python
class BackfillCommandTest(TestCase):
    def setUp(self):
        self.pool, self.participants, self.matches = _build_pool_with_3_rounds()

    def test_command_with_pool_slug_backfills(self):
        out = StringIO()
        call_command("backfill_ranking_history", pool=self.pool.slug, stdout=out)
        self.assertEqual(PoolRankingHistory.objects.filter(pool=self.pool).count(), 3 * len(self.participants))
        self.assertIn(self.pool.slug, out.getvalue())

    def test_command_requires_a_selector(self):
        with self.assertRaises(CommandError):
            call_command("backfill_ranking_history")

    def test_command_unknown_pool_errors(self):
        with self.assertRaises(CommandError):
            call_command("backfill_ranking_history", pool="nao-existe")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PENNINICUP_SETTINGS_PROFILE=test poetry run python -m src.manage test src.rankings.tests.BackfillCommandTest --settings=src.config.settings -v 1`
Expected: FAIL com `CommandError: Unknown command: 'backfill_ranking_history'` (ou erro de comando inexistente).

- [ ] **Step 3: Write minimal implementation**

Crie os pacotes vazios `src/rankings/management/__init__.py` e `src/rankings/management/commands/__init__.py` (arquivos vazios).

Crie `src/rankings/management/commands/backfill_ranking_history.py`:

```python
import logging

from django.core.management.base import BaseCommand, CommandError

from src.pool.models import Pool
from src.rankings.services.history_backfill import backfill_pool_history, backfill_pools

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Reconstrói o histórico de ranking (PoolRankingHistory) dos bolões."

    def add_arguments(self, parser):
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument("--pool", type=str, help="Slug do bolão.")
        group.add_argument("--season", type=int, help="ID da season: todos os bolões ativos dela.")
        group.add_argument("--all", action="store_true", help="Todos os bolões ativos.")

    def handle(self, *args, **options):
        if options.get("pool"):
            pool = Pool.objects.filter(slug=options["pool"]).first()
            if not pool:
                raise CommandError(f"Bolão '{options['pool']}' não encontrado.")
            rounds = backfill_pool_history(pool)
            self.stdout.write(f"{pool.slug}: {rounds} rodadas")
            logger.info("Backfill ranking history pool=%s rounds=%s", pool.slug, rounds)
            return

        if options.get("season"):
            pools = list(Pool.objects.filter(season_id=options["season"], is_active=True))
        else:  # --all
            pools = list(Pool.objects.filter(is_active=True))

        for pool in pools:
            rounds = backfill_pool_history(pool)
            self.stdout.write(f"{pool.slug}: {rounds} rodadas")
        total = sum(1 for _ in pools)
        self.stdout.write(f"Concluído: {total} bolões")
        logger.info("Backfill ranking history em massa: %s bolões", total)
```

> Nota: `backfill_pools` é importado para reuso futuro/admin; o comando itera explicitamente para imprimir por bolão. Se o lint reclamar de import não usado, remova `backfill_pools` deste import (ele é usado no admin, Task 5).

- [ ] **Step 4: Run test to verify it passes**

Run: `PENNINICUP_SETTINGS_PROFILE=test poetry run python -m src.manage test src.rankings.tests.BackfillCommandTest --settings=src.config.settings -v 1`
Expected: PASS (3 tests OK).

- [ ] **Step 5: Commit**

```bash
git add src/rankings/management/ src/rankings/tests.py
git commit -m "feat(rankings): comando backfill_ranking_history"
```

______________________________________________________________________

## Task 5: Admin action

**Files:**

- Modify: `src/rankings/admin.py`
- Test: `src/rankings/tests.py`

**Interfaces:**

- Consumes: `backfill_pool_history` da Task 3; `Pool`, `PoolAdmin` existente (de `src/pool/admin.py`).

- Produces: admin action "Reprocessar histórico de ranking" disponível na changelist do `Pool`.

- [ ] **Step 1: Inspecione o admin do Pool**

Antes de codar, leia `src/pool/admin.py` para descobrir se `Pool` já está registrado e com qual `ModelAdmin`. A action deve ser adicionada a esse admin existente. Se `Pool` for registrado em `src/pool/admin.py` com `@admin.register(Pool)`, adicione a action lá (e este arquivo passa a ser o modificado em vez de `src/rankings/admin.py`). Use o caminho real conforme o registro existente.

- [ ] **Step 2: Write the failing test**

Em `src/rankings/tests.py`, adicione:

```python
class BackfillAdminActionTest(TestCase):
    def setUp(self):
        self.pool, self.participants, self.matches = _build_pool_with_3_rounds()

    def test_admin_action_backfills_selected_pools(self):
        from django.contrib.admin.sites import site

        from src.pool.models import Pool
        from src.rankings.admin import backfill_ranking_history_action

        model_admin = site._registry[Pool]
        request = _make_admin_request()  # request com messages habilitado; ver helper existente
        queryset = Pool.objects.filter(id=self.pool.id)
        backfill_ranking_history_action(model_admin, request, queryset)

        self.assertEqual(
            PoolRankingHistory.objects.filter(pool=self.pool).count(),
            3 * len(self.participants),
        )
```

> Nota ao implementador: `_make_admin_request` é um `RequestFactory` request com middleware de messages anexado (padrão Django: `SessionMiddleware` + `MessageMiddleware`, ou `request._messages = FallbackStorage(request)`). Se `src/rankings/tests.py` ou outro tests.py já tiver um helper assim, reutilize. A action é importável de `src.rankings.admin` como função de módulo, mesmo que registrada via outro ModelAdmin.

- [ ] **Step 3: Run test to verify it fails**

Run: `PENNINICUP_SETTINGS_PROFILE=test poetry run python -m src.manage test src.rankings.tests.BackfillAdminActionTest --settings=src.config.settings -v 1`
Expected: FAIL com `ImportError: cannot import name 'backfill_ranking_history_action'`.

- [ ] **Step 4: Write minimal implementation**

Em `src/rankings/admin.py`, adicione a função de action e registre-a no admin do `Pool`. Implementação (função de módulo + registro via `AdminSite.add_action` para não exigir mexer em `src/pool/admin.py`):

```python
from django.contrib import admin, messages

from src.pool.models import Pool
from src.rankings.models import RankingTieBreakOverride
from src.rankings.services.history_backfill import backfill_pool_history


@admin.action(description="Reprocessar histórico de ranking")
def backfill_ranking_history_action(modeladmin, request, queryset):
    total_rounds = 0
    failed = []
    for pool in queryset:
        try:
            total_rounds += backfill_pool_history(pool)
        except Exception:  # noqa: BLE001 — reporta sem abortar os demais
            failed.append(pool.slug)
    if failed:
        messages.error(request, f"Falha ao reprocessar: {', '.join(failed)}")
    messages.success(request, f"Histórico reprocessado: {total_rounds} rodadas em {queryset.count()} bolão(ões).")


admin.site.add_action(backfill_ranking_history_action, "backfill_ranking_history_action")
```

> Mantenha o `RankingTieBreakOverrideAdmin` existente neste arquivo. `admin.site.add_action(...)` registra a action globalmente (aparece em todas as changelists); se preferir restringir só ao `Pool`, adicione `actions = [backfill_ranking_history_action]` ao `ModelAdmin` do Pool em `src/pool/admin.py` e remova a linha `admin.site.add_action`. Escolha conforme o que o teste importa (função de módulo) — ambas as formas mantêm `backfill_ranking_history_action` importável de `src.rankings.admin`.

- [ ] **Step 5: Run test to verify it passes**

Run: `PENNINICUP_SETTINGS_PROFILE=test poetry run python -m src.manage test src.rankings.tests.BackfillAdminActionTest --settings=src.config.settings -v 1`
Expected: PASS (1 test OK).

- [ ] **Step 6: Suíte completa de rankings + pool**

Run: `PENNINICUP_SETTINGS_PROFILE=test poetry run python -m src.manage test src.rankings.tests src.pool.tests --settings=src.config.settings -v 1`
Expected: PASS (sem regressões).

- [ ] **Step 7: Commit**

```bash
git add src/rankings/admin.py src/rankings/tests.py
git commit -m "feat(rankings): admin action para reprocessar historico de ranking"
```

______________________________________________________________________

## Notas finais

- Após o backfill rodar em produção, a aba de ranking já mostra o badge de movimento (a feature de leitura `_previous_round_positions` + template já existe). Nenhuma mudança de template é necessária.
- Para popular a produção: `poetry run python -m src.manage backfill_ranking_history --all` (ou via admin action no Pool selecionado).
- Continuidade: a última rodada do backfill coincide com as standings live; snapshots live seguintes anexam `round_index = max+1` normalmente.

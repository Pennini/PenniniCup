# Tipo 2 — pontuação de mata-mata por fase — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fazer o mata-mata do bolão Tipo 2 escalar a pontuação por fase (R32 < R16 < QF < SF < FINAL, mais THIRD com faixa própria), sem mudar a semântica do gate de classificado.

**Architecture:** Novo modelo filho `PoolKnockoutPhaseScoring` (uma linha por fase por config) guarda as 5 faixas de placar por fase. `calculate_bet_points` recebe um mapa `{phase_key: row}` e, no branch Tipo 2, escolhe a faixa pela fase do jogo (`normalize_stage_key`). Sem mapa/linha → fallback nos campos flat `knockout_*` (retrocompatível). Call sites (ranking/asof/diagnose) montam o mapa uma vez e o repassam. Tipo 1 (posicional, flat) intacto.

**Tech Stack:** Django 6, Python 3.12, PostgreSQL. Testes via `unittest` do Django.

## Global Constraints

- Só `POOL_TYPE_2` muda comportamento. Tipo 1 (posicional) lê os campos flat `knockout_*` e **não** muda.

- O gate de classificado **não muda**: `predicted_advancing_id == match.winner_id`, por jogo, por identidade. Classificado errado → 0, sem consolação, sem cascata.

- **Sem** bônus de classificado separado — a magnitude está embutida nas faixas.

- `src/config/settings/base.py` tem modificação pré-existente NÃO relacionada: **nunca** stage/commit esse arquivo.

- `docs/` é gitignored — commitar specs/plans exige `git add -f`.

- Rodar testes com `PENNINICUP_SETTINGS_PROFILE=test` (CLAUDE.md diz `DJANGO_SETTINGS_PROFILE`, está errado).

- Fases (chaves) vêm de `normalize_stage_key`: `"R32" | "R16" | "QF" | "SF" | "FINAL" | "THIRD"`.

- Tabela de pontos (defaults oficiais):

  | phase_key | exact | advancing_goals | diff | loser_goals | advancing_only |
  | --------- | ----- | --------------- | ---- | ----------- | -------------- |
  | R32       | 40    | 30              | 25   | 22          | 20             |
  | R16       | 50    | 38              | 32   | 28          | 26             |
  | QF        | 62    | 47              | 40   | 35          | 32             |
  | SF        | 78    | 59              | 50   | 44          | 40             |
  | FINAL     | 95    | 72              | 60   | 53          | 48             |
  | THIRD     | 55    | 41              | 35   | 30          | 27             |

______________________________________________________________________

## File Structure

- `src/pool/models.py` — novo modelo `PoolKnockoutPhaseScoring`, constante `KNOCKOUT_PHASE_DEFAULTS`, helper `ensure_knockout_phase_rows`, alteração em `Pool.get_scoring_config`.
- `src/pool/migrations/0017_poolknockoutphasescoring.py` — schema (auto).
- `src/pool/migrations/0018_seed_knockout_phase_scoring.py` — data migration (popula configs existentes).
- `src/pool/admin.py` — `TabularInline` das faixas por fase em `PoolScoringConfigAdmin`.
- `src/pool/services/scoring.py` — faixa por tier; param `knockout_phase_scoring`; fallback flat.
- `src/pool/services/ranking.py` — monta `{phase_key: row}` e repassa.
- `src/pool/services/asof_standings.py` — idem.
- `src/rankings/management/commands/diagnose_dashboard.py` — idem (inline, comando diagnóstico).
- `src/rankings/services/dashboard.py` — `_match_max_points` ciente de fase no Tipo 2 (evita utilização >100%).
- `src/SCORE.md` — documentar tabela por fase; remover frase de "eliminado antes".
- `src/pool/tests/test_pool.py` — testes unit + integração.

______________________________________________________________________

### Task 1: Modelo `PoolKnockoutPhaseScoring` + migrations + admin

**Files:**

- Modify: `src/pool/models.py` (adicionar constante + modelo + helper; alterar `get_scoring_config` em `Pool`, hoje em `models.py:101-103`)
- Create: `src/pool/migrations/0017_poolknockoutphasescoring.py` (gerada)
- Create: `src/pool/migrations/0018_seed_knockout_phase_scoring.py`
- Modify: `src/pool/admin.py` (inline em `PoolScoringConfigAdmin`, hoje em `admin.py:303-319`)
- Test: `src/pool/tests/test_pool.py`

**Interfaces:**

- Produces:

  - `PoolKnockoutPhaseScoring` model com campos `config (FK)`, `phase_key (str)`, `exact`, `advancing_goals`, `diff`, `loser_goals`, `advancing_only` (todos `PositiveSmallIntegerField`). `related_name="knockout_phases"`. `unique_together = ("config", "phase_key")`.
  - `KNOCKOUT_PHASE_DEFAULTS: dict[str, dict[str, int]]` em `src/pool/models.py`.
  - `ensure_knockout_phase_rows(config) -> None` em `src/pool/models.py`.
  - `Pool.get_scoring_config()` passa a garantir as 6 linhas em config recém-criada.

- [ ] **Step 1: Escrever o teste que falha**

Em `src/pool/tests/test_pool.py`, adicionar no fim do arquivo:

```python
class KnockoutPhaseScoringSeedTest(TestCase):
    """get_scoring_config garante as 6 faixas de fase com os defaults oficiais."""

    def _make_minimal_pool(self):
        from src.football.models import Competition, Season
        from src.pool.models import Pool, POOL_TYPE_2
        from src.accounts.models import User

        user = User.objects.create_user(username="kps", email="kps@example.com", password="pass")
        competition = Competition.objects.create(fifa_id=9100, name="KPS Cup")
        season = Season.objects.create(
            fifa_id=9100,
            competition=competition,
            name="KPS Season",
            year=2026,
            start_date="2026-06-01",
            end_date="2026-07-30",
        )
        return Pool.objects.create(
            name="KPS Pool",
            slug="kps-pool",
            season=season,
            created_by=user,
            requires_payment=False,
            pool_type=POOL_TYPE_2,
        )

    def test_get_scoring_config_seeds_six_phase_rows(self):
        from src.pool.models import KNOCKOUT_PHASE_DEFAULTS

        pool = self._make_minimal_pool()
        config = pool.get_scoring_config()

        rows = {row.phase_key: row for row in config.knockout_phases.all()}
        self.assertEqual(set(rows), set(KNOCKOUT_PHASE_DEFAULTS))

        sf = rows["SF"]
        self.assertEqual(sf.exact, 78)
        self.assertEqual(sf.advancing_goals, 59)
        self.assertEqual(sf.diff, 50)
        self.assertEqual(sf.loser_goals, 44)
        self.assertEqual(sf.advancing_only, 40)

        final = rows["FINAL"]
        self.assertEqual(final.exact, 95)
        self.assertEqual(final.advancing_only, 48)
```

(Confira o import real do `User` — em outros testes do arquivo `User` já está importado no topo; se já estiver, remova o import local.)

- [ ] **Step 2: Rodar o teste e ver falhar**

Run: `PENNINICUP_SETTINGS_PROFILE=test poetry run python -m src.manage test src.pool.tests.test_pool.KnockoutPhaseScoringSeedTest -v 2`
Expected: FAIL — `ImportError`/`cannot import name 'KNOCKOUT_PHASE_DEFAULTS'` ou `knockout_phases` inexistente.

- [ ] **Step 3: Adicionar constante, modelo e helper em `models.py`**

No topo de `src/pool/models.py` (junto às outras constantes de módulo, após os imports), adicionar:

```python
KNOCKOUT_PHASE_DEFAULTS = {
    "R32": {"exact": 40, "advancing_goals": 30, "diff": 25, "loser_goals": 22, "advancing_only": 20},
    "R16": {"exact": 50, "advancing_goals": 38, "diff": 32, "loser_goals": 28, "advancing_only": 26},
    "QF": {"exact": 62, "advancing_goals": 47, "diff": 40, "loser_goals": 35, "advancing_only": 32},
    "SF": {"exact": 78, "advancing_goals": 59, "diff": 50, "loser_goals": 44, "advancing_only": 40},
    "FINAL": {"exact": 95, "advancing_goals": 72, "diff": 60, "loser_goals": 53, "advancing_only": 48},
    "THIRD": {"exact": 55, "advancing_goals": 41, "diff": 35, "loser_goals": 30, "advancing_only": 27},
}
```

Após a definição de `PoolScoringConfig` (depois da linha `models.py:477`, o `__str__`), adicionar o modelo:

```python
class PoolKnockoutPhaseScoring(models.Model):
    PHASE_CHOICES = [
        ("R32", "32 Avos"),
        ("R16", "Oitavas"),
        ("QF", "Quartas"),
        ("SF", "Semifinal"),
        ("FINAL", "Final"),
        ("THIRD", "Disputa de 3o lugar"),
    ]

    config = models.ForeignKey(PoolScoringConfig, on_delete=models.CASCADE, related_name="knockout_phases")
    phase_key = models.CharField(max_length=5, choices=PHASE_CHOICES)

    exact = models.PositiveSmallIntegerField(default=40)
    advancing_goals = models.PositiveSmallIntegerField(default=30)
    diff = models.PositiveSmallIntegerField(default=25)
    loser_goals = models.PositiveSmallIntegerField(default=22)
    advancing_only = models.PositiveSmallIntegerField(default=20)

    class Meta:
        verbose_name = "Faixa de mata-mata por fase"
        verbose_name_plural = "Faixas de mata-mata por fase"
        unique_together = ("config", "phase_key")

    def __str__(self):
        return f"{self.config.pool.slug}:{self.phase_key}"


def ensure_knockout_phase_rows(config):
    """Garante as 6 linhas de faixa por fase para uma PoolScoringConfig."""
    for phase_key, values in KNOCKOUT_PHASE_DEFAULTS.items():
        PoolKnockoutPhaseScoring.objects.get_or_create(config=config, phase_key=phase_key, defaults=values)
```

Alterar `Pool.get_scoring_config` (`models.py:101-103`) de:

```python
    def get_scoring_config(self):
        config, _ = PoolScoringConfig.objects.get_or_create(pool=self)
        return config
```

para:

```python
    def get_scoring_config(self):
        config, created = PoolScoringConfig.objects.get_or_create(pool=self)
        if created:
            ensure_knockout_phase_rows(config)
        return config
```

- [ ] **Step 4: Gerar a migration de schema**

Run: `poetry run python -m src.manage makemigrations pool`
Expected: cria `src/pool/migrations/0017_poolknockoutphasescoring.py` com `CreateModel`. (Se o nome divergir, ajuste as referências nos próximos passos.)

- [ ] **Step 5: Criar a data migration que popula configs existentes**

Create `src/pool/migrations/0018_seed_knockout_phase_scoring.py`:

```python
from django.db import migrations

KNOCKOUT_PHASE_DEFAULTS = {
    "R32": {"exact": 40, "advancing_goals": 30, "diff": 25, "loser_goals": 22, "advancing_only": 20},
    "R16": {"exact": 50, "advancing_goals": 38, "diff": 32, "loser_goals": 28, "advancing_only": 26},
    "QF": {"exact": 62, "advancing_goals": 47, "diff": 40, "loser_goals": 35, "advancing_only": 32},
    "SF": {"exact": 78, "advancing_goals": 59, "diff": 50, "loser_goals": 44, "advancing_only": 40},
    "FINAL": {"exact": 95, "advancing_goals": 72, "diff": 60, "loser_goals": 53, "advancing_only": 48},
    "THIRD": {"exact": 55, "advancing_goals": 41, "diff": 35, "loser_goals": 30, "advancing_only": 27},
}


def seed_phase_rows(apps, schema_editor):
    PoolScoringConfig = apps.get_model("pool", "PoolScoringConfig")
    PoolKnockoutPhaseScoring = apps.get_model("pool", "PoolKnockoutPhaseScoring")
    for config in PoolScoringConfig.objects.all():
        for phase_key, values in KNOCKOUT_PHASE_DEFAULTS.items():
            PoolKnockoutPhaseScoring.objects.get_or_create(config=config, phase_key=phase_key, defaults=values)


def unseed_phase_rows(apps, schema_editor):
    PoolKnockoutPhaseScoring = apps.get_model("pool", "PoolKnockoutPhaseScoring")
    PoolKnockoutPhaseScoring.objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [
        ("pool", "0017_poolknockoutphasescoring"),
    ]

    operations = [
        migrations.RunPython(seed_phase_rows, unseed_phase_rows),
    ]
```

- [ ] **Step 6: Aplicar migrations**

Run: `poetry run python -m src.manage migrate pool`
Expected: `0017` e `0018` aplicadas sem erro.

- [ ] **Step 7: Registrar o inline no admin**

Em `src/pool/admin.py`, importar o modelo novo no bloco de imports (junto a `PoolScoringConfig`, hoje `admin.py:21`):

```python
    PoolKnockoutPhaseScoring,
```

Antes de `@admin.register(PoolScoringConfig)` (`admin.py:303`), adicionar:

```python
class PoolKnockoutPhaseScoringInline(admin.TabularInline):
    model = PoolKnockoutPhaseScoring
    extra = 0
    fields = ("phase_key", "exact", "advancing_goals", "diff", "loser_goals", "advancing_only")
```

E na classe `PoolScoringConfigAdmin` adicionar o atributo:

```python
    inlines = [PoolKnockoutPhaseScoringInline]
```

- [ ] **Step 8: Rodar o teste e ver passar**

Run: `PENNINICUP_SETTINGS_PROFILE=test poetry run python -m src.manage test src.pool.tests.test_pool.KnockoutPhaseScoringSeedTest -v 2`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add src/pool/models.py src/pool/admin.py src/pool/migrations/0017_poolknockoutphasescoring.py src/pool/migrations/0018_seed_knockout_phase_scoring.py src/pool/tests/test_pool.py
git commit -m "feat(pool): modelo PoolKnockoutPhaseScoring com faixas por fase"
```

______________________________________________________________________

### Task 2: Scoring engine lê faixa por fase (Tipo 2)

**Files:**

- Modify: `src/pool/services/scoring.py` (`scoring.py:1` import; `scoring.py:28-55` helper; `scoring.py:58` assinatura; `scoring.py:119-141` branch Tipo 2)
- Test: `src/pool/tests/test_pool.py`

**Interfaces:**

- Consumes: `KNOCKOUT_PHASE_DEFAULTS` (Task 1).

- Produces:

  - `calculate_bet_points(bet, scoring_config, pool_type=None, predicted_advancing_id=None, knockout_phase_scoring=None)` — `knockout_phase_scoring` é `dict[str, obj]` `{phase_key: linha-com-atributos exact/advancing_goals/diff/loser_goals/advancing_only}`.
  - `_knockout_points_by_score(tier, home, away, guess_home, guess_away)` — agora recebe `tier` (objeto com os 5 atributos) em vez de `scoring_config`.

- [ ] **Step 1: Escrever o teste que falha**

Em `src/pool/tests/test_pool.py`, dentro de `class ScoringCalculateBetPointsTest(SimpleTestCase)`, adicionar um helper de mapa por fase e um param de stage no bet de mata-mata. Primeiro, adicionar o helper logo após `_make_scoring_config`:

```python
    def _make_phase_scoring(self):
        from src.pool.models import KNOCKOUT_PHASE_DEFAULTS

        return {key: SimpleNamespace(**values) for key, values in KNOCKOUT_PHASE_DEFAULTS.items()}
```

Adicionar um helper paralelo ao `_make_knockout_bet` que permite escolher a fase (não altere o original para não quebrar os testes existentes):

```python
    def _make_knockout_bet_phase(
        self, home_pred, away_pred, home_real, away_real, *, stage_name,
        winner_real_id=None, winner_pred_id=None, home_team_id=1,
    ):
        stage = SimpleNamespace(name=stage_name)
        match = SimpleNamespace(
            stage=stage, home_score=home_real, away_score=away_real,
            winner_id=winner_real_id, home_team_id=home_team_id, away_team_id=2,
        )
        return SimpleNamespace(
            is_active=True, home_score_pred=home_pred, away_score_pred=away_pred,
            winner_pred_id=winner_pred_id, match=match,
        )
```

Agora os testes de faixa por fase:

```python
    def test_tipo2_final_exact_uses_final_tier(self):
        from src.pool.services.rules import POOL_TYPE_2

        bet = self._make_knockout_bet_phase(2, 1, 2, 1, stage_name="Final", winner_real_id=1)
        result = calculate_bet_points(
            bet, self._make_scoring_config(), pool_type=POOL_TYPE_2,
            predicted_advancing_id=1, knockout_phase_scoring=self._make_phase_scoring(),
        )
        self.assertEqual(result["points"], 95)
        self.assertTrue(result["exact_score"])
        self.assertTrue(result["advancing_correct"])

    def test_tipo2_r32_exact_uses_r32_tier(self):
        from src.pool.services.rules import POOL_TYPE_2

        bet = self._make_knockout_bet_phase(2, 1, 2, 1, stage_name="R32", winner_real_id=1)
        result = calculate_bet_points(
            bet, self._make_scoring_config(), pool_type=POOL_TYPE_2,
            predicted_advancing_id=1, knockout_phase_scoring=self._make_phase_scoring(),
        )
        self.assertEqual(result["points"], 40)

    def test_tipo2_final_scores_more_than_r32_same_guess(self):
        from src.pool.services.rules import POOL_TYPE_2

        phases = self._make_phase_scoring()
        cfg = self._make_scoring_config()
        final_bet = self._make_knockout_bet_phase(2, 0, 2, 0, stage_name="Final", winner_real_id=1)
        r32_bet = self._make_knockout_bet_phase(2, 0, 2, 0, stage_name="R32", winner_real_id=1)
        final_pts = calculate_bet_points(
            final_bet, cfg, pool_type=POOL_TYPE_2, predicted_advancing_id=1,
            knockout_phase_scoring=phases,
        )["points"]
        r32_pts = calculate_bet_points(
            r32_bet, cfg, pool_type=POOL_TYPE_2, predicted_advancing_id=1,
            knockout_phase_scoring=phases,
        )["points"]
        self.assertEqual(final_pts, 72)  # FINAL advancing_goals
        self.assertEqual(r32_pts, 30)    # R32 advancing_goals
        self.assertGreater(final_pts, r32_pts)

    def test_tipo2_wrong_classified_zero_even_in_final(self):
        from src.pool.services.rules import POOL_TYPE_2

        bet = self._make_knockout_bet_phase(2, 1, 2, 1, stage_name="Final", winner_real_id=2)
        result = calculate_bet_points(
            bet, self._make_scoring_config(), pool_type=POOL_TYPE_2,
            predicted_advancing_id=1, knockout_phase_scoring=self._make_phase_scoring(),
        )
        self.assertEqual(result["points"], 0)
        self.assertFalse(result["advancing_correct"])

    def test_tipo2_example_wrong_opponent_right_classified_scores_full(self):
        # Real Marrocos(1) x Holanda(2): away advances. Palpite Brasil x Holanda 1x2.
        # Classificado (away, id=2) == real winner (id=2) → exato da fase (QF).
        from src.pool.services.rules import POOL_TYPE_2

        bet = self._make_knockout_bet_phase(
            1, 2, 1, 2, stage_name="Quartas", winner_real_id=2,
        )
        result = calculate_bet_points(
            bet, self._make_scoring_config(), pool_type=POOL_TYPE_2,
            predicted_advancing_id=2, knockout_phase_scoring=self._make_phase_scoring(),
        )
        self.assertEqual(result["points"], 62)  # QF exact
        self.assertTrue(result["exact_score"])

    def test_tipo2_fallback_to_flat_when_no_phase_map(self):
        # Sem knockout_phase_scoring → usa campos flat (retrocompatível).
        from src.pool.services.rules import POOL_TYPE_2

        bet = self._make_knockout_bet_phase(2, 1, 2, 1, stage_name="Final", winner_real_id=1)
        result = calculate_bet_points(
            bet, self._make_scoring_config(), pool_type=POOL_TYPE_2,
            predicted_advancing_id=1,
        )
        self.assertEqual(result["points"], 35)  # knockout_exact_and_advancing flat
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `PENNINICUP_SETTINGS_PROFILE=test poetry run python -m src.manage test src.pool.tests.test_pool.ScoringCalculateBetPointsTest -v 2`
Expected: FAIL — os novos testes falham (`calculate_bet_points() got an unexpected keyword argument 'knockout_phase_scoring'`).

- [ ] **Step 3: Refatorar `scoring.py`**

Trocar o import do topo (`scoring.py:1`) para incluir `normalize_stage_key` e `SimpleNamespace`:

```python
from types import SimpleNamespace

from src.pool.services.rules import PHASE_GROUP, POOL_TYPE_2, normalize_stage_key, phase_for_match
```

Trocar a assinatura e o corpo de `_knockout_points_by_score` (`scoring.py:28-55`) para ler de `tier`:

```python
def _knockout_points_by_score(tier, home, away, guess_home, guess_away):
    """Faixa de pontos do mata-mata pelo placar (posicional), assumindo classificado correto.

    `tier` é um objeto com os atributos exact/advancing_goals/diff/loser_goals/advancing_only.
    Retorna (points, is_exact, advancing_goals, diff_correct, eliminated_goals).
    """
    is_exact = guess_home == home and guess_away == away
    if is_exact:
        return tier.exact, True, False, False, False

    is_diff = (guess_home - guess_away) == (home - away)

    if home == away:
        # Empate real (decidido nos pênaltis): sem vencedor posicional.
        if is_diff:
            return tier.diff, False, False, True, False
        return tier.advancing_only, False, False, False, False

    actual_direction = _winner_from_score(home, away)
    winner_goals = _is_winner_goals_correct(actual_direction, guess_home, guess_away, home, away)
    loser_goals = _is_loser_goals_correct(actual_direction, guess_home, guess_away, home, away)

    if winner_goals:
        return tier.advancing_goals, False, True, False, False
    if is_diff:
        return tier.diff, False, False, True, False
    if loser_goals:
        return tier.loser_goals, False, False, False, True
    return tier.advancing_only, False, False, False, False
```

Adicionar, logo após esse helper, um construtor de tier a partir dos campos flat:

```python
def _tier_from_flat_config(scoring_config):
    return SimpleNamespace(
        exact=scoring_config.knockout_exact_and_advancing,
        advancing_goals=scoring_config.knockout_advancing_and_winner_goals,
        diff=scoring_config.knockout_advancing_and_diff,
        loser_goals=scoring_config.knockout_advancing_and_loser_goals,
        advancing_only=scoring_config.knockout_advancing_only,
    )
```

Trocar a assinatura de `calculate_bet_points` (`scoring.py:58`):

```python
def calculate_bet_points(
    bet, scoring_config, pool_type=None, predicted_advancing_id=None, knockout_phase_scoring=None
):
```

Trocar o branch Tipo 2 (`scoring.py:119-141`) para resolver a fase e o tier:

```python
    # KNOCKOUT Tipo 2: gate por classificado (identidade do time), não por posição.
    if pool_type == POOL_TYPE_2:
        is_advancing_correct = bool(match.winner_id) and predicted_advancing_id == match.winner_id
        if not is_advancing_correct:
            return {
                "points": 0,
                "exact_score": is_exact_score,
                "advancing_correct": False,
                "advancing_goals_correct": False,
                "diff_correct": False,
                "eliminated_goals_correct": False,
            }
        stage_key = normalize_stage_key(match.stage)
        tier = (knockout_phase_scoring or {}).get(stage_key)
        if tier is None:
            # Fallback retrocompatível: pool sem faixas por fase usa os campos flat.
            tier = _tier_from_flat_config(scoring_config)
        points, is_exact, advancing_goals, diff_correct, eliminated_goals = _knockout_points_by_score(
            tier, home, away, guess_home, guess_away
        )
        return {
            "points": points,
            "exact_score": is_exact,
            "advancing_correct": True,
            "advancing_goals_correct": advancing_goals,
            "diff_correct": diff_correct,
            "eliminated_goals_correct": eliminated_goals,
        }
```

(O branch posicional Tipo 1, `scoring.py:143` em diante, não muda — continua lendo `scoring_config.knockout_*`.)

- [ ] **Step 4: Rodar os testes novos e ver passar**

Run: `PENNINICUP_SETTINGS_PROFILE=test poetry run python -m src.manage test src.pool.tests.test_pool.ScoringCalculateBetPointsTest -v 2`
Expected: PASS (novos + os antigos de Tipo 2 — que não passam `knockout_phase_scoring` e caem no fallback flat 35/25/20/17/15 — todos verdes).

- [ ] **Step 5: Commit**

```bash
git add src/pool/services/scoring.py src/pool/tests/test_pool.py
git commit -m "feat(pool): scoring de mata-mata Tipo 2 por fase (fallback flat)"
```

______________________________________________________________________

### Task 3: Call sites repassam o mapa de faixas por fase

**Files:**

- Modify: `src/pool/services/ranking.py` (`ranking.py:144-198`)
- Modify: `src/pool/services/asof_standings.py` (`asof_standings.py:141-197`)
- Modify: `src/rankings/management/commands/diagnose_dashboard.py` (`diagnose_dashboard.py:96-123`)
- Test: `src/pool/tests/test_pool.py`

**Interfaces:**

- Consumes: `calculate_bet_points(..., knockout_phase_scoring=...)` (Task 2); `config.knockout_phases` (Task 1).

- Produces: comportamento — pools Tipo 2 recalculadas usam os valores por fase.

- [ ] **Step 1: Escrever o teste de integração que falha**

Em `src/pool/tests/test_pool.py`, adicionar nova classe (usa o padrão de fixture de `RecalculateTipo2KnockoutTest`, `test_pool.py:3275`):

```python
class RecalculateTipo2PhaseTierTest(TestCase):
    """recalculate_participant_scores usa a faixa da fase (SF) no Tipo 2."""

    def _build_sf_pool(self):
        user = User.objects.create_user(username="t2sf", email="t2sf@example.com", password="pass")
        competition = Competition.objects.create(fifa_id=8201, name="Copa T2 SF")
        season = Season.objects.create(
            fifa_id=8201,
            competition=competition,
            name="T2 SF Season",
            year=2026,
            start_date="2026-06-01",
            end_date="2026-07-30",
        )
        stage_sf = Stage.objects.create(fifa_id="SF-PT", season=season, name="SF", order=80)
        team_a = Team.objects.create(fifa_id="PT-A", name="PT Alpha", name_norm="pt-alpha", code="PTA")
        team_b = Team.objects.create(fifa_id="PT-B", name="PT Beta", name_norm="pt-beta", code="PTB")

        past = timezone.now() - timezone.timedelta(hours=2)
        match = Match.objects.create(
            fifa_id="PT-SF-1",
            season=season,
            stage=stage_sf,
            match_number=100,
            match_date_utc=past,
            match_date_local=past,
            match_date_brasilia=past,
            home_team=team_a,
            away_team=team_b,
            home_score=2,
            away_score=0,
            winner=team_a,
            status=Match.STATUS_FINISHED,
        )
        pool = Pool.objects.create(
            name="Pool T2 SF",
            slug="pool-t2-sf",
            season=season,
            created_by=user,
            requires_payment=False,
            pool_type=POOL_TYPE_2,
        )
        participant = PoolParticipant.objects.create(pool=pool, user=user, is_active=True)
        # Placar exato 2x0, classificado team_a (== real winner) → SF exact = 78
        bet = PoolBet.objects.create(
            participant=participant,
            match=match,
            home_score_pred=2,
            away_score_pred=0,
            winner_pred=team_a,
            is_active=True,
        )
        return {"participant": participant, "bet": bet}

    def test_recalculate_uses_sf_exact_tier(self):
        from src.pool.models import PoolBetScore

        ctx = self._build_sf_pool()
        recalculate_participant_scores(ctx["participant"])
        score = PoolBetScore.objects.get(bet=ctx["bet"])
        self.assertEqual(score.points, 78)
        self.assertTrue(score.exact_score)
        self.assertTrue(score.advancing_correct)
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `PENNINICUP_SETTINGS_PROFILE=test poetry run python -m src.manage test src.pool.tests.test_pool.RecalculateTipo2PhaseTierTest -v 2`
Expected: FAIL — `points` == 35 (fallback flat), esperado 78.

- [ ] **Step 3: Wire `ranking.py`**

Em `recalculate_participant_scores` (`ranking.py:144`), dentro do bloco `if pool_type == POOL_TYPE_2:` (`ranking.py:153-170`), após resolver `advancing_map`, montar o mapa de fases. Adicionar logo após `scoring_config = scoring_config or ...` (`ranking.py:145`) uma resolução única:

```python
    knockout_phase_scoring = None
    if pool_type == POOL_TYPE_2:
        knockout_phase_scoring = {row.phase_key: row for row in scoring_config.knockout_phases.all()}
```

(Coloque essas linhas após `pool_type = participant.pool.pool_type` em `ranking.py:147`, antes de `bets = list(...)`.)

E na chamada `calculate_bet_points` (`ranking.py:193-198`), acrescentar o argumento:

```python
        score_data = calculate_bet_points(
            bet,
            scoring_config=scoring_config,
            pool_type=pool_type,
            predicted_advancing_id=advancing_map.get(bet.match_id),
            knockout_phase_scoring=knockout_phase_scoring,
        )
```

- [ ] **Step 4: Wire `asof_standings.py`**

Em `compute_asof_standings` (`asof_standings.py:141`), após `pool_type = pool.pool_type` (`asof_standings.py:149`), montar o mapa uma vez:

```python
    knockout_phase_scoring = None
    if pool_type == POOL_TYPE_2:
        knockout_phase_scoring = {row.phase_key: row for row in scoring_config.knockout_phases.all()}
```

E na chamada `calculate_bet_points` (`asof_standings.py:192-197`), acrescentar:

```python
            score_data = calculate_bet_points(
                bet,
                scoring_config=scoring_config,
                pool_type=pool_type,
                predicted_advancing_id=advancing_map.get(bet.match_id),
                knockout_phase_scoring=knockout_phase_scoring,
            )
```

- [ ] **Step 5: Wire `diagnose_dashboard.py`**

Em `diagnose_dashboard.py`, após `cfg = pool.get_scoring_config()` (`diagnose_dashboard.py:53`), montar o mapa:

```python
        knockout_phase_scoring = None
        if pool.pool_type == POOL_TYPE_2:
            knockout_phase_scoring = {row.phase_key: row for row in cfg.knockout_phases.all()}
```

E na chamada `calculate_bet_points` (`diagnose_dashboard.py:118-123`), acrescentar `knockout_phase_scoring=knockout_phase_scoring,`.

- [ ] **Step 6: Rodar o teste novo + a suíte Tipo 2 existente**

Run: `PENNINICUP_SETTINGS_PROFILE=test poetry run python -m src.manage test src.pool.tests.test_pool.RecalculateTipo2PhaseTierTest src.pool.tests.test_pool.RecalculateTipo2KnockoutTest src.pool.tests.test_pool.RecalculateTipo2KnockoutR16CascadeTest -v 2`
Expected: PASS (o novo dá 78; os antigos seguem verdes — só checam `>0`/`==0`).

- [ ] **Step 7: Commit**

```bash
git add src/pool/services/ranking.py src/pool/services/asof_standings.py src/rankings/management/commands/diagnose_dashboard.py src/pool/tests/test_pool.py
git commit -m "feat(pool): call sites repassam faixas por fase no recálculo Tipo 2"
```

______________________________________________________________________

### Task 4: Utilização do dashboard ciente de fase no Tipo 2

**Files:**

- Modify: `src/rankings/services/dashboard.py` (`dashboard.py:132-147`)
- Test: `src/pool/tests/test_pool.py` (ou `src/rankings/tests.py` se preferir; manter junto dos demais por simplicidade)

**Interfaces:**

- Consumes: `config.knockout_phases` (Task 1).
- Produces: `_match_max_points(match, scoring_config, phase_max_map=None)` — denominador de utilização usa o `exact` da fase quando há mapa.

**Contexto:** hoje `_match_max_points` (`dashboard.py:132-135`) usa o campo flat `knockout_exact_and_advancing` (35) como teto de qualquer jogo de mata-mata. No Tipo 2 um jogo pode valer até 95 (FINAL), então o denominador fica menor que o ponto real obtido → utilização pode passar de 100%. Corrigir usando o `exact` da fase.

- [ ] **Step 1: Escrever o teste que falha**

```python
class MatchMaxPointsPhaseTest(SimpleTestCase):
    def test_phase_map_overrides_flat_knockout_max(self):
        from src.rankings.services.dashboard import _match_max_points

        scoring_config = SimpleNamespace(group_exact_score=25, knockout_exact_and_advancing=35)
        final_stage = SimpleNamespace(name="Final")
        match = SimpleNamespace(stage=final_stage, group_id=None)
        phase_max_map = {"FINAL": 95, "R32": 40}

        self.assertEqual(_match_max_points(match, scoring_config, phase_max_map), 95)

    def test_no_phase_map_uses_flat(self):
        from src.rankings.services.dashboard import _match_max_points

        scoring_config = SimpleNamespace(group_exact_score=25, knockout_exact_and_advancing=35)
        final_stage = SimpleNamespace(name="Final")
        match = SimpleNamespace(stage=final_stage, group_id=None)

        self.assertEqual(_match_max_points(match, scoring_config), 35)
```

(Confirme que `phase_for_match` enxerga `group_id`/`stage` desses `SimpleNamespace`. `phase_for_match` usa `normalize_stage_key(match.stage)`; "Final" → não-GROUP, então cai no ramo de mata-mata. `group_id` não é usado por `phase_for_match`, mas mantemos no fake por clareza.)

- [ ] **Step 2: Rodar e ver falhar**

Run: `PENNINICUP_SETTINGS_PROFILE=test poetry run python -m src.manage test src.pool.tests.test_pool.MatchMaxPointsPhaseTest -v 2`
Expected: FAIL — `_match_max_points() takes 2 positional arguments but 3 were given`.

- [ ] **Step 3: Atualizar `_match_max_points` e o chamador**

Trocar `_match_max_points` (`dashboard.py:132-135`):

```python
def _match_max_points(match, scoring_config, phase_max_map=None):
    if phase_for_match(match) == PHASE_GROUP:
        return scoring_config.group_exact_score if scoring_config else _DEFAULT_GROUP_MAX
    if phase_max_map:
        stage_key = normalize_stage_key(match.stage)
        phase_max = phase_max_map.get(stage_key)
        if phase_max is not None:
            return phase_max
    return scoring_config.knockout_exact_and_advancing if scoring_config else _DEFAULT_KNOCKOUT_MAX
```

Garantir o import de `normalize_stage_key` no topo de `dashboard.py` (ele já importa de `src.pool.services.rules`; adicionar `normalize_stage_key` à lista). Verifique a linha de import existente e acrescente o nome.

Em `_utilization_inputs` (`dashboard.py:138-147`), montar o mapa de teto por fase só para Tipo 2 e passá-lo:

```python
    scoring_config = getattr(pool, "scoring_config", None)
    phase_max_map = None
    if scoring_config is not None and getattr(pool, "pool_type", None) == POOL_TYPE_2:
        phase_max_map = {row.phase_key: row.exact for row in scoring_config.knockout_phases.all()}
    denominator = sum(_match_max_points(match, scoring_config, phase_max_map) for match in finished_matches)
```

Adicionar `POOL_TYPE_2` ao import de `src.pool.services.rules` no topo de `dashboard.py` se ainda não estiver.

- [ ] **Step 4: Rodar e ver passar**

Run: `PENNINICUP_SETTINGS_PROFILE=test poetry run python -m src.manage test src.pool.tests.test_pool.MatchMaxPointsPhaseTest -v 2`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/rankings/services/dashboard.py src/pool/tests/test_pool.py
git commit -m "fix(rankings): teto de utilização por fase no Tipo 2 (evita >100%)"
```

______________________________________________________________________

### Task 5: Documentação `SCORE.md`

**Files:**

- Modify: `src/SCORE.md` (`SCORE.md:43-55` subseção do Tipo 2)

- [ ] **Step 1: Rodar a suíte pool + rankings (baseline verde)**

Run: `PENNINICUP_SETTINGS_PROFILE=test poetry run python -m src.manage test src.pool src.rankings -v 1`
Expected: PASS (sem regressões; tracebacks logados de error-paths são esperados, resultado final OK).

- [ ] **Step 2: Reescrever a subseção do Tipo 2**

Substituir o conteúdo de "### Mata-mata no Tipo 2 (palpite progressivo)" (`SCORE.md:43-55`) por:

```markdown
### Mata-mata no Tipo 2 (palpite progressivo)

No bolão **Tipo 2** o mata-mata é pontuado **pelo classificado** (identidade do
time que o participante projetou para o jogo vs. `match.winner` real) e a faixa
de placar **escala por fase** — quanto mais avançado o jogo, mais vale.

Regra do gate (por jogo, por identidade — **sem cascata, sem olhar fases
passadas ou futuras**):

- **Classificado errado → 0**, mesmo com placar exato. É o gate, não acumula,
  não há consolação.
- O time **eliminado** do confronto é irrelevante: acertar quem avança e errar
  o adversário pontua cheio (ex.: real Marrocos 1×2 Holanda, palpite
  Brasil 1×2 Holanda → classificado Holanda correto, placar exato → faixa cheia
  da fase).
- **Classificado certo →** aplica-se a faixa da fase do jogo (tabela abaixo).

Faixas de placar por fase (exato / gols do classificado / diferença / gols do
eliminado / só o classificado):

| Fase  | exato | gols-classif | dif | gols-elim | só-classif |
| ----- | ----- | ------------ | --- | --------- | ---------- |
| R32   | 40    | 30           | 25  | 22        | 20         |
| R16   | 50    | 38           | 32  | 28        | 26         |
| QF    | 62    | 47           | 40  | 35        | 32         |
| SF    | 78    | 59           | 50  | 44        | 40         |
| FINAL | 95    | 72           | 60  | 53        | 48         |
| THIRD | 55    | 41           | 35  | 30        | 27         |

**Sem bônus de classificado separado** — a recompensa por acertar quem avança já
está embutida na faixa (`só o classificado` é o piso). Acertar o classificado da
FINAL = acertar o campeão, que dispara o **bônus de campeão** (120), mecanismo de
torneio à parte que acumula.

Real empate decidido nos pênaltis (Tipo 2, classificado certo): placar exato =
`exato` da fase / mesma diferença (0) = `dif` da fase / senão = `só-classif` da
fase.
```

- [ ] **Step 3: Lint**

Run: `poetry run pre-commit run --all-files`
Expected: PASS (mdformat pode reformatar `SCORE.md`; re-stage e repita se um hook modificar arquivos).

- [ ] **Step 4: Commit**

```bash
git add src/SCORE.md
git commit -m "docs(pool): mata-mata do Tipo 2 com faixas por fase no SCORE.md"
```

______________________________________________________________________

## Self-Review

**Spec coverage:**

- Faixas por fase (modelo + valores) → Task 1 (modelo/migração) + Task 2 (engine). ✅
- Gate inalterado → Task 2 mantém `predicted_advancing_id == match.winner_id`. ✅
- Sem bônus de classificado → nenhum campo de bônus criado; documentado Task 5. ✅
- Empate-pênaltis reusa faixas da fase → Task 2 (`home == away` no helper). ✅
- THIRD faixa própria → incluída em `KNOCKOUT_PHASE_DEFAULTS` (Task 1) e testável. ✅
- Tipo 1 intacto → branch posicional não tocado (Task 2 nota explícita). ✅
- Call sites repassam o mapa → Task 3. ✅
- Migração de dados para configs existentes → Task 1 Step 5. ✅
- Docs SCORE.md (remover "eliminado antes") → Task 5. ✅
- Extra (não no spec, mas correção necessária): utilização >100% no dashboard → Task 4.

**Placeholder scan:** sem TBD/TODO; todo passo tem código ou comando concreto.

**Type consistency:** atributos do tier (`exact/advancing_goals/diff/loser_goals/advancing_only`) idênticos entre o modelo (Task 1), o `SimpleNamespace` de fallback/teste (Task 2) e o helper `_knockout_points_by_score` (Task 2). `knockout_phase_scoring` é sempre `{phase_key: row}` nas 3 call sites (Task 3). `phase_key` usa as chaves de `normalize_stage_key` em todo lugar.

______________________________________________________________________

## Fora de escopo

- Tipo 1 (posicional, flat).
- Mudar a resolução de classificado / `_walk_knockout_bracket`.
- Consolação por placar ao errar o classificado (gate segue duro).

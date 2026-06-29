# Tipo 2 — Placar exato com classificado errado Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** No mata-mata Tipo 2, quando o classificado palpitado está errado MAS os dois times do palpite são exatamente os dois times reais do jogo e o placar é exato, pontuar com um valor configurável por fase (`exact_wrong_advancing`) em vez de zerar.

**Architecture:** Quatro camadas. (1) `models.py`: novo campo por fase `exact_wrong_advancing` em `PoolKnockoutPhaseScoring` + defaults + migração; o campo flat morto `knockout_exact_wrong_advancing` passa a alimentar o fallback. (2) `scoring.py`: novo param `predicted_team_ids` e ramo de exceção que paga `tier.exact_wrong_advancing`. (3) `context_builder.py`: resolver que devolve times projetados + classificado num walk. (4) callers (`ranking.py`, `asof_standings.py`) passam `predicted_team_ids`.

**Tech Stack:** Python 3.12, Django 6, testes `unittest` (Django test runner).

## Global Constraints

- Rodar testes com profile `test`. Bash: `PENNINICUP_SETTINGS_PROFILE=test poetry run python -m src.manage test <dotted.path> -v 2`. PowerShell: `$env:PENNINICUP_SETTINGS_PROFILE='test'; poetry run python -m src.manage test <dotted.path> -v 2`.
- Não usar `DJANGO_SETTINGS_PROFILE` (CLAUDE.md errado nesse ponto; correto é `PENNINICUP_SETTINGS_PROFILE`).
- A exceção paga um valor CONFIGURÁVEL por fase: `points = tier.exact_wrong_advancing`. Sem subtração, sem piso.
- `tier` vem da fase (`knockout_phase_scoring[stage]`, linha `PoolKnockoutPhaseScoring`), com fallback `_tier_from_flat_config` (usa o campo flat `knockout_exact_wrong_advancing`).
- `predicted_team_ids` default `None` → exceção NÃO dispara (retrocompat).
- Defaults out-of-box de `exact_wrong_advancing` por fase = `exact − advancing_only` (admin configura à vontade).
- Não tocar Tipo 1 nem fase de grupos. Não editar migrações históricas (0017/0018/0019).
- Lint: ruff, linha máx. 119 (`make lint`); ruff format pode quebrar dicts longos — aceitar.

______________________________________________________________________

### Task 1: Campo por fase `exact_wrong_advancing` (model + defaults + admin + migração)

**Files:**

- Modify: `src/pool/models.py` (`KNOCKOUT_PHASE_DEFAULTS` em `:22-29`; `PoolKnockoutPhaseScoring` em `:491-519`)
- Create: `src/pool/migrations/0020_poolknockoutphasescoring_exact_wrong_advancing.py`
- Modify: `src/pool/admin.py` (`PoolKnockoutPhaseScoringInline.fields` em `:307`)
- Test: `src/pool/tests/test_pool.py` (estender `test_get_scoring_config_seeds_six_phase_rows`, `:4370`)

**Interfaces:**

- Produces: `PoolKnockoutPhaseScoring.exact_wrong_advancing` (PositiveSmallIntegerField, sem default no model). `KNOCKOUT_PHASE_DEFAULTS[phase]["exact_wrong_advancing"]`. As linhas (objetos `PoolKnockoutPhaseScoring`) usadas como `tier` em `scoring.py` passam a expor `.exact_wrong_advancing`.

- [ ] **Step 1: Estender o teste de seed (falha)**

Em `src/pool/tests/test_pool.py`, dentro de `test_get_scoring_config_seeds_six_phase_rows` (`:4378-4388`), adicionar asserts do novo campo:

```
        sf = rows["SF"]
        self.assertEqual(sf.exact, 78)
        self.assertEqual(sf.advancing_goals, 59)
        self.assertEqual(sf.diff, 50)
        self.assertEqual(sf.loser_goals, 44)
        self.assertEqual(sf.advancing_only, 40)
        self.assertEqual(sf.exact_wrong_advancing, 38)

        final = rows["FINAL"]
        self.assertEqual(final.exact, 95)
        self.assertEqual(final.advancing_only, 48)
        self.assertEqual(final.exact_wrong_advancing, 47)
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `PENNINICUP_SETTINGS_PROFILE=test poetry run python -m src.manage test src.pool.tests.test_pool.PoolScoringConfigSeedTest.test_get_scoring_config_seeds_six_phase_rows -v 2`
(Se o nome da classe diferir, rodar o módulo: `... test src.pool.tests.test_pool -v 2` e localizar o teste.)
Expected: FAIL com `AttributeError: 'PoolKnockoutPhaseScoring' object has no attribute 'exact_wrong_advancing'` (ou erro de migração faltante).

- [ ] **Step 3: Adicionar o campo ao model**

Em `src/pool/models.py`, na classe `PoolKnockoutPhaseScoring`, após `advancing_only = models.PositiveSmallIntegerField()` (`:511`):

```
    advancing_only = models.PositiveSmallIntegerField()
    exact_wrong_advancing = models.PositiveSmallIntegerField()
```

- [ ] **Step 4: Adicionar o default por fase**

Em `src/pool/models.py`, trocar `KNOCKOUT_PHASE_DEFAULTS` (`:22-29`) por (default = exact − advancing_only por fase):

```
KNOCKOUT_PHASE_DEFAULTS = {
    "R32": {"exact": 40, "advancing_goals": 30, "diff": 25, "loser_goals": 22, "advancing_only": 20, "exact_wrong_advancing": 20},
    "R16": {"exact": 50, "advancing_goals": 38, "diff": 32, "loser_goals": 28, "advancing_only": 26, "exact_wrong_advancing": 24},
    "QF": {"exact": 62, "advancing_goals": 47, "diff": 40, "loser_goals": 35, "advancing_only": 32, "exact_wrong_advancing": 30},
    "SF": {"exact": 78, "advancing_goals": 59, "diff": 50, "loser_goals": 44, "advancing_only": 40, "exact_wrong_advancing": 38},
    "FINAL": {"exact": 95, "advancing_goals": 72, "diff": 60, "loser_goals": 53, "advancing_only": 48, "exact_wrong_advancing": 47},
    "THIRD": {"exact": 55, "advancing_goals": 41, "diff": 35, "loser_goals": 30, "advancing_only": 27, "exact_wrong_advancing": 28},
}
```

(Linhas >119 chars: rodar `poetry run ruff format src/pool/models.py` e aceitar a quebra automática.)

- [ ] **Step 5: Adicionar ao admin inline**

Em `src/pool/admin.py`, trocar (`:307`):

```
    fields = ("phase_key", "exact", "advancing_goals", "diff", "loser_goals", "advancing_only")
```

por:

```
    fields = ("phase_key", "exact", "advancing_goals", "diff", "loser_goals", "advancing_only", "exact_wrong_advancing")
```

- [ ] **Step 6: Criar a migração**

Criar `src/pool/migrations/0020_poolknockoutphasescoring_exact_wrong_advancing.py`:

```
from django.db import migrations, models


def populate_exact_wrong_advancing(apps, schema_editor):
    PoolKnockoutPhaseScoring = apps.get_model("pool", "PoolKnockoutPhaseScoring")
    for row in PoolKnockoutPhaseScoring.objects.all():
        row.exact_wrong_advancing = max(row.exact - row.advancing_only, 0)
        row.save(update_fields=["exact_wrong_advancing"])


class Migration(migrations.Migration):
    dependencies = [
        ("pool", "0019_alter_poolknockoutphasescoring_advancing_goals_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="poolknockoutphasescoring",
            name="exact_wrong_advancing",
            field=models.PositiveSmallIntegerField(default=0),
            preserve_default=False,
        ),
        migrations.RunPython(populate_exact_wrong_advancing, migrations.RunPython.noop),
    ]
```

- [ ] **Step 7: Confirmar que não há migração pendente faltando**

Run: `PENNINICUP_SETTINGS_PROFILE=test poetry run python -m src.manage makemigrations pool --check --dry-run`
Expected: "No changes detected" (o model bate com a migração escrita à mão).

- [ ] **Step 8: Rodar o teste e confirmar que passa**

Run: `PENNINICUP_SETTINGS_PROFILE=test poetry run python -m src.manage test src.pool.tests.test_pool -v 2 2>&1 | tail -20`
Expected: o teste de seed passa (DB de teste construído da migração nova).

- [ ] **Step 9: Commit**

```
git add src/pool/models.py src/pool/admin.py src/pool/migrations/0020_poolknockoutphasescoring_exact_wrong_advancing.py src/pool/tests/test_pool.py
git commit -m "feat(pool): campo exact_wrong_advancing por fase no mata-mata tipo 2"
```

______________________________________________________________________

### Task 2: Exceção de pontuação em `scoring.py`

**Files:**

- Modify: `src/pool/services/scoring.py` (`_tier_from_flat_config` em `:66-73`; assinatura em `:76-78`; bloco `POOL_TYPE_2` em `:139-166`)
- Test: `src/pool/tests/test_pool.py` (classe `ScoringTipo2ExhaustiveUnitTest`, `SimpleTestCase`)

**Interfaces:**

- Consumes: `tier.exact_wrong_advancing` (Task 1); `scoring_config.knockout_exact_wrong_advancing` (campo flat já existente, default 10).
- Produces: `calculate_bet_points(bet, scoring_config, pool_type=None, predicted_advancing_id=None, knockout_phase_scoring=None, predicted_team_ids=None)`. Tipo 2, classificado errado, `is_exact_score` e `set(predicted_team_ids) == {match.home_team_id, match.away_team_id}` (sem `None`) → `points = tier.exact_wrong_advancing`, flags `exact_score=True`/`advancing_correct=False`.

O helper `_make_scoring_config` (`test_pool.py:3779`) tem `knockout_exact_wrong_advancing=10` e `knockout_exact_and_advancing=35`. `_make_knockout_bet` cria `match` com `home_team_id=1`, `away_team_id=2`, `stage.name="Semi-Final"` (→ `"SF"`).

- [ ] **Step 1: Escrever os testes que falham**

Adicionar dentro de `ScoringTipo2ExhaustiveUnitTest` (após `test_tipo2_knockout_exact_wrong_advancing_field_ignored`, `:3915`):

```
    # Exceção via fallback flat: classificado errado + placar exato + os dois
    # times do palpite são os reais → paga knockout_exact_wrong_advancing (10).
    def test_tipo2_exact_wrong_advancing_flat(self):
        bet = self._make_knockout_bet(1, 1, 1, 1, winner_real_id=1)
        result = calculate_bet_points(
            bet,
            self._make_scoring_config(),
            pool_type=POOL_TYPE_2,
            predicted_advancing_id=2,
            predicted_team_ids=(1, 2),
        )
        self.assertEqual(result["points"], 10)
        self.assertTrue(result["exact_score"])
        self.assertFalse(result["advancing_correct"])

    # Exceção via faixa por fase: paga tier.exact_wrong_advancing (23), provando
    # que lê o campo configurado (exact=99, advancing_only=15 não influenciam).
    def test_tipo2_exact_wrong_advancing_per_phase(self):
        tier = SimpleNamespace(
            exact=99, advancing_goals=70, diff=60, loser_goals=50,
            advancing_only=15, exact_wrong_advancing=23,
        )
        bet = self._make_knockout_bet(1, 1, 1, 1, winner_real_id=1)
        result = calculate_bet_points(
            bet,
            self._make_scoring_config(),
            pool_type=POOL_TYPE_2,
            predicted_advancing_id=2,
            knockout_phase_scoring={"SF": tier},
            predicted_team_ids=(1, 2),
        )
        self.assertEqual(result["points"], 23)
        self.assertTrue(result["exact_score"])
        self.assertFalse(result["advancing_correct"])

    # R16+: par projetado difere do par real → exceção não dispara → 0.
    def test_tipo2_exact_wrong_advancing_projected_teams_differ(self):
        bet = self._make_knockout_bet(1, 1, 1, 1, winner_real_id=1)
        result = calculate_bet_points(
            bet,
            self._make_scoring_config(),
            pool_type=POOL_TYPE_2,
            predicted_advancing_id=2,
            predicted_team_ids=(1, 3),
        )
        self.assertEqual(result["points"], 0)
        self.assertFalse(result["advancing_correct"])

    # Retrocompat: sem predicted_team_ids → exceção não dispara → 0.
    def test_tipo2_exact_wrong_advancing_no_team_ids(self):
        bet = self._make_knockout_bet(1, 1, 1, 1, winner_real_id=1)
        result = calculate_bet_points(
            bet,
            self._make_scoring_config(),
            pool_type=POOL_TYPE_2,
            predicted_advancing_id=2,
        )
        self.assertEqual(result["points"], 0)
```

Atualizar também o comentário do teste pré-existente `test_tipo2_knockout_exact_wrong_advancing_field_ignored` (`:3902-3906`): o campo deixou de ser morto; o teste agora cobre o caminho sem `predicted_team_ids` (→ 0). Trocar a docstring/comentário por:

```
    # Sem predicted_team_ids a exceção não dispara: classificado errado → 0,
    # mesmo com placar exato (placar decisivo 2x1).
    def test_tipo2_knockout_exact_wrong_advancing_field_ignored(self):
        """Sem info dos times projetados, classificado errado → 0 (sem exceção)."""
```

(O corpo e os asserts do teste permanecem iguais — ele não passa `predicted_team_ids`.)

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `PENNINICUP_SETTINGS_PROFILE=test poetry run python -m src.manage test src.pool.tests.test_pool.ScoringTipo2ExhaustiveUnitTest -v 2`
Expected: FAIL (`test_tipo2_exact_wrong_advancing_flat` espera 10, recebe 0; idem per_phase espera 23).

- [ ] **Step 3: Mapear o campo flat no fallback**

Em `src/pool/services/scoring.py`, em `_tier_from_flat_config` (`:66-73`), adicionar a chave:

```
def _tier_from_flat_config(scoring_config):
    return SimpleNamespace(
        exact=scoring_config.knockout_exact_and_advancing,
        advancing_goals=scoring_config.knockout_advancing_and_winner_goals,
        diff=scoring_config.knockout_advancing_and_diff,
        loser_goals=scoring_config.knockout_advancing_and_loser_goals,
        advancing_only=scoring_config.knockout_advancing_only,
        exact_wrong_advancing=scoring_config.knockout_exact_wrong_advancing,
    )
```

- [ ] **Step 4: Alterar a assinatura de `calculate_bet_points`**

Trocar (`:76-78`):

```
def calculate_bet_points(
    bet, scoring_config, pool_type=None, predicted_advancing_id=None, knockout_phase_scoring=None
):
```

por:

```
def calculate_bet_points(
    bet,
    scoring_config,
    pool_type=None,
    predicted_advancing_id=None,
    knockout_phase_scoring=None,
    predicted_team_ids=None,
):
```

- [ ] **Step 5: Substituir o bloco `POOL_TYPE_2`**

Trocar o bloco (`:139-166`):

```
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

por:

```
    # KNOCKOUT Tipo 2: gate por classificado (identidade do time), não por posição.
    if pool_type == POOL_TYPE_2:
        stage_key = normalize_stage_key(match.stage)
        tier = (knockout_phase_scoring or {}).get(stage_key)
        if tier is None:
            # Fallback retrocompatível: pool sem faixas por fase usa os campos flat.
            tier = _tier_from_flat_config(scoring_config)

        is_advancing_correct = bool(match.winner_id) and predicted_advancing_id == match.winner_id
        if not is_advancing_correct:
            # Exceção: placar EXATO + os dois times do palpite são exatamente os
            # dois times reais do jogo, mas o classificado está errado. Paga o
            # valor configurável da fase (tier.exact_wrong_advancing). Só ocorre
            # em jogos decididos nos pênaltis: num placar decisivo, clean() força
            # winner_pred ao vencedor do placar.
            real_pair = {match.home_team_id, match.away_team_id}
            teams_match_real = (
                None not in real_pair
                and predicted_team_ids is not None
                and set(predicted_team_ids) == real_pair
            )
            if is_exact_score and teams_match_real:
                return {
                    "points": tier.exact_wrong_advancing,
                    "exact_score": True,
                    "advancing_correct": False,
                    "advancing_goals_correct": False,
                    "diff_correct": False,
                    "eliminated_goals_correct": False,
                }
            return {
                "points": 0,
                "exact_score": is_exact_score,
                "advancing_correct": False,
                "advancing_goals_correct": False,
                "diff_correct": False,
                "eliminated_goals_correct": False,
            }
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

- [ ] **Step 6: Rodar e confirmar que passa**

Run: `PENNINICUP_SETTINGS_PROFILE=test poetry run python -m src.manage test src.pool.tests.test_pool.ScoringTipo2ExhaustiveUnitTest -v 2`
Expected: PASS (todos).

- [ ] **Step 7: Commit**

```
git add src/pool/services/scoring.py src/pool/tests/test_pool.py
git commit -m "feat(scoring): tipo 2 paga exact_wrong_advancing quando time real e classificado errado"
```

______________________________________________________________________

### Task 3: Resolver combinado em `context_builder.py`

**Files:**

- Modify: `src/pool/services/context_builder.py` (adicionar função após `resolve_knockout_advancing_by_match`, `:510-515`)
- Test: `src/pool/tests/test_pool.py` (nova classe `ResolveKnockoutTeamsAndAdvancingTest(TestCase)`)

**Interfaces:**

- Consumes: `_walk_knockout_bracket(...) -> (teams_by_match, advancing_by_match)` (já existe, `:427`).

- Produces: `resolve_knockout_teams_and_advancing(*, participant, matches, season, bets_by_match_id=None) -> (teams_by_match, advancing_by_match)`. `teams_by_match`: `{match_id: (home_team, away_team)}` (objetos `Team` ou `None`). `advancing_by_match`: `{match_id: team_id}`.

- [ ] **Step 1: Escrever o teste que falha**

Adicionar ao fim de `src/pool/tests/test_pool.py`:

```
class ResolveKnockoutTeamsAndAdvancingTest(TestCase):
    """resolve_knockout_teams_and_advancing devolve times e classificado num walk."""

    def test_r32_real_teams_and_advancing(self):
        from src.pool.services.context_builder import resolve_knockout_teams_and_advancing

        user = User.objects.create_user(username="rkta", email="rkta@example.com", password="pass")
        competition = Competition.objects.create(fifa_id=8500, name="Copa RKTA")
        season = Season.objects.create(
            fifa_id=8500, competition=competition, name="RKTA", year=2026,
            start_date="2026-06-01", end_date="2026-07-30",
        )
        ko_stage = Stage.objects.create(fifa_id="R32-rkta", season=season, name="32 Avos", order=40)
        team_a = Team.objects.create(fifa_id="rkta-A", name="RKTA A", name_norm="rkta-a", code="RKA")
        team_b = Team.objects.create(fifa_id="rkta-B", name="RKTA B", name_norm="rkta-b", code="RKB")
        past = timezone.now() - timezone.timedelta(hours=2)
        ko_match = Match.objects.create(
            fifa_id="rkta-KO", season=season, stage=ko_stage, match_number=8501,
            match_date_utc=past, match_date_local=past, match_date_brasilia=past,
            home_team=team_a, away_team=team_b, home_score=1, away_score=1,
            winner=team_a, status=Match.STATUS_FINISHED,
        )
        pool = Pool.objects.create(
            name="Pool RKTA", slug="pool-rkta", season=season, created_by=user,
            requires_payment=False, pool_type=POOL_TYPE_2,
        )
        participant = PoolParticipant.objects.create(pool=pool, user=user, is_active=True)
        PoolBet.objects.create(
            participant=participant, match=ko_match, home_score_pred=1, away_score_pred=1,
            winner_pred=team_b, is_active=True,
        )

        teams_by_match, advancing_by_match = resolve_knockout_teams_and_advancing(
            participant=participant, matches=[ko_match], season=season,
        )

        self.assertEqual(teams_by_match[ko_match.id], (team_a, team_b))
        self.assertEqual(advancing_by_match[ko_match.id], team_b.id)
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `PENNINICUP_SETTINGS_PROFILE=test poetry run python -m src.manage test src.pool.tests.test_pool.ResolveKnockoutTeamsAndAdvancingTest -v 2`
Expected: FAIL com `ImportError: cannot import name 'resolve_knockout_teams_and_advancing'`.

- [ ] **Step 3: Adicionar a função**

Em `src/pool/services/context_builder.py`, após `resolve_knockout_advancing_by_match` (`:515`):

```
def resolve_knockout_teams_and_advancing(*, participant, matches, season, bets_by_match_id=None):
    """(teams_by_match, advancing_by_match) num único walk do bracket projetado.

    teams_by_match: {match_id: (home_team, away_team)} (objetos Team ou None).
    advancing_by_match: {match_id: team_id} do classificado projetado.
    Evita dois walks quando o caller precisa de ambos.
    """
    return _walk_knockout_bracket(
        participant=participant, matches=matches, season=season, bets_by_match_id=bets_by_match_id
    )
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `PENNINICUP_SETTINGS_PROFILE=test poetry run python -m src.manage test src.pool.tests.test_pool.ResolveKnockoutTeamsAndAdvancingTest -v 2`
Expected: PASS.

- [ ] **Step 5: Commit**

```
git add src/pool/services/context_builder.py src/pool/tests/test_pool.py
git commit -m "feat(pool): resolver de times+classificado num walk do bracket"
```

______________________________________________________________________

### Task 4: Wiring dos callers + teste de integração

**Files:**

- Modify: `src/pool/services/ranking.py` (`:165-212`)
- Modify: `src/pool/services/asof_standings.py` (`:169-209`)
- Test: `src/pool/tests/test_pool.py` (método novo em `Tipo2IntegrationExtraTest`, `:3923`)

**Interfaces:**

- Consumes: `resolve_knockout_teams_and_advancing(...)` (Task 3); `calculate_bet_points(..., predicted_team_ids=...)` (Task 2); `tier.exact_wrong_advancing` por fase (Task 1).

- Produces: scores persistidos com a exceção aplicada end-to-end via `recalculate_participant_scores`.

- [ ] **Step 1: Escrever o teste de integração que falha**

Adicionar dentro de `Tipo2IntegrationExtraTest` (após `test_tipo2_mixed_group_and_knockout`):

```
    def test_tipo2_exact_wrong_advancing_partial_credit(self):
        """Empate 1x1 (pênaltis → team_a), palpite 1x1 com team_b classificando,
        ambos os times reais → paga exact_wrong_advancing da fase SF."""
        from src.pool.models import PoolBetScore

        ctx = self._build_fixture(fifa_id_base=8600, slug_suffix="exwa")
        ko = ctx["ko_match"]
        ko.home_score = 1
        ko.away_score = 1
        ko.winner = ctx["team_a"]
        ko.save(update_fields=["home_score", "away_score", "winner"])

        participant = PoolParticipant.objects.create(pool=ctx["pool"], user=ctx["user"], is_active=True)
        ko_bet = PoolBet.objects.create(
            participant=participant,
            match=ko,
            home_score_pred=1,
            away_score_pred=1,
            winner_pred=ctx["team_b"],
            is_active=True,
        )

        recalculate_participant_scores(participant)

        sf_row = ctx["pool"].get_scoring_config().knockout_phases.get(phase_key="SF")
        score = PoolBetScore.objects.get(bet=ko_bet)
        self.assertEqual(score.points, sf_row.exact_wrong_advancing)
        self.assertTrue(score.exact_score)
        self.assertFalse(score.advancing_correct)
```

Nota: `_build_fixture` usa stage `Semi-Final` (→ `SF`); `get_scoring_config()` semeia as linhas por fase, então `sf_row.exact_wrong_advancing` = 38 (default), e o caminho usa a faixa da fase.

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `PENNINICUP_SETTINGS_PROFILE=test poetry run python -m src.manage test src.pool.tests.test_pool.Tipo2IntegrationExtraTest.test_tipo2_exact_wrong_advancing_partial_credit -v 2`
Expected: FAIL (`score.points == 0`; wiring ainda não passa `predicted_team_ids`).

- [ ] **Step 3: Wire `ranking.py`**

Trocar o bloco `:165-183`:

```
    advancing_map = {}
    if pool_type in (POOL_TYPE_1, POOL_TYPE_2):
        from src.football.models import Match as FootballMatch
        from src.pool.services.context_builder import resolve_knockout_advancing_by_match

        knockout_matches = [
            m
            for m in FootballMatch.objects.filter(season=participant.pool.season)
            .select_related("stage", "home_team", "away_team", "winner")
            .order_by("match_number")
            if phase_for_match(m) != PHASE_GROUP
        ]
        bets_by_match_id = {bet.match_id: bet for bet in bets}
        advancing_map = resolve_knockout_advancing_by_match(
            participant=participant,
            matches=knockout_matches,
            season=participant.pool.season,
            bets_by_match_id=bets_by_match_id,
        )
```

por:

```
    advancing_map = {}
    teams_by_match = {}
    if pool_type in (POOL_TYPE_1, POOL_TYPE_2):
        from src.football.models import Match as FootballMatch
        from src.pool.services.context_builder import resolve_knockout_teams_and_advancing

        knockout_matches = [
            m
            for m in FootballMatch.objects.filter(season=participant.pool.season)
            .select_related("stage", "home_team", "away_team", "winner")
            .order_by("match_number")
            if phase_for_match(m) != PHASE_GROUP
        ]
        bets_by_match_id = {bet.match_id: bet for bet in bets}
        teams_by_match, advancing_map = resolve_knockout_teams_and_advancing(
            participant=participant,
            matches=knockout_matches,
            season=participant.pool.season,
            bets_by_match_id=bets_by_match_id,
        )
```

E na chamada de `calculate_bet_points` (`:205-212`), trocar:

```
    for bet in bets:
        score_data = calculate_bet_points(
            bet,
            scoring_config=scoring_config,
            pool_type=pool_type,
            predicted_advancing_id=advancing_map.get(bet.match_id),
            knockout_phase_scoring=knockout_phase_scoring,
        )
```

por:

```
    for bet in bets:
        home_t, away_t = teams_by_match.get(bet.match_id, (None, None))
        predicted_team_ids = (home_t.id, away_t.id) if home_t is not None and away_t is not None else None
        score_data = calculate_bet_points(
            bet,
            scoring_config=scoring_config,
            pool_type=pool_type,
            predicted_advancing_id=advancing_map.get(bet.match_id),
            knockout_phase_scoring=knockout_phase_scoring,
            predicted_team_ids=predicted_team_ids,
        )
```

- [ ] **Step 4: Wire `asof_standings.py`**

Trocar o import local (`:171`):

```
        from src.pool.services.context_builder import resolve_knockout_advancing_by_match
```

por:

```
        from src.pool.services.context_builder import resolve_knockout_teams_and_advancing
```

Trocar o bloco por participante (`:191-199`):

```
        advancing_map = {}
        if pool_type in (POOL_TYPE_1, POOL_TYPE_2):
            bets_by_match_id = {b.match_id: b for b in bets}
            advancing_map = resolve_knockout_advancing_by_match(
                participant=participant,
                matches=knockout_matches,
                season=pool.season,
                bets_by_match_id=bets_by_match_id,
            )
```

por:

```
        advancing_map = {}
        teams_by_match = {}
        if pool_type in (POOL_TYPE_1, POOL_TYPE_2):
            bets_by_match_id = {b.match_id: b for b in bets}
            teams_by_match, advancing_map = resolve_knockout_teams_and_advancing(
                participant=participant,
                matches=knockout_matches,
                season=pool.season,
                bets_by_match_id=bets_by_match_id,
            )
```

E na chamada de `calculate_bet_points` (`:204-209`), trocar:

```
            score_data = calculate_bet_points(
                bet,
                scoring_config=scoring_config,
                pool_type=pool_type,
                predicted_advancing_id=advancing_map.get(bet.match_id),
                knockout_phase_scoring=knockout_phase_scoring,
            )
```

por:

```
            home_t, away_t = teams_by_match.get(bet.match_id, (None, None))
            predicted_team_ids = (home_t.id, away_t.id) if home_t is not None and away_t is not None else None
            score_data = calculate_bet_points(
                bet,
                scoring_config=scoring_config,
                pool_type=pool_type,
                predicted_advancing_id=advancing_map.get(bet.match_id),
                knockout_phase_scoring=knockout_phase_scoring,
                predicted_team_ids=predicted_team_ids,
            )
```

- [ ] **Step 5: Rodar o teste de integração e confirmar que passa**

Run: `PENNINICUP_SETTINGS_PROFILE=test poetry run python -m src.manage test src.pool.tests.test_pool.Tipo2IntegrationExtraTest.test_tipo2_exact_wrong_advancing_partial_credit -v 2`
Expected: PASS.

- [ ] **Step 6: Rodar a suíte do app `pool` (regressão)**

Run: `PENNINICUP_SETTINGS_PROFILE=test poetry run python -m src.manage test src.pool -v 2`
Expected: PASS (sem regressões em ranking/asof/seed/integração Tipo 2).

- [ ] **Step 7: Commit**

```
git add src/pool/services/ranking.py src/pool/services/asof_standings.py src/pool/tests/test_pool.py
git commit -m "feat(scoring): wiring de predicted_team_ids no recalculo tipo 2"
```

______________________________________________________________________

## Self-Review

**Cobertura do spec:**

- Campo configurável por fase `exact_wrong_advancing` (model + defaults + migração + admin) → Task 1.
- Exceção paga `tier.exact_wrong_advancing` → Task 2 (Steps 5) + testes flat(10)/per_phase(23)/differ(0)/no_team_ids(0).
- Fallback flat via `knockout_exact_wrong_advancing` → Task 2 Step 3 + teste flat.
- Condição "dois times == reais" + R16+ projetado difere → Task 2 teste `..._projected_teams_differ`.
- Retrocompat `predicted_team_ids=None` → Task 2 teste `..._no_team_ids` (+ teste pré-existente atualizado).
- Resolver combinado (um walk) → Task 3.
- Wiring callers + integração end-to-end → Task 4.
- Sem impacto em exibição (views derivam `advancing_correct` à parte).

**Placeholder scan:** sem TBD/TODO; todo passo tem código/comando concreto.

**Consistência de tipos:** `exact_wrong_advancing` é `PositiveSmallIntegerField` no model (Task 1), chave no `_tier_from_flat_config` SimpleNamespace e no `tier` por fase (Task 2), e atributo lido em `tier.exact_wrong_advancing` (Task 2 Step 5). `predicted_team_ids` = `(id, id)`/`None` (Task 2, 4). `resolve_knockout_teams_and_advancing` retorna `(teams_by_match, advancing_by_match)` consistente entre Task 3 e os dois callers.

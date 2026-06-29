# Tipo 2 — Placar exato com classificado errado Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** No mata-mata Tipo 2, quando o classificado palpitado está errado MAS os dois times do palpite são exatamente os dois times reais do jogo e o placar é exato, pontuar `exact − advancing_only` (piso `advancing_only`) em vez de zerar.

**Architecture:** Mudança em três camadas. (1) `scoring.py`: novo param opcional `predicted_team_ids` e ramo de exceção no bloco `POOL_TYPE_2`. (2) `context_builder.py`: novo resolver que devolve times projetados + classificado num único walk. (3) callers (`ranking.py`, `asof_standings.py`) passam `predicted_team_ids`. Lógica pura de pontuação — sem migração de banco.

**Tech Stack:** Python 3.12, Django 6, testes `unittest` (Django test runner).

## Global Constraints

- Rodar testes com profile `test`. Bash: `PENNINICUP_SETTINGS_PROFILE=test poetry run python -m src.manage test <dotted.path> -v 2`. PowerShell: `$env:PENNINICUP_SETTINGS_PROFILE='test'; poetry run python -m src.manage test <dotted.path> -v 2`.
- Não usar `DJANGO_SETTINGS_PROFILE` (o CLAUDE.md está errado nesse ponto; o correto é `PENNINICUP_SETTINGS_PROFILE`).
- Fórmula exata da exceção: `points = max(tier.exact - tier.advancing_only, tier.advancing_only)`.
- `exact` e `advancing_only` saem da MESMA `tier` da fase (`knockout_phase_scoring[stage]`, fallback `_tier_from_flat_config`).
- `predicted_team_ids` default `None` → exceção NÃO dispara (retrocompat).
- Não tocar Tipo 1, fase de grupos, nem o campo morto `knockout_exact_wrong_advancing` (a exceção usa fórmula computada, não esse campo).
- Lint: ruff, linha máx. 119 (`make lint` ou pre-commit no commit).

______________________________________________________________________

### Task 1: Exceção de pontuação em `scoring.py`

**Files:**

- Modify: `src/pool/services/scoring.py` (assinatura de `calculate_bet_points` em `:76-78`; bloco `POOL_TYPE_2` em `:139-166`)
- Test: `src/pool/tests/test_pool.py` (classe `ScoringTipo2ExhaustiveUnitTest`, `SimpleTestCase`, sem DB)

**Interfaces:**

- Consumes: nada de tasks anteriores.
- Produces: `calculate_bet_points(bet, scoring_config, pool_type=None, predicted_advancing_id=None, knockout_phase_scoring=None, predicted_team_ids=None)`. `predicted_team_ids` é uma tupla `(home_team_id, away_team_id)` ou `None`. Comportamento: Tipo 2, classificado errado, `is_exact_score` e `set(predicted_team_ids) == {match.home_team_id, match.away_team_id}` (sem `None`) → `points = max(tier.exact - tier.advancing_only, tier.advancing_only)`, flags `exact_score=True`/`advancing_correct=False`.

O helper `_make_knockout_bet` (já existente em `ScoringTipo2ExhaustiveUnitTest`, `test_pool.py:3797`) cria `match` com `home_team_id=1`, `away_team_id=2`, `stage.name="Semi-Final"` (→ `normalize_stage_key` = `"SF"`). O helper `_make_scoring_config` (`test_pool.py:3779`) tem `knockout_exact_and_advancing=35`, `knockout_advancing_only=15`.

- [ ] **Step 1: Escrever os testes que falham**

Adicionar estes métodos dentro da classe `ScoringTipo2ExhaustiveUnitTest` (logo após `test_tipo2_knockout_exact_wrong_advancing_field_ignored`, `test_pool.py:3915`):

```
    # Exceção: placar exato + os dois times do palpite == os dois times reais,
    # classificado errado → exact - advancing_only. Config flat: 35 - 15 = 20.
    def test_tipo2_exact_wrong_advancing_both_teams_real_flat(self):
        bet = self._make_knockout_bet(1, 1, 1, 1, winner_real_id=1)
        result = calculate_bet_points(
            bet,
            self._make_scoring_config(),
            pool_type=POOL_TYPE_2,
            predicted_advancing_id=2,
            predicted_team_ids=(1, 2),
        )
        self.assertEqual(result["points"], 20)
        self.assertTrue(result["exact_score"])
        self.assertFalse(result["advancing_correct"])

    # Exemplo do usuário: faixa por fase exact=38, advancing_only=15 → 23.
    def test_tipo2_exact_wrong_advancing_per_phase_23(self):
        tier = SimpleNamespace(
            exact=38, advancing_goals=30, diff=25, loser_goals=22, advancing_only=15
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

    # Piso negativo: exact=10 < advancing_only=15 → max(10-15, 15) = 15.
    def test_tipo2_exact_wrong_advancing_negative_floor(self):
        bet = self._make_knockout_bet(1, 1, 1, 1, winner_real_id=1)
        result = calculate_bet_points(
            bet,
            self._make_scoring_config(
                knockout_exact_and_advancing=10, knockout_advancing_only=15
            ),
            pool_type=POOL_TYPE_2,
            predicted_advancing_id=2,
            predicted_team_ids=(1, 2),
        )
        self.assertEqual(result["points"], 15)

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

- [ ] **Step 2: Rodar os testes e confirmar que falham**

Run: `PENNINICUP_SETTINGS_PROFILE=test poetry run python -m src.manage test src.pool.tests.test_pool.ScoringTipo2ExhaustiveUnitTest -v 2`
Expected: FAIL (ex.: `test_tipo2_exact_wrong_advancing_both_teams_real_flat` espera 20, recebe 0; `..._no_team_ids` passa). Os que esperam 0 já passam; os de crédito parcial falham.

- [ ] **Step 3: Alterar a assinatura de `calculate_bet_points`**

Em `src/pool/services/scoring.py`, trocar:

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

- [ ] **Step 4: Substituir o bloco `POOL_TYPE_2`**

Trocar o bloco atual (`scoring.py:139-166`):

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
            # dois times reais do jogo, mas o classificado está errado. Ganha
            # exact - advancing_only (piso advancing_only). Só ocorre em jogos
            # decididos nos pênaltis (empate no tempo regulamentar): num placar
            # decisivo, clean() força winner_pred ao vencedor do placar.
            real_pair = {match.home_team_id, match.away_team_id}
            teams_match_real = (
                None not in real_pair
                and predicted_team_ids is not None
                and set(predicted_team_ids) == real_pair
            )
            if is_exact_score and teams_match_real:
                points = max(tier.exact - tier.advancing_only, tier.advancing_only)
                return {
                    "points": points,
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

- [ ] **Step 5: Rodar os testes e confirmar que passam**

Run: `PENNINICUP_SETTINGS_PROFILE=test poetry run python -m src.manage test src.pool.tests.test_pool.ScoringTipo2ExhaustiveUnitTest -v 2`
Expected: PASS (todos, inclusive os pré-existentes B5/D3 que continuam retornando 0 por não passarem `predicted_team_ids`).

- [ ] **Step 6: Commit**

```bash
git add src/pool/services/scoring.py src/pool/tests/test_pool.py
git commit -m "feat(scoring): tipo 2 credita placar exato com classificado errado"
```

______________________________________________________________________

### Task 2: Resolver combinado em `context_builder.py`

**Files:**

- Modify: `src/pool/services/context_builder.py` (adicionar função após `resolve_knockout_advancing_by_match`, `:510-515`)
- Test: `src/pool/tests/test_pool.py` (nova classe `ResolveKnockoutTeamsAndAdvancingTest(TestCase)`)

**Interfaces:**

- Consumes: `_walk_knockout_bracket(*, participant, matches, season, bets_by_match_id=None) -> (teams_by_match, advancing_by_match)` (já existe, `context_builder.py:427`).

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

- [ ] **Step 2: Rodar o teste e confirmar que falha**

Run: `PENNINICUP_SETTINGS_PROFILE=test poetry run python -m src.manage test src.pool.tests.test_pool.ResolveKnockoutTeamsAndAdvancingTest -v 2`
Expected: FAIL com `ImportError: cannot import name 'resolve_knockout_teams_and_advancing'`.

- [ ] **Step 3: Adicionar a função**

Em `src/pool/services/context_builder.py`, logo após `resolve_knockout_advancing_by_match` (`:515`):

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

- [ ] **Step 4: Rodar o teste e confirmar que passa**

Run: `PENNINICUP_SETTINGS_PROFILE=test poetry run python -m src.manage test src.pool.tests.test_pool.ResolveKnockoutTeamsAndAdvancingTest -v 2`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/pool/services/context_builder.py src/pool/tests/test_pool.py
git commit -m "feat(pool): resolver de times+classificado num walk do bracket"
```

______________________________________________________________________

### Task 3: Wiring dos callers + teste de integração

**Files:**

- Modify: `src/pool/services/ranking.py` (`:165-212`)
- Modify: `src/pool/services/asof_standings.py` (`:169-209`)
- Test: `src/pool/tests/test_pool.py` (adicionar método em `Tipo2IntegrationExtraTest`, `:3923`)

**Interfaces:**

- Consumes: `resolve_knockout_teams_and_advancing(...)` (Task 2); `calculate_bet_points(..., predicted_team_ids=...)` (Task 1).

- Produces: scores persistidos com a exceção aplicada end-to-end via `recalculate_participant_scores`.

- [ ] **Step 1: Escrever o teste de integração que falha**

Adicionar dentro de `Tipo2IntegrationExtraTest` (após `test_tipo2_mixed_group_and_knockout`). Reaproveita `_build_fixture`, mas sobrescreve o jogo de mata-mata para empate decidido nos pênaltis com classificado errado no palpite:

```
    def test_tipo2_exact_wrong_advancing_partial_credit(self):
        """Empate 1x1 (pênaltis → team_a), palpite 1x1 com team_b classificando,
        ambos os times reais → crédito parcial (exact - advancing_only), > 0."""
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

        score = PoolBetScore.objects.get(bet=ko_bet)
        config = ctx["pool"].get_scoring_config()
        expected = max(
            config.knockout_exact_and_advancing - config.knockout_advancing_only,
            config.knockout_advancing_only,
        )
        self.assertEqual(score.points, expected)
        self.assertTrue(score.exact_score)
        self.assertFalse(score.advancing_correct)
```

Nota: `_build_fixture` usa `Semi-Final` como stage do mata-mata; sem linhas `PoolKnockoutPhaseScoring`, a `tier` cai no fallback flat, então `expected` usa os campos flat do `scoring_config` (correto).

- [ ] **Step 2: Rodar o teste e confirmar que falha**

Run: `PENNINICUP_SETTINGS_PROFILE=test poetry run python -m src.manage test src.pool.tests.test_pool.Tipo2IntegrationExtraTest.test_tipo2_exact_wrong_advancing_partial_credit -v 2`
Expected: FAIL (`score.points == 0`, pois o wiring ainda não passa `predicted_team_ids`).

- [ ] **Step 3: Wire `ranking.py`**

Em `src/pool/services/ranking.py`, trocar o bloco `:165-183`:

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

Em `src/pool/services/asof_standings.py`, trocar o import local (`:171`):

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

- [ ] **Step 6: Rodar a suíte do app `pool` (regressão dos callers)**

Run: `PENNINICUP_SETTINGS_PROFILE=test poetry run python -m src.manage test src.pool -v 2`
Expected: PASS (sem regressões em ranking/asof/integração Tipo 2).

- [ ] **Step 7: Commit**

```bash
git add src/pool/services/ranking.py src/pool/services/asof_standings.py src/pool/tests/test_pool.py
git commit -m "feat(scoring): wiring de predicted_team_ids no recalculo tipo 2"
```

______________________________________________________________________

## Self-Review

**Cobertura do spec:**

- Exceção `exact - advancing_only` + piso → Task 1 (Steps 4) + testes `..._flat`, `..._per_phase_23`, `..._negative_floor`.
- Condição "dois times == reais" + R16+ projetado difere → Task 1 teste `..._projected_teams_differ`.
- Retrocompat `predicted_team_ids=None` → Task 1 teste `..._no_team_ids`.
- Resolver combinado (um walk) → Task 2.
- Wiring callers → Task 3 (ranking + asof) + integração end-to-end.
- Impacto em exibição (nenhum) → sem task (flags consistentes; views derivam `advancing_correct` por conta própria).
- Sem migração → confirmado (lógica pura).

**Placeholder scan:** sem TBD/TODO; todo passo tem código/comando concreto.

**Consistência de tipos:** `predicted_team_ids` = `(id, id)`/`None` em Task 1, 3; `resolve_knockout_teams_and_advancing` retorna `(teams_by_match, advancing_by_match)` consistente entre Task 2 e os dois callers; `tier.exact`/`tier.advancing_only` batem com `_tier_from_flat_config` e com o `SimpleNamespace` do teste por-fase.

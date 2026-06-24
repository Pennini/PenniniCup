# Tipo 2 Mata-mata Scoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pontuar o mata-mata do bolão Tipo 2 pelo classificado (identidade do time que avança), não pela posição do placar.

**Architecture:** `calculate_bet_points` ganha um parâmetro `predicted_advancing_id` e um branch Tipo 2 para o mata-mata: gate em `predicted_advancing_id == match.winner_id`, depois pontua o placar pelos campos `knockout_*`. O time projetado vem de um walk do chaveamento do participante (refatorado a partir de `resolve_knockout_match_teams`). Os scorers (`recalculate_participant_scores`, `compute_asof_standings`, `diagnose_dashboard`) resolvem o mapa de classificados uma vez por participante e o injetam no scoring.

**Tech Stack:** Python 3.12, Django 6, testes `unittest` (`SimpleTestCase`/`TestCase`).

## Global Constraints

- Altera **apenas** `pool_type == POOL_TYPE_2` na fase de mata-mata. Tipo 1 e fase de grupos: sem mudança de comportamento.
- `scoring.py` **não** importa `context_builder` (evita ciclo). O classificado projetado chega pronto via parâmetro.
- Sem migração / sem novos campos de config — reusa os `knockout_*` existentes.
- Rodar testes com profile de teste: `PENNINICUP_SETTINGS_PROFILE=test`.
- Comando de teste single-module:
  `PENNINICUP_SETTINGS_PROFILE=test poetry run python -m src.manage test src.pool.tests.test_pool -v 2`
- O config de teste em `ScoringCalculateBetPointsTest._make_scoring_config` usa valores próprios: `knockout_exact_and_advancing=35`, `knockout_advancing_and_winner_goals=25`, `knockout_advancing_and_diff=20`, `knockout_advancing_and_loser_goals=17`, `knockout_advancing_only=15`. **Asserts dos testes unitários usam esses números** (não os defaults 21/14 do model).

______________________________________________________________________

### Task 1: Branch de scoring Tipo 2 no mata-mata

**Files:**

- Modify: `src/pool/services/scoring.py`
- Test: `src/pool/tests/test_pool.py` (classe `ScoringCalculateBetPointsTest`, ~linha 1300)

**Interfaces:**

- Produces: `calculate_bet_points(bet, scoring_config, pool_type=None, predicted_advancing_id=None) -> dict`. Para `pool_type == POOL_TYPE_2` + mata-mata: gate `bool(match.winner_id) and predicted_advancing_id == match.winner_id`; se falhar → `points=0`, `advancing_correct=False`. Se passar → pontua placar pelos campos `knockout_*`.

- Produces: helper `_knockout_points_by_score(scoring_config, home, away, guess_home, guess_away) -> tuple[int, bool, bool, bool, bool]` retornando `(points, is_exact, advancing_goals, diff_correct, eliminated_goals)`.

- Consumes: `_winner_from_score`, `_is_winner_goals_correct`, `_is_loser_goals_correct` (já existem em `scoring.py`).

- [ ] **Step 1: Escrever os testes que falham**

Adicionar ao final da classe `ScoringCalculateBetPointsTest` (depois da linha ~1581):

```
    # --- Tipo 2 mata-mata: gate por classificado ---

    def test_tipo2_ex1_advancing_loser_goals(self):
        # real Brasil(1) 2x1 Holanda(2), palpite 3x1, classificado certo (1).
        bet = self._make_knockout_bet(3, 1, 2, 1, winner_real_id=1, winner_pred_id=1)
        result = calculate_bet_points(
            bet, self._make_scoring_config(), pool_type=POOL_TYPE_2, predicted_advancing_id=1
        )
        self.assertEqual(result["points"], 17)
        self.assertTrue(result["advancing_correct"])
        self.assertTrue(result["eliminated_goals_correct"])
        self.assertFalse(result["exact_score"])

    def test_tipo2_ex2_wrong_advancing_zero(self):
        # palpite 0x1 -> classificado palpitado = Holanda(2); real = Brasil(1).
        bet = self._make_knockout_bet(0, 1, 2, 1, winner_real_id=1, winner_pred_id=2)
        result = calculate_bet_points(
            bet, self._make_scoring_config(), pool_type=POOL_TYPE_2, predicted_advancing_id=2
        )
        self.assertEqual(result["points"], 0)
        self.assertFalse(result["advancing_correct"])

    def test_tipo2_ex3_exact_score_with_different_loser(self):
        # palpite 2x1 (eliminado projetado != real), classificado certo (1).
        bet = self._make_knockout_bet(2, 1, 2, 1, winner_real_id=1, winner_pred_id=1)
        result = calculate_bet_points(
            bet, self._make_scoring_config(), pool_type=POOL_TYPE_2, predicted_advancing_id=1
        )
        self.assertEqual(result["points"], 35)
        self.assertTrue(result["exact_score"])
        self.assertTrue(result["advancing_correct"])

    def test_tipo2_ex4_exact_score_wrong_advancing_zero(self):
        # placar exato 2x1, mas classificado palpitado = Marrocos(3) != real Brasil(1).
        bet = self._make_knockout_bet(2, 1, 2, 1, winner_real_id=1, winner_pred_id=3)
        result = calculate_bet_points(
            bet, self._make_scoring_config(), pool_type=POOL_TYPE_2, predicted_advancing_id=3
        )
        self.assertEqual(result["points"], 0)
        self.assertFalse(result["advancing_correct"])

    def test_tipo2_ex5_draw_pred_correct_advancing_only(self):
        # palpite 0x0 + Brasil(1) classifica; real 2x1 Brasil. Só classificado.
        bet = self._make_knockout_bet(0, 0, 2, 1, winner_real_id=1, winner_pred_id=1)
        result = calculate_bet_points(
            bet, self._make_scoring_config(), pool_type=POOL_TYPE_2, predicted_advancing_id=1
        )
        self.assertEqual(result["points"], 15)
        self.assertTrue(result["advancing_correct"])
        self.assertFalse(result["exact_score"])

    def test_tipo2_real_draw_exact(self):
        # real 1x1 (pênaltis, Brasil avança), palpite 1x1, classificado certo.
        bet = self._make_knockout_bet(1, 1, 1, 1, winner_real_id=1, winner_pred_id=1)
        result = calculate_bet_points(
            bet, self._make_scoring_config(), pool_type=POOL_TYPE_2, predicted_advancing_id=1
        )
        self.assertEqual(result["points"], 35)
        self.assertTrue(result["exact_score"])

    def test_tipo2_real_draw_same_diff(self):
        # real 1x1, palpite 0x0 (mesma diferença 0), classificado certo.
        bet = self._make_knockout_bet(0, 0, 1, 1, winner_real_id=1, winner_pred_id=1)
        result = calculate_bet_points(
            bet, self._make_scoring_config(), pool_type=POOL_TYPE_2, predicted_advancing_id=1
        )
        self.assertEqual(result["points"], 20)
        self.assertTrue(result["diff_correct"])

    def test_tipo2_real_draw_non_draw_pred_advancing_only(self):
        # real 1x1 (pênaltis), palpite 2x1 (não-empate), classificado certo.
        bet = self._make_knockout_bet(2, 1, 1, 1, winner_real_id=1, winner_pred_id=1)
        result = calculate_bet_points(
            bet, self._make_scoring_config(), pool_type=POOL_TYPE_2, predicted_advancing_id=1
        )
        self.assertEqual(result["points"], 15)
        self.assertTrue(result["advancing_correct"])

    def test_tipo2_no_winner_yet_zero(self):
        # match.winner_id ausente (jogo não decidido) -> 0.
        bet = self._make_knockout_bet(2, 1, 2, 1, winner_real_id=None, winner_pred_id=1)
        result = calculate_bet_points(
            bet, self._make_scoring_config(), pool_type=POOL_TYPE_2, predicted_advancing_id=1
        )
        self.assertEqual(result["points"], 0)
        self.assertFalse(result["advancing_correct"])
```

- [ ] **Step 2: Rodar para ver falhar**

Run: `PENNINICUP_SETTINGS_PROFILE=test poetry run python -m src.manage test src.pool.tests.test_pool.ScoringCalculateBetPointsTest -v 2`
Expected: FAIL — `calculate_bet_points()` ainda não aceita `predicted_advancing_id` (TypeError) ou pontua posicional (asserts errados).

- [ ] **Step 3: Importar `POOL_TYPE_2` em `scoring.py`**

Modificar a primeira linha de `src/pool/services/scoring.py`:

```
from src.pool.services.rules import PHASE_GROUP, POOL_TYPE_2, phase_for_match
```

- [ ] **Step 4: Adicionar o helper `_knockout_points_by_score`**

Inserir em `src/pool/services/scoring.py` logo após `_is_loser_goals_correct` (depois da linha 25):

```
def _knockout_points_by_score(scoring_config, home, away, guess_home, guess_away):
    """Faixa de pontos do mata-mata pelo placar (posicional), assumindo classificado correto.

    Retorna (points, is_exact, advancing_goals, diff_correct, eliminated_goals).
    """
    is_exact = guess_home == home and guess_away == away
    if is_exact:
        return scoring_config.knockout_exact_and_advancing, True, False, False, False

    is_diff = (guess_home - guess_away) == (home - away)

    if home == away:
        # Empate real (decidido nos pênaltis): sem vencedor posicional.
        if is_diff:
            return scoring_config.knockout_advancing_and_diff, False, False, True, False
        return scoring_config.knockout_advancing_only, False, False, False, False

    actual_direction = _winner_from_score(home, away)
    winner_goals = _is_winner_goals_correct(actual_direction, guess_home, guess_away, home, away)
    loser_goals = _is_loser_goals_correct(actual_direction, guess_home, guess_away, home, away)

    if winner_goals:
        return scoring_config.knockout_advancing_and_winner_goals, False, True, False, False
    if is_diff:
        return scoring_config.knockout_advancing_and_diff, False, False, True, False
    if loser_goals:
        return scoring_config.knockout_advancing_and_loser_goals, False, False, False, True
    return scoring_config.knockout_advancing_only, False, False, False, False
```

- [ ] **Step 5: Alterar a assinatura e inserir o branch Tipo 2**

Em `src/pool/services/scoring.py`, mudar a assinatura (linha 28):

```
def calculate_bet_points(bet, scoring_config, pool_type=None, predicted_advancing_id=None):
```

Depois do bloco `if phase == PHASE_GROUP:` (que termina no `return` da linha ~87) e **antes** do comentário `# KNOCKOUT phase — positional scoring...` (linha ~89), inserir:

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
        points, is_exact, advancing_goals, diff_correct, eliminated_goals = _knockout_points_by_score(
            scoring_config, home, away, guess_home, guess_away
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

(`home`, `away`, `guess_home`, `guess_away`, `is_exact_score`, `phase` já estão calculados acima, linhas 46–53.)

- [ ] **Step 6: Rodar os novos testes (devem passar)**

Run: `PENNINICUP_SETTINGS_PROFILE=test poetry run python -m src.manage test src.pool.tests.test_pool.ScoringCalculateBetPointsTest -v 2`
Expected: os 9 novos testes `test_tipo2_*` passam.

- [ ] **Step 7: Reescrever os 2 testes que codificavam o comportamento antigo do Tipo 2**

Substituir `test_knockout_non_draw_exact_score` (linha ~1497) por:

```
    def test_tipo2_knockout_exact_score_wrong_advancing_zero(self):
        # Tipo 2: placar exato não salva se o classificado estiver errado.
        bet = self._make_knockout_bet(2, 1, 2, 1, winner_real_id=1, winner_pred_id=2)
        result = calculate_bet_points(
            bet, self._make_scoring_config(), pool_type=POOL_TYPE_2, predicted_advancing_id=2
        )
        self.assertEqual(result["points"], 0)
        self.assertTrue(result["exact_score"])
        self.assertFalse(result["advancing_correct"])
```

Substituir `test_knockout_winner_pred_ignored_in_non_draw` (linha ~1570) por:

```
    def test_winner_pred_ignored_tipo1_but_gates_tipo2(self):
        # Tipo 1 (posicional): winner_pred irrelevante -> 25.
        bet_t1 = self._make_knockout_bet(2, 0, 2, 1, winner_real_id=1, winner_pred_id=2)
        r1 = calculate_bet_points(bet_t1, self._make_scoring_config())
        self.assertEqual(r1["points"], 25)
        self.assertTrue(r1["advancing_correct"])
        # Tipo 2: classificado projetado (2) != real (1) -> 0.
        bet_t2 = self._make_knockout_bet(2, 0, 2, 1, winner_real_id=1, winner_pred_id=2)
        r2 = calculate_bet_points(
            bet_t2, self._make_scoring_config(), pool_type=POOL_TYPE_2, predicted_advancing_id=2
        )
        self.assertEqual(r2["points"], 0)
        self.assertFalse(r2["advancing_correct"])
```

- [ ] **Step 8: Rodar a classe inteira**

Run: `PENNINICUP_SETTINGS_PROFILE=test poetry run python -m src.manage test src.pool.tests.test_pool.ScoringCalculateBetPointsTest -v 2`
Expected: PASS (todos, incluindo os reescritos; nenhum teste posicional de Tipo 1 quebra).

- [ ] **Step 9: Commit**

```bash
git add src/pool/services/scoring.py src/pool/tests/test_pool.py
git commit -m "feat(pool): scoring de mata-mata do Tipo 2 por classificado"
```

______________________________________________________________________

### Task 2: Resolver do classificado projetado por partida

**Files:**

- Modify: `src/pool/services/context_builder.py:427-491`
- Test: `src/pool/tests/test_pool.py` (nova classe `ResolveKnockoutAdvancingTest(TestCase)`)

**Interfaces:**

- Consumes: `_infer_advancing_team`, `_infer_losing_team`, `_resolve_match_team_from_placeholder`, `build_projected_placeholder_map`, `load_assign_third_map` (já em `context_builder.py`).

- Produces: `resolve_knockout_advancing_by_match(*, participant, matches, season, bets_by_match_id=None) -> dict[int, int]` — mapeia `match_id -> team_id` do classificado projetado pelo participante.

- Produces (inalterado p/ chamadores): `resolve_knockout_match_teams(*, participant, matches, season, bets_by_match_id=None) -> dict[int, tuple]`.

- [ ] **Step 1: Escrever o teste que falha**

Adicionar nova classe em `src/pool/tests/test_pool.py` (perto das outras de context_builder, ~linha 2127). Usar as fixtures de bolão existentes do arquivo como referência (`Tipo2KnockoutOpenTestCase`, linha 2638, mostra o setup de pool Tipo 2 + bets de mata-mata). O teste cobre o caso R32 (times reais conhecidos):

```
class ResolveKnockoutAdvancingTest(TestCase):
    def test_advancing_map_uses_winner_pred_for_r32(self):
        from src.pool.services.context_builder import resolve_knockout_advancing_by_match

        # Reaproveita o cenário montado por _build_tipo2_knockout_fixture (Task helper).
        ctx = self._build_tipo2_knockout_fixture()
        advancing = resolve_knockout_advancing_by_match(
            participant=ctx["participant"],
            matches=ctx["knockout_matches"],
            season=ctx["season"],
            bets_by_match_id=ctx["bets_by_match_id"],
        )
        self.assertEqual(advancing[ctx["r32_match"].id], ctx["expected_advancing_team_id"])
```

Nota p/ o implementador: monte `_build_tipo2_knockout_fixture` espelhando o setup de `Tipo2KnockoutOpenTestCase` (linha 2638): uma `Season`, um `Stage` R32 com `Match` de times reais `home_team`/`away_team`, um `PoolParticipant` com um `PoolBet` ativo não-empate (ex. home 2 x 1 away). O classificado esperado é o `home_team` (lado vencedor do placar). `knockout_matches` = lista de matches de mata-mata da season ordenada por `match_number`; `bets_by_match_id` = `{bet.match_id: bet}` com `select_related("winner_pred")`.

- [ ] **Step 2: Rodar para ver falhar**

Run: `PENNINICUP_SETTINGS_PROFILE=test poetry run python -m src.manage test src.pool.tests.test_pool.ResolveKnockoutAdvancingTest -v 2`
Expected: FAIL — `ImportError: cannot import name 'resolve_knockout_advancing_by_match'`.

- [ ] **Step 3: Extrair o walk e adicionar a nova função**

Em `src/pool/services/context_builder.py`, substituir o corpo de `resolve_knockout_match_teams` (linhas 427-491) por um walk compartilhado mais dois wrappers. O setup (linhas 434-454) e o loop (457-491) vão para `_walk_knockout_bracket`; o loop passa a guardar também o classificado por `match_id`:

```
def _walk_knockout_bracket(*, participant, matches, season, bets_by_match_id=None):
    """Walk único do chaveamento: resolve times por slot e classificado por partida.

    Retorna (teams_by_match, advancing_by_match):
    - teams_by_match: {match_id: (home_team, away_team)}
    - advancing_by_match: {match_id: advancing_team_id}
    Matches devem vir ordenados por match_number para a cascata de vencedores.
    """
    projected_standings = list(
        participant.projected_standings.select_related("group", "team").order_by(
            "group__name", "position", "team__code"
        )
    )
    projected_third_places = list(
        participant.projected_third_places.select_related("group", "team").order_by(
            "position_global", "group__name", "team__code"
        )
    )

    projected_groups = _build_projected_groups_from_rows(projected_standings)
    third_rows = _build_third_rows_from_rows(projected_third_places)
    projected_slots = build_projected_placeholder_map(projected_groups=projected_groups, third_rows=third_rows)
    qualified_groups = sorted([row["group"].name for row in third_rows if row["is_qualified"]])
    assign_third_map = load_assign_third_map(season=season, qualified_groups=qualified_groups)

    if bets_by_match_id is None:
        bets_by_match_id = {bet.match_id: bet for bet in participant.bets.select_related("winner_pred").all()}
    winners_map = {}
    losers_map = {}

    teams_by_match = {}
    advancing_by_match = {}
    for match in matches:
        if phase_for_match(match) != PHASE_KNOCKOUT:
            continue

        home_team = match.home_team
        away_team = match.away_team

        if home_team is None:
            home_team = _resolve_match_team_from_placeholder(
                placeholder=match.home_placeholder,
                projected_slots=projected_slots,
                assign_third_map=assign_third_map,
                winners_map=winners_map,
                losers_map=losers_map,
            )
        if away_team is None:
            away_team = _resolve_match_team_from_placeholder(
                placeholder=match.away_placeholder,
                projected_slots=projected_slots,
                assign_third_map=assign_third_map,
                winners_map=winners_map,
                losers_map=losers_map,
            )

        teams_by_match[match.id] = (home_team, away_team)

        bet = bets_by_match_id.get(match.id)
        advancing = _infer_advancing_team(match=match, bet=bet, home_team=home_team, away_team=away_team)
        if advancing is not None:
            advancing_by_match[match.id] = advancing.id
            winners_map[match.match_number] = advancing
            losing = _infer_losing_team(winner_team=advancing, home_team=home_team, away_team=away_team)
            if losing is not None:
                losers_map[match.match_number] = losing

    return teams_by_match, advancing_by_match


def resolve_knockout_match_teams(*, participant, matches, season, bets_by_match_id=None):
    """{match_id: (home_team, away_team)} para todas as partidas de mata-mata."""
    teams_by_match, _ = _walk_knockout_bracket(
        participant=participant, matches=matches, season=season, bets_by_match_id=bets_by_match_id
    )
    return teams_by_match


def resolve_knockout_advancing_by_match(*, participant, matches, season, bets_by_match_id=None):
    """{match_id: team_id} do classificado projetado pelo participante em cada partida."""
    _, advancing_by_match = _walk_knockout_bracket(
        participant=participant, matches=matches, season=season, bets_by_match_id=bets_by_match_id
    )
    return advancing_by_match
```

Manter a docstring original de `resolve_knockout_match_teams` (linhas 428-433) se preferir — o comportamento e a assinatura são idênticos, então `views.py:411` segue funcionando sem alteração. Confirmar que `PHASE_KNOCKOUT` já está importado em `context_builder.py` (é usado na linha 458 original); se o import era indireto, garantir `from src.pool.services.rules import ... PHASE_KNOCKOUT`.

- [ ] **Step 4: Rodar o teste novo + os de context_builder existentes**

Run: `PENNINICUP_SETTINGS_PROFILE=test poetry run python -m src.manage test src.pool.tests.test_pool.ResolveKnockoutAdvancingTest src.pool.tests.test_pool.ContextBuilderBetScoreRowTest -v 2`
Expected: PASS (o novo passa; o refactor não quebra `resolve_knockout_match_teams`).

- [ ] **Step 5: Commit**

```bash
git add src/pool/services/context_builder.py src/pool/tests/test_pool.py
git commit -m "refactor(pool): expõe classificado projetado por partida (walk único)"
```

______________________________________________________________________

### Task 3: Integrar no scorer canônico (`recalculate_participant_scores`)

**Files:**

- Modify: `src/pool/services/ranking.py:143-184`
- Test: `src/pool/tests/test_pool.py` (nova classe `RecalculateTipo2KnockoutTest(TestCase)`)

**Interfaces:**

- Consumes: `resolve_knockout_advancing_by_match` (Task 2), `POOL_TYPE_2` (de `rules`), `Match` (de `football.models`).

- Produces: `recalculate_participant_scores` passa `predicted_advancing_id` por palpite quando `pool_type == POOL_TYPE_2`; `PoolBetScore` persistidos refletem a regra nova.

- [ ] **Step 1: Escrever o teste que falha**

Adicionar em `src/pool/tests/test_pool.py`. Monta um pool Tipo 2 com um jogo de mata-mata R32 já decidido e verifica os pontos persistidos. Reaproveite o setup de `Tipo2KnockoutOpenTestCase` (linha 2638) para criar season/stage/match/pool/participant.

```
class RecalculateTipo2KnockoutTest(TestCase):
    def test_recalculate_uses_advancing_gate(self):
        from src.pool.models import PoolBetScore
        from src.pool.services.ranking import recalculate_participant_scores

        ctx = self._build_tipo2_decided_knockout()  # ver nota
        recalculate_participant_scores(ctx["participant"])

        score = PoolBetScore.objects.get(bet=ctx["correct_bet"])
        self.assertGreater(score.points, 0)
        self.assertTrue(score.advancing_correct)

        wrong = PoolBetScore.objects.get(bet=ctx["wrong_bet"])
        self.assertEqual(wrong.points, 0)
        self.assertFalse(wrong.advancing_correct)
```

Nota p/ o implementador: `_build_tipo2_decided_knockout` cria um pool Tipo 2 com **dois** participantes (ou dois jogos) — um cujo `winner_pred` == `match.winner` (classificado certo, placar não-exato p/ pontuar a faixa de classificado) e outro cujo `winner_pred` != `match.winner` (errado). O `match` precisa de `home_team`, `away_team`, `home_score`, `away_score` e `winner` setados. Garanta `participant.projected_standings`/`projected_third_places` sincronizados se o cenário tiver fases além de R32 — para R32 puro não é necessário (times reais já no match).

- [ ] **Step 2: Rodar para ver falhar**

Run: `PENNINICUP_SETTINGS_PROFILE=test poetry run python -m src.manage test src.pool.tests.test_pool.RecalculateTipo2KnockoutTest -v 2`
Expected: FAIL — sem o gate, o palpite "errado" pontua posicional (> 0).

- [ ] **Step 3: Resolver o mapa de classificados e injetar no scoring**

Em `src/pool/services/ranking.py`, dentro de `recalculate_participant_scores`, depois de `bets = list(...)` (linha 149) e antes do loop de pontuação (linha 171), adicionar:

```
    advancing_map = {}
    if pool_type == POOL_TYPE_2:
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

E mudar a chamada de scoring (linha 172) para:

```
        score_data = calculate_bet_points(
            bet,
            scoring_config=scoring_config,
            pool_type=pool_type,
            predicted_advancing_id=advancing_map.get(bet.match_id),
        )
```

Imports locais (dentro do `if`) evitam qualquer ciclo entre `ranking` e `context_builder`.

- [ ] **Step 4: Rodar o teste (deve passar)**

Run: `PENNINICUP_SETTINGS_PROFILE=test poetry run python -m src.manage test src.pool.tests.test_pool.RecalculateTipo2KnockoutTest -v 2`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/pool/services/ranking.py src/pool/tests/test_pool.py
git commit -m "feat(pool): aplica gate de classificado no recálculo do Tipo 2"
```

______________________________________________________________________

### Task 4: Integrar no recálculo histórico (`compute_asof_standings`)

**Files:**

- Modify: `src/pool/services/asof_standings.py:141-184`
- Test: `src/pool/tests/test_pool.py` (classe existente `ComputeAsOfStandingsBetsTest`, linha 2758 — adicionar um teste)

**Interfaces:**

- Consumes: `resolve_knockout_advancing_by_match` (Task 2), `POOL_TYPE_2`.

- Produces: `compute_asof_standings` pontua o mata-mata do Tipo 2 pelo classificado (consistente com `recalculate_participant_scores`).

- [ ] **Step 1: Escrever o teste que falha**

Adicionar um método em `ComputeAsOfStandingsBetsTest` (linha 2758). Reusa o setup da classe; cobre um pool Tipo 2 com um jogo de mata-mata decidido e classificado errado → 0 nos `knockout_points` as-of.

```
    def test_asof_tipo2_knockout_wrong_advancing_zero(self):
        ctx = self._build_tipo2_decided_knockout_asof()  # espelha o fixture da Task 3
        rows = compute_asof_standings(
            ctx["pool"], ctx["allowed_match_ids"], ctx["scoring_config"], ctx["official_result"]
        )
        row = next(r for r in rows if r.participant_id == ctx["wrong_participant"].id)
        self.assertEqual(row.knockout_points, 0)
```

Nota: se `AsOfStanding` não expõe `participant_id`, filtrar por `r.participant.id`. Reaproveitar o helper de fixture criado na Task 3 (extrair p/ um mixin/módulo de teste se necessário, evitando duplicação).

- [ ] **Step 2: Rodar para ver falhar**

Run: `PENNINICUP_SETTINGS_PROFILE=test poetry run python -m src.manage test src.pool.tests.test_pool.ComputeAsOfStandingsBetsTest.test_asof_tipo2_knockout_wrong_advancing_zero -v 2`
Expected: FAIL — pontua posicional sem o gate.

- [ ] **Step 3: Resolver o mapa e injetar no scoring as-of**

Em `src/pool/services/asof_standings.py`, dentro de `compute_asof_standings`, depois de `participants = list(...)` (linha 150), adicionar a resolução por participante. Como o loop é por participante (linha 155), resolver dentro do loop, depois de `bets = participant.bets...` (linha 162):

```
        advancing_map = {}
        if pool_type == POOL_TYPE_2:
            from src.pool.services.context_builder import resolve_knockout_advancing_by_match

            knockout_matches = [
                m
                for m in Match.objects.filter(season=pool.season)
                .select_related("stage", "home_team", "away_team", "winner")
                .order_by("match_number")
                if phase_for_match(m) != PHASE_GROUP
            ]
            bets_by_match_id = {b.match_id: b for b in bets}
            advancing_map = resolve_knockout_advancing_by_match(
                participant=participant,
                matches=knockout_matches,
                season=pool.season,
                bets_by_match_id=bets_by_match_id,
            )
```

E mudar a chamada (linha 166) para:

```
            score_data = calculate_bet_points(
                bet,
                scoring_config=scoring_config,
                pool_type=pool_type,
                predicted_advancing_id=advancing_map.get(bet.match_id),
            )
```

(`Match` e `phase_for_match` já estão importados no topo de `asof_standings.py`, linhas 3 e 6.)

- [ ] **Step 4: Rodar o teste + a classe inteira**

Run: `PENNINICUP_SETTINGS_PROFILE=test poetry run python -m src.manage test src.pool.tests.test_pool.ComputeAsOfStandingsBetsTest -v 2`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/pool/services/asof_standings.py src/pool/tests/test_pool.py
git commit -m "feat(pool): gate de classificado no recálculo histórico do Tipo 2"
```

______________________________________________________________________

### Task 5: Alinhar o comando de diagnóstico

**Files:**

- Modify: `src/rankings/management/commands/diagnose_dashboard.py:90-100`

**Interfaces:**

- Consumes: `resolve_knockout_advancing_by_match` (Task 2).

- Produces: o recálculo "fresco" do diagnóstico usa o mesmo `predicted_advancing_id`, então não acusa divergência falsa para o Tipo 2.

- [ ] **Step 1: Ler o contexto da chamada**

Abrir `src/rankings/management/commands/diagnose_dashboard.py` em torno da linha 95 (`fresh = calculate_bet_points(bet, scoring_config=cfg, pool_type=pool.pool_type)`). Identificar o `participant`/`pool` disponíveis no loop.

- [ ] **Step 2: Resolver o mapa por participante e passar no scoring**

Onde o diagnóstico itera os bets de um participante, antes do loop adicionar (apenas Tipo 2):

```
            advancing_map = {}
            if pool.pool_type == POOL_TYPE_2:
                from src.football.models import Match as FootballMatch

                from src.pool.services.context_builder import resolve_knockout_advancing_by_match
                from src.pool.services.rules import phase_for_match as _phase_for_match

                knockout_matches = [
                    m
                    for m in FootballMatch.objects.filter(season=pool.season)
                    .select_related("stage", "home_team", "away_team", "winner")
                    .order_by("match_number")
                    if _phase_for_match(m) != PHASE_GROUP
                ]
                advancing_map = resolve_knockout_advancing_by_match(
                    participant=participant, matches=knockout_matches, season=pool.season
                )
```

E mudar a linha 95 para:

```
                fresh = calculate_bet_points(
                    bet,
                    scoring_config=cfg,
                    pool_type=pool.pool_type,
                    predicted_advancing_id=advancing_map.get(bet.match_id),
                )
```

Garantir imports de `POOL_TYPE_2` e `PHASE_GROUP` no topo do arquivo (adicionar `from src.pool.services.rules import PHASE_GROUP, POOL_TYPE_2` se ausente). Ajustar a indentação ao loop real do comando.

- [ ] **Step 3: Sanidade — comando importa sem erro**

Run: `PENNINICUP_SETTINGS_PROFILE=test poetry run python -m src.manage help diagnose_dashboard`
Expected: imprime o help do comando (sem ImportError).

- [ ] **Step 4: Commit**

```bash
git add src/rankings/management/commands/diagnose_dashboard.py
git commit -m "chore(rankings): diagnose_dashboard usa gate de classificado no Tipo 2"
```

______________________________________________________________________

### Task 6: Suíte completa + atualizar `SCORE.md`

**Files:**

- Modify: `src/SCORE.md` (seção de mata-mata — documentar a regra do Tipo 2)

- [ ] **Step 1: Rodar a suíte do app pool + rankings**

Run: `PENNINICUP_SETTINGS_PROFILE=test poetry run python -m src.manage test src.pool src.rankings -v 1`
Expected: PASS (sem regressões).

- [ ] **Step 2: Documentar a regra do Tipo 2 em `SCORE.md`**

Adicionar uma subseção curta após a tabela de mata-mata (linha 39) explicando que o **Tipo 2** pontua o mata-mata pelo **classificado** (identidade do time projetado vs. `match.winner`), com gate: classificado errado = 0 (mesmo com placar exato), e as faixas `knockout_*` aplicadas quando o classificado bate. Real empate decidido nos pênaltis: exato 35 / mesma diferença 21 / senão 14.

- [ ] **Step 3: Lint**

Run: `poetry run pre-commit run --all-files`
Expected: PASS (ruff/mdformat formatam `SCORE.md` se preciso; re-stage e repetir se um hook modificar arquivos).

- [ ] **Step 4: Commit**

```bash
git add src/SCORE.md
git commit -m "docs(pool): regra de mata-mata do Tipo 2 no SCORE.md"
```

______________________________________________________________________

## Notas de execução

- Fixtures de teste: o arquivo já tem cenários de pool Tipo 2 (`Tipo2KnockoutOpenTestCase` linha 2638) e de as-of (`ComputeAsOfStandingsBetsTest` linha 2758). Reaproveite-os; extraia um helper compartilhado se duplicar setup entre as Tasks 3 e 4.
- Pré-requisito de produção: o worker de projeção deve manter `projected_standings`/`projected_third_places` sincronizados antes do recálculo, pois o resolver depende deles para fases R16+. Para R32 (times reais já no match) não há essa dependência.

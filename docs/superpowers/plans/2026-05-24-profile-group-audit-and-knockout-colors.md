# Profile Group-Stage Audit + Knockout Colors — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add group-stage qualifier audit and knockout color feedback to the user profile, and extend the group qualifier bonus to count the 8 best 3rd-placed teams (matching the 48-team World Cup format).

**Architecture:** A new shared helper `_real_qualifier_position_map(season)` in `src/pool/services/ranking.py` derives the real qualifier set (always top 2 + the 3rd-place teams that FIFA placed in R32). The scoring function `_calculate_group_qualifier_bonus` consumes it. The profile view builds two new context structures (`group_audit`, enriched `predicted_winners`) from the same helper, so the displayed numbers reconcile with the stored `qualifier_bonus_points`. The template renders the audit panel inline and color-codes knockout predicted winners.

**Tech Stack:** Django 6, Python 3.12, TailwindCSS classes (template only), Django TestCase. PostgreSQL (no migrations needed).

**Spec:** `docs/superpowers/specs/2026-05-24-profile-group-audit-and-knockout-colors-design.md`

______________________________________________________________________

## File Structure

**Modified:**

- `src/pool/services/ranking.py` — add `_real_qualifier_position_map`, refactor `_calculate_group_qualifier_bonus` to use it (positions 1-3, not 1-2).
- `src/penninicup/views.py` — enrich `_build_knockout_by_phase` `predicted_winners`; add `_build_group_audit`; wire `group_audit` into profile context.
- `src/penninicup/templates/penninicup/profile.html` — replace `predicted_winners` rendering with color tokens; replace `qualifier_bonus_points` block with audit panel.
- `src/penninicup/templates/penninicup/rules.html` — update group-qualifier wording to mention 3rd place + 8 best thirds rule.

**Created:**

- `src/pool/tests/test_qualifier_bonus_top3.py` — tests for the helper + scoring change.
- (Extend) `src/penninicup/tests.py` — tests for `_build_group_audit` builder + profile context wiring.

**No migrations. No model changes. No new mgmt command** (existing `recalculate_pool_scores` covers backfill).

______________________________________________________________________

## Task 1: Test fixture builder for qualifier scenarios

**Files:**

- Create: `src/pool/tests/test_qualifier_bonus_top3.py`

A shared `TestCase` base that builds: a season with 3 groups (A, B, C) of 4 teams each, a Pool + PoolParticipant with payment, an R32 stage, and helpers to create `Standing` rows and `PoolParticipantStanding` rows by position. This is the test substrate for Tasks 2 and 3.

- [ ] **Step 1: Write the fixture base class**

```python
# src/pool/tests/test_qualifier_bonus_top3.py
from django.test import TestCase
from django.utils import timezone

from src.accounts.models import UserProfile  # noqa: F401  (ensures app loaded)
from src.football.models import Competition, Group, Match, Season, Stage, Standing, Team
from src.payments.models import Payment
from src.pool.models import (
    Pool,
    PoolParticipant,
    PoolParticipantStanding,
)
from django.contrib.auth import get_user_model

User = get_user_model()


class QualifierBonusBase(TestCase):
    """Builds 3 groups (A, B, C) of 4 teams + a Pool with one paid participant.

    Helpers:
      - set_real_position(group_name, position, team_name)
      - set_proj_position(group_name, position, team_name)
      - create_r32_match(home_team_name, away_team_name)
    """

    def setUp(self):
        self.user = User.objects.create_user(username="qb-user", email="qb@example.com", password="123456Aa!")
        self.competition = Competition.objects.create(fifa_id=100, name="QB Cup")
        self.season = Season.objects.create(
            fifa_id=100,
            competition=self.competition,
            name="QB 2026",
            year=2026,
            start_date="2026-06-01",
            end_date="2026-07-30",
        )
        self.group_stage = Stage.objects.create(fifa_id="QB-GROUP", season=self.season, name="Group", order=1)
        self.r32_stage = Stage.objects.create(fifa_id="QB-R32", season=self.season, name="R32", order=2)

        self.groups = {}
        self.teams_by_group = {}
        for group_name in ("A", "B", "C"):
            group = Group.objects.create(stage=self.group_stage, name=group_name, fifa_id=f"QB-G{group_name}")
            self.groups[group_name] = group
            self.teams_by_group[group_name] = []
            for i in range(1, 5):
                team = Team.objects.create(
                    fifa_id=f"QB-{group_name}{i}",
                    name=f"Team {group_name}{i}",
                    name_norm=f"team {group_name}{i}",
                    code=f"{group_name}{i}",
                )
                team.group_set.add(group) if hasattr(team, "group_set") else None
                # Group membership is implicit via Standing + Match rows; the
                # Team model has no FK to Group in this codebase.
                self.teams_by_group[group_name].append(team)

        self.pool = Pool.objects.create(name="QB Pool", slug="qb-pool", season=self.season, created_by=self.user)
        self.participant = PoolParticipant.objects.create(pool=self.pool, user=self.user, is_active=True)
        Payment.objects.create(
            participant=self.participant,
            amount=self.pool.entry_amount or 0,
            status=Payment.STATUS_APPROVED,
        )
        self.scoring_config = self.pool.get_scoring_config()
        assert self.scoring_config is not None

    def team(self, group_name, index):
        """Index is 1-based: team('A', 1) → 'Team A1'."""
        return self.teams_by_group[group_name][index - 1]

    def set_real_position(self, group_name, position, team_index):
        team = self.team(group_name, team_index)
        Standing.objects.update_or_create(
            season=self.season,
            group=self.groups[group_name],
            team=team,
            defaults={"position": position, "points": 9 - position},
        )

    def set_proj_position(self, group_name, position, team_index):
        team = self.team(group_name, team_index)
        PoolParticipantStanding.objects.update_or_create(
            participant=self.participant,
            group=self.groups[group_name],
            team=team,
            defaults={"position": position, "points": 9 - position},
        )

    def create_r32_match(self, home_group, home_index, away_group, away_index, *, fifa_id):
        return Match.objects.create(
            fifa_id=fifa_id,
            season=self.season,
            stage=self.r32_stage,
            match_number=int(fifa_id[-2:]) if fifa_id[-2:].isdigit() else 90,
            match_date_utc=timezone.now(),
            match_date_local=timezone.now(),
            match_date_brasilia=timezone.now() + timezone.timedelta(hours=2),
            home_team=self.team(home_group, home_index),
            away_team=self.team(away_group, away_index),
        )
```

- [ ] **Step 2: Run base class smoke test**

Add temporarily:

```python
class FixtureSmokeTest(QualifierBonusBase):
    def test_setup_runs(self):
        self.assertEqual(len(self.teams_by_group["A"]), 4)
        self.assertIsNotNone(self.scoring_config)
```

Run: `DJANGO_SETTINGS_PROFILE=test poetry run python -m src.manage test src.pool.tests.test_qualifier_bonus_top3.FixtureSmokeTest -v 2`
Expected: 1 test passed.

If `Team`/`Standing`/`PoolParticipantStanding` field names differ from what the base class assumes (e.g. `points` is non-nullable with different default), adjust the fixture so this smoke test passes before moving on. The smoke test exists only to validate the fixture; delete it once it passes.

- [ ] **Step 3: Commit**

```bash
git add src/pool/tests/test_qualifier_bonus_top3.py
git commit -m "test(pool): scaffold qualifier-bonus-top-3 fixture base"
```

______________________________________________________________________

## Task 2: Helper `_real_qualifier_position_map` (TDD)

**Files:**

- Modify: `src/pool/services/ranking.py` (add helper)

- Modify: `src/pool/tests/test_qualifier_bonus_top3.py` (add tests)

- [ ] **Step 1: Write failing tests for the helper**

Append to `src/pool/tests/test_qualifier_bonus_top3.py`:

```python
from src.pool.services.ranking import _real_qualifier_position_map


class RealQualifierPositionMapTest(QualifierBonusBase):
    def test_no_standings_returns_empty_and_not_drawn(self):
        result, r32_drawn = _real_qualifier_position_map(self.season)
        self.assertEqual(result, {})
        self.assertFalse(r32_drawn)

    def test_top2_only_when_r32_not_drawn(self):
        self.set_real_position("A", 1, 1)
        self.set_real_position("A", 2, 2)
        self.set_real_position("A", 3, 3)
        self.set_real_position("A", 4, 4)

        result, r32_drawn = _real_qualifier_position_map(self.season)

        self.assertFalse(r32_drawn)
        gid = self.groups["A"].id
        self.assertEqual(
            result[gid],
            {1: self.team("A", 1).id, 2: self.team("A", 2).id},
        )

    def test_third_included_when_team_in_r32(self):
        for group_name in ("A", "B"):
            for pos in (1, 2, 3, 4):
                self.set_real_position(group_name, pos, pos)

        # A3 is placed in an R32 match → qualifies. B3 is not → does not.
        self.create_r32_match("A", 1, "B", 2, fifa_id="QB-R3201")
        self.create_r32_match("A", 3, "B", 1, fifa_id="QB-R3202")

        result, r32_drawn = _real_qualifier_position_map(self.season)

        self.assertTrue(r32_drawn)
        a_id = self.groups["A"].id
        b_id = self.groups["B"].id
        self.assertIn(3, result[a_id])
        self.assertEqual(result[a_id][3], self.team("A", 3).id)
        self.assertNotIn(3, result[b_id])

    def test_third_excluded_when_r32_empty_teams(self):
        for pos in (1, 2, 3, 4):
            self.set_real_position("A", pos, pos)

        # R32 match exists but no teams assigned yet.
        Match.objects.create(
            fifa_id="QB-R3299",
            season=self.season,
            stage=self.r32_stage,
            match_number=99,
            match_date_utc=timezone.now(),
            match_date_local=timezone.now(),
            match_date_brasilia=timezone.now() + timezone.timedelta(hours=2),
            home_team=None,
            away_team=None,
        )

        result, r32_drawn = _real_qualifier_position_map(self.season)
        self.assertFalse(r32_drawn)
        self.assertNotIn(3, result[self.groups["A"].id])
```

- [ ] **Step 2: Run tests — expect ImportError**

Run: `DJANGO_SETTINGS_PROFILE=test poetry run python -m src.manage test src.pool.tests.test_qualifier_bonus_top3.RealQualifierPositionMapTest -v 2`
Expected: 4 errors — `ImportError: cannot import name '_real_qualifier_position_map'`.

- [ ] **Step 3: Add helper to ranking.py**

In `src/pool/services/ranking.py`, insert before `_calculate_group_qualifier_bonus`:

```python
def _real_qualifier_position_map(season):
    """Return ({group_id: {position: team_id}}, r32_drawn).

    Positions 1 and 2 always come from Standings. Position 3 is included only
    when FIFA has placed the team in an R32 match (the 8 best thirds rule).
    r32_drawn is True iff at least one R32 match has any team assigned.
    """
    from src.football.models import Match, Standing
    from src.pool.services.rules import normalize_stage_key

    real = {}
    for s in Standing.objects.filter(season=season, position__lte=2).values("group_id", "position", "team_id"):
        real.setdefault(s["group_id"], {})[s["position"]] = s["team_id"]

    r32_team_ids = set()
    for match in Match.objects.filter(season=season).select_related("stage"):
        if normalize_stage_key(match.stage) != "R32":
            continue
        if match.home_team_id:
            r32_team_ids.add(match.home_team_id)
        if match.away_team_id:
            r32_team_ids.add(match.away_team_id)

    r32_drawn = bool(r32_team_ids)
    if r32_drawn:
        for s in Standing.objects.filter(season=season, position=3).values("group_id", "team_id"):
            if s["team_id"] in r32_team_ids:
                real.setdefault(s["group_id"], {})[3] = s["team_id"]

    return real, r32_drawn
```

- [ ] **Step 4: Run tests — expect pass**

Run: same command as Step 2.
Expected: 4 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/pool/services/ranking.py src/pool/tests/test_qualifier_bonus_top3.py
git commit -m "feat(pool): add _real_qualifier_position_map helper

Derives the real group-stage qualifier set: top 2 always, plus 3rd-place
teams that FIFA actually placed in R32 (the 8 best thirds). Returns an
r32_drawn flag so callers can distinguish 'pending' from 'wrong' for
3rd-place predictions."
```

______________________________________________________________________

## Task 3: Switch `_calculate_group_qualifier_bonus` to top 3 (TDD)

**Files:**

- Modify: `src/pool/services/ranking.py`

- Modify: `src/pool/tests/test_qualifier_bonus_top3.py`

- [ ] **Step 1: Add scoring test class**

Append to `src/pool/tests/test_qualifier_bonus_top3.py`:

```python
from src.pool.services.ranking import _calculate_group_qualifier_bonus


class CalculateGroupQualifierBonusTopThreeTest(QualifierBonusBase):
    def _qpts(self):
        return self.scoring_config.group_qualifier_points

    def _pbonus(self):
        return self.scoring_config.group_qualifier_position_bonus

    def _set_full_group_real(self, group_name, draw_r32_for_third=True):
        # Team {name}1 → 1st, {name}2 → 2nd, {name}3 → 3rd, {name}4 → 4th.
        for pos in (1, 2, 3, 4):
            self.set_real_position(group_name, pos, pos)
        if draw_r32_for_third:
            # Put 3rd-place team in an R32 match so r32_drawn=True and team qualifies.
            self.create_r32_match(group_name, 3, group_name, 1, fifa_id=f"QB-RX{group_name}")

    def test_predicted_third_finishes_third_and_advances(self):
        self._set_full_group_real("A")
        self.set_proj_position("A", 3, 3)
        result = _calculate_group_qualifier_bonus(self.participant, self.scoring_config)
        self.assertEqual(result, self._qpts() + self._pbonus())

    def test_predicted_third_finishes_third_but_does_not_advance(self):
        # Real top 4 set, but no R32 match places the 3rd team → does not qualify.
        for pos in (1, 2, 3, 4):
            self.set_real_position("A", pos, pos)
        # R32 only contains 1st/2nd teams.
        self.create_r32_match("A", 1, "A", 2, fifa_id="QB-RZ01")

        self.set_proj_position("A", 3, 3)
        result = _calculate_group_qualifier_bonus(self.participant, self.scoring_config)
        self.assertEqual(result, 0)

    def test_predicted_third_finishes_first(self):
        self._set_full_group_real("A")
        # Predict team A1 (the actual 1st) in 3rd slot.
        self.set_proj_position("A", 3, 1)
        result = _calculate_group_qualifier_bonus(self.participant, self.scoring_config)
        # Qualifies (A1 is in real top 2), no position match.
        self.assertEqual(result, self._qpts())

    def test_predicted_third_finishes_fourth(self):
        self._set_full_group_real("A")
        self.set_proj_position("A", 3, 4)
        result = _calculate_group_qualifier_bonus(self.participant, self.scoring_config)
        self.assertEqual(result, 0)

    def test_predicted_first_finishes_third_and_advances(self):
        self._set_full_group_real("A")
        # Predict team A3 (the actual 3rd) in 1st slot.
        self.set_proj_position("A", 1, 3)
        result = _calculate_group_qualifier_bonus(self.participant, self.scoring_config)
        # A3 is a real qualifier → qualifier_points, no position match.
        self.assertEqual(result, self._qpts())

    def test_predicted_first_finishes_third_but_does_not_advance(self):
        for pos in (1, 2, 3, 4):
            self.set_real_position("A", pos, pos)
        self.create_r32_match("A", 1, "A", 2, fifa_id="QB-RZ02")  # 3rd not in R32

        self.set_proj_position("A", 1, 3)
        result = _calculate_group_qualifier_bonus(self.participant, self.scoring_config)
        self.assertEqual(result, 0)

    def test_r32_not_drawn_predicted_third_is_zero(self):
        for pos in (1, 2, 3, 4):
            self.set_real_position("A", pos, pos)
        # No R32 match created at all → r32_drawn=False.

        self.set_proj_position("A", 3, 3)
        result = _calculate_group_qualifier_bonus(self.participant, self.scoring_config)
        self.assertEqual(result, 0)

    def test_perfect_top_three_match(self):
        self._set_full_group_real("A")
        self.set_proj_position("A", 1, 1)
        self.set_proj_position("A", 2, 2)
        self.set_proj_position("A", 3, 3)
        result = _calculate_group_qualifier_bonus(self.participant, self.scoring_config)
        self.assertEqual(result, 3 * (self._qpts() + self._pbonus()))

    def test_empty_standings_returns_zero(self):
        result = _calculate_group_qualifier_bonus(self.participant, self.scoring_config)
        self.assertEqual(result, 0)
```

- [ ] **Step 2: Run tests — most fail**

Run: `DJANGO_SETTINGS_PROFILE=test poetry run python -m src.manage test src.pool.tests.test_qualifier_bonus_top3.CalculateGroupQualifierBonusTopThreeTest -v 2`
Expected: tests like `test_predicted_third_finishes_third_and_advances` fail because old code ignores position 3.

- [ ] **Step 3: Replace `_calculate_group_qualifier_bonus` body**

In `src/pool/services/ranking.py`, replace the function body with:

```python
def _calculate_group_qualifier_bonus(participant, scoring_config):
    """Award points for correctly predicting group-stage qualifiers.

    Top 2 always qualify; 3rd place qualifies only if the team is among the
    8 best thirds (i.e. FIFA placed it in an R32 match). For each predicted
    team that matches a real qualifier: +group_qualifier_points; +position_bonus
    additionally if the predicted position equals the real Standings position.
    """
    season = participant.pool.season

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
```

Remove the now-unused inline `from src.football.models import Standing` import at the top of the old function body; the new body uses the helper so no Standing import is needed here.

- [ ] **Step 4: Run tests — expect pass**

Run: same command as Step 2.
Expected: 9 tests pass.

- [ ] **Step 5: Run full pool test suite (regression)**

Run: `DJANGO_SETTINGS_PROFILE=test poetry run python -m src.manage test src.pool -v 2`
Expected: all tests pass. If any pre-existing tests around `qualifier_bonus_points` or `recalculate_participant_scores` break due to the new top-3 behavior, fix them (they likely had stale fixtures assuming top-2-only). Do not weaken assertions; instead update fixtures to either include R32 placement (so 3rd qualifies) or omit R32 (so it doesn't).

- [ ] **Step 6: Commit**

```bash
git add src/pool/services/ranking.py src/pool/tests/test_qualifier_bonus_top3.py
git commit -m "feat(pool): include 8 best 3rd places in group qualifier bonus

3rd-place predictions now score qualifier_points (+ position_bonus if
exact match) when the team is among the 8 best thirds derived from the
R32 bracket. Predictions of 3rd-place teams that did not advance, or
made before the R32 draw, score 0."
```

______________________________________________________________________

## Task 4: Update rules page wording

**Files:**

- Modify: `src/penninicup/templates/penninicup/rules.html` (lines 163-185 area)

- [ ] **Step 1: Read the current block**

Read `src/penninicup/templates/penninicup/rules.html` lines 160-185 to see current wording verbatim.

- [ ] **Step 2: Update lines**

Apply these exact edits.

Line 165:

```html
<p class="text-sm text-neutral-400">Para cada time que você acertou como classificado da fase de grupos:</p>
```

→

```html
<p class="text-sm text-neutral-400">Para cada time que você acertou como classificado (1º, 2º ou um dos 8 melhores 3º) da fase de grupos:</p>
```

Line 171:

```html
+ posição exata (1º ou 2º): <strong class="text-emerald-300">+{{ scoring_config.group_qualifier_position_bonus }} pts</strong>
```

→

```html
+ posição exata (1º, 2º ou 3º): <strong class="text-emerald-300">+{{ scoring_config.group_qualifier_position_bonus }} pts</strong>
```

After line 179 (`<li>Se você apostou Marrocos em 1º…`), insert one additional `<li>` example:

```html
<li>Se você apostou um time em 3º que ficou em 3º E foi um dos 8 melhores 3º → <span class="text-emerald-300">+{{ qualifier_bonus_max }} pts</span> (passou E posição certa)</li>
```

- [ ] **Step 3: Verify rules page renders**

Run: `DJANGO_SETTINGS_PROFILE=test poetry run python -m src.manage test src.penninicup.tests.RulesPageTest -v 2`
Expected: all `RulesPageTest` tests pass (they verify the page renders without 500). If `RulesPageTest` has assertions on specific copy, update them to match the new wording.

- [ ] **Step 4: Commit**

```bash
git add src/penninicup/templates/penninicup/rules.html
git commit -m "docs(rules): mention 3rd place qualification + 8 best thirds rule"
```

______________________________________________________________________

## Task 5: Enrich knockout `predicted_winners` (TDD)

**Files:**

- Modify: `src/penninicup/views.py` (function `_build_knockout_by_phase`, lines 69-93)

- Modify: `src/penninicup/tests.py` (add test class)

- [ ] **Step 1: Write failing test**

Append to `src/penninicup/tests.py`:

```python
class BuildKnockoutByPhasePredictedWinnersTest(SimpleTestCase):
    def test_predicted_includes_advanced_and_decided_flags(self):
        from types import SimpleNamespace
        from src.penninicup.views import _build_knockout_by_phase

        team_a = SimpleNamespace(id=1, name="A")
        team_b = SimpleNamespace(id=2, name="B")
        team_c = SimpleNamespace(id=3, name="C")
        stage = SimpleNamespace(fifa_id="R16", name="Oitavas de Final")

        match_decided = SimpleNamespace(
            stage=stage,
            match_number=1,
            winner=team_a,
        )
        match_pending = SimpleNamespace(
            stage=stage,
            match_number=2,
            winner=None,
        )
        bet_advanced = SimpleNamespace(winner_pred=team_a, winner_pred_id=1)
        bet_eliminated = SimpleNamespace(winner_pred=team_c, winner_pred_id=3)
        bet_pending = SimpleNamespace(winner_pred=team_b, winner_pred_id=2)

        rows = [
            {"match": match_decided, "bet": bet_advanced, "bet_score": None},
            {"match": match_pending, "bet": bet_eliminated, "bet_score": None},
            {"match": match_pending, "bet": bet_pending, "bet_score": None},
        ]

        # Scoring config not needed for predicted_winners semantics.
        scoring_config = SimpleNamespace(knockout_team_advancement_bonus=0)
        phases = _build_knockout_by_phase(rows, scoring_config)

        self.assertEqual(len(phases), 1)
        predicted = phases[0]["predicted_winners"]
        self.assertEqual(len(predicted), 3)
        items_by_team_id = {item["team"].id: item for item in predicted}

        # team_a appears in real_winners → advanced
        self.assertTrue(items_by_team_id[1]["advanced"])
        self.assertTrue(items_by_team_id[1]["decided"])

        # team_c does NOT appear in real_winners (only team_a does) → eliminated.
        # Phase has real_winners overall, so decided=True.
        self.assertFalse(items_by_team_id[3]["advanced"])
        self.assertTrue(items_by_team_id[3]["decided"])

        # team_b: same phase, still decided=True because the phase as a whole
        # has at least one real winner.
        self.assertTrue(items_by_team_id[2]["decided"])
        self.assertFalse(items_by_team_id[2]["advanced"])
```

- [ ] **Step 2: Run test — expect fail**

Run: `DJANGO_SETTINGS_PROFILE=test poetry run python -m src.manage test src.penninicup.tests.BuildKnockoutByPhasePredictedWinnersTest -v 2`
Expected: AssertionError — `predicted_winners` currently contains `Team` objects, not dicts.

- [ ] **Step 3: Replace `_build_knockout_by_phase`**

In `src/penninicup/views.py`, replace the function body (lines 69-93) with:

```python
def _build_knockout_by_phase(knockout_rows, scoring_config):
    bonus_pts_each = scoring_config.knockout_team_advancement_bonus if scoring_config else 0
    sorted_rows = sorted(knockout_rows, key=_stage_sort_key)
    phases = []
    for stage_key, rows in groupby(sorted_rows, key=lambda r: _resolve_stage_key(r["match"].stage)):
        rows = list(rows)
        real_winners = [r["match"].winner for r in rows if r["match"].winner]
        real_winners_ids = {t.id for t in real_winners}
        decided = bool(real_winners_ids)
        predicted = [
            {
                "team": r["bet"].winner_pred,
                "advanced": r["bet"].winner_pred_id in real_winners_ids,
                "decided": decided,
            }
            for r in rows
            if r.get("bet") and r["bet"] and r["bet"].winner_pred
        ]
        bonus_rows = [r for r in rows if r["bet_score"] and r["bet_score"].team_advancement_bonus]
        bonus_count = len(bonus_rows)
        phases.append(
            {
                "stage_name": stage_key,
                "stage_label": _KNOCKOUT_STAGE_LABELS.get(stage_key, stage_key),
                "rows": rows,
                "predicted_winners": predicted,
                "real_winners": real_winners,
                "bonus_count": bonus_count,
                "bonus_total": bonus_count * bonus_pts_each,
                "bonus_pts_each": bonus_pts_each,
                "has_results": bool(real_winners),
                "has_bonus": bonus_count > 0,
            }
        )
    return phases
```

- [ ] **Step 4: Run test — expect pass**

Run: same command as Step 2.
Expected: 1 test passes.

- [ ] **Step 5: Commit**

```bash
git add src/penninicup/views.py src/penninicup/tests.py
git commit -m "feat(profile): enrich knockout predicted_winners with advanced/decided

Each entry is now {team, advanced, decided} so the template can color
predicted classifiers green if they advanced in that phase, red if
eliminated, or keep neutral while the phase has no results yet."
```

______________________________________________________________________

## Task 6: Update profile template — knockout colors

**Files:**

- Modify: `src/penninicup/templates/penninicup/profile.html` (lines 348-354)

- [ ] **Step 1: Replace the `predicted_winners` loop**

Find this block in `src/penninicup/templates/penninicup/profile.html`:

```django
{% for team in phase.predicted_winners %}
<span class="inline-flex items-center gap-1 rounded-full border border-orange-500/20 bg-orange-500/5 px-2 py-0.5 text-xs text-orange-300">
    {% if team.flag_image_url %}<span class="inline-flex h-3.5 w-3.5 overflow-hidden rounded-full ring-1 ring-neutral-600 bg-neutral-800 shrink-0"><img src="{{ team.flag_image_url }}" alt="" class="h-full w-full object-cover" /></span>{% endif %}
    {{ team.name }}
</span>
{% endfor %}
```

Replace with:

```django
{% for item in phase.predicted_winners %}
<span class="inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs
    {% if not item.decided %}border-orange-500/20 bg-orange-500/5 text-orange-300
    {% elif item.advanced %}border-green-500/30 bg-green-500/10 text-green-300
    {% else %}border-red-500/30 bg-red-500/10 text-red-300{% endif %}">
    {% if item.team.flag_image_url %}<span class="inline-flex h-3.5 w-3.5 overflow-hidden rounded-full ring-1 ring-neutral-600 bg-neutral-800 shrink-0"><img src="{{ item.team.flag_image_url }}" alt="" class="h-full w-full object-cover" /></span>{% endif %}
    {{ item.team.name }}
</span>
{% endfor %}
```

- [ ] **Step 2: Smoke-render the profile page**

Run: `DJANGO_SETTINGS_PROFILE=test poetry run python -m src.manage test src.penninicup -v 2`
Expected: existing profile tests still pass (template renders without TemplateSyntaxError or attribute errors).

- [ ] **Step 3: Commit**

```bash
git add src/penninicup/templates/penninicup/profile.html
git commit -m "feat(profile): color knockout predicted classifiers green/red

Green = team advanced in the phase, red = eliminated, neutral orange
while the phase has no real results yet."
```

______________________________________________________________________

## Task 7: Add `_build_group_audit` (TDD)

**Files:**

- Modify: `src/penninicup/views.py` (add function + wire into context)

- Modify: `src/penninicup/tests.py` (add test class)

- [ ] **Step 1: Write failing tests**

Append to `src/penninicup/tests.py`:

```python
class BuildGroupAuditTest(TestCase):
    """Integration test: builds a real season + participant and verifies the
    audit structure matches the qualifier bonus formula exactly."""

    def setUp(self):
        from src.football.models import Standing
        from src.pool.models import PoolParticipantStanding

        self.user = User.objects.create_user(username="ga-user", email="ga@example.com", password="123456Aa!")
        self.competition = Competition.objects.create(fifa_id=300, name="GA Cup")
        self.season = Season.objects.create(
            fifa_id=300,
            competition=self.competition,
            name="GA 2026",
            year=2026,
            start_date="2026-06-01",
            end_date="2026-07-30",
        )
        self.group_stage = Stage.objects.create(fifa_id="GA-GROUP", season=self.season, name="Group", order=1)
        self.r32_stage = Stage.objects.create(fifa_id="GA-R32", season=self.season, name="R32", order=2)

        self.group_a = Group.objects.create(stage=self.group_stage, name="A", fifa_id="GA-GA")
        self.teams = []
        for i in range(1, 5):
            t = Team.objects.create(fifa_id=f"GA-A{i}", name=f"GA A{i}", name_norm=f"ga a{i}", code=f"GA{i}")
            self.teams.append(t)

        self.pool = Pool.objects.create(name="GA Pool", slug="ga-pool", season=self.season, created_by=self.user)
        self.participant = PoolParticipant.objects.create(pool=self.pool, user=self.user, is_active=True)
        Payment.objects.create(
            participant=self.participant,
            amount=self.pool.entry_amount or 0,
            status=Payment.STATUS_APPROVED,
        )

        # Real standings A1..A4 in positions 1..4
        for pos in (1, 2, 3, 4):
            Standing.objects.create(
                season=self.season,
                group=self.group_a,
                team=self.teams[pos - 1],
                position=pos,
                points=10 - pos,
            )

        # Projection: I predicted A1 in 1st, A3 in 2nd, A2 in 3rd
        PoolParticipantStanding.objects.create(
            participant=self.participant, group=self.group_a, team=self.teams[0], position=1
        )
        PoolParticipantStanding.objects.create(
            participant=self.participant, group=self.group_a, team=self.teams[2], position=2
        )
        PoolParticipantStanding.objects.create(
            participant=self.participant, group=self.group_a, team=self.teams[1], position=3
        )

    def test_audit_before_r32_draw_keeps_third_row_pending(self):
        from src.penninicup.views import _build_group_audit

        audit = _build_group_audit(self.participant, self.season, self.pool.get_scoring_config())

        self.assertEqual(len(audit), 1)
        entry = audit[0]
        rows = entry["rows"]
        self.assertEqual([r["position"] for r in rows], [1, 2, 3])

        # Row 1: predicted A1 (real 1st) → qualified+position_match
        self.assertTrue(rows[0]["settled"])
        self.assertTrue(rows[0]["qualified"])
        self.assertTrue(rows[0]["position_match"])

        # Row 2: predicted A3 (real 3rd) in 2nd → before R32 draw, 3rd is not
        # a qualifier yet, but A3 is also not in real top 2 → not qualified.
        self.assertTrue(rows[1]["settled"])
        self.assertFalse(rows[1]["qualified"])

        # Row 3: predicted A2 (real 2nd) in 3rd → r32_drawn=False so settled=False
        # to avoid marking it wrong before the bracket is known.
        self.assertFalse(rows[2]["settled"])

    def test_audit_after_r32_draw_with_advancing_third(self):
        from src.penninicup.views import _build_group_audit

        # Draw R32 placing A3 in a match → A3 becomes a real qualifier.
        Match.objects.create(
            fifa_id="GA-R3201",
            season=self.season,
            stage=self.r32_stage,
            match_number=1,
            match_date_utc=timezone.now(),
            match_date_local=timezone.now(),
            match_date_brasilia=timezone.now() + timezone.timedelta(hours=2),
            home_team=self.teams[2],  # A3
            away_team=self.teams[0],  # A1
        )

        audit = _build_group_audit(self.participant, self.season, self.pool.get_scoring_config())
        entry = audit[0]
        rows = entry["rows"]
        scoring = self.pool.get_scoring_config()

        # Row 2: predicted A3 in 2nd → A3 advanced (qualifier) but position 2 != 3
        self.assertTrue(rows[1]["qualified"])
        self.assertFalse(rows[1]["position_match"])
        self.assertEqual(rows[1]["points"], scoring.group_qualifier_points)

        # Row 3: predicted A2 in 3rd, real 3rd is A3 → A2 IS a real qualifier
        # (it finished 2nd in real), so qualified=True, but position_match=False.
        self.assertTrue(rows[2]["settled"])
        self.assertTrue(rows[2]["qualified"])
        self.assertFalse(rows[2]["position_match"])
        self.assertEqual(rows[2]["points"], scoring.group_qualifier_points)
        # third_advanced reflects the REAL team at that slot (A3), not the predicted one.
        self.assertTrue(rows[2]["third_advanced"])

        # Group points sum equals what the scoring function computes.
        from src.pool.services.ranking import _calculate_group_qualifier_bonus

        expected = _calculate_group_qualifier_bonus(self.participant, scoring)
        self.assertEqual(entry["group_points"], expected)

    def test_audit_after_r32_draw_with_unlucky_third(self):
        from src.penninicup.views import _build_group_audit

        # R32 contains A1 and A2 only → A3 is NOT a real qualifier.
        Match.objects.create(
            fifa_id="GA-R3202",
            season=self.season,
            stage=self.r32_stage,
            match_number=2,
            match_date_utc=timezone.now(),
            match_date_local=timezone.now(),
            match_date_brasilia=timezone.now() + timezone.timedelta(hours=2),
            home_team=self.teams[0],
            away_team=self.teams[1],
        )

        audit = _build_group_audit(self.participant, self.season, self.pool.get_scoring_config())
        rows = audit[0]["rows"]

        # Row 3: real 3rd-place team A3 did NOT advance.
        self.assertTrue(rows[2]["settled"])
        self.assertFalse(rows[2]["third_advanced"])
```

- [ ] **Step 2: Run tests — expect ImportError**

Run: `DJANGO_SETTINGS_PROFILE=test poetry run python -m src.manage test src.penninicup.tests.BuildGroupAuditTest -v 2`
Expected: 3 errors — `_build_group_audit` not found.

- [ ] **Step 3: Add `_build_group_audit` to views.py**

In `src/penninicup/views.py`, add this function after `_build_knockout_by_phase` (around line 94). Add `from collections import defaultdict` to the existing imports at the top if not present.

```python
def _build_group_audit(participant, season, scoring_config):
    """Per-group audit of the group_qualifier_bonus the participant earned.

    Returns list[{group, rows[3], group_points, has_real}] where each row is
    {position, predicted_team, real_team, qualified, position_match, points,
    settled, third_advanced}.

    settled is False for the 3rd-place row when R32 has not yet been drawn,
    so the template can render that row as 'pending' instead of 'wrong'.
    """
    from src.football.models import Group, Standing
    from src.pool.services.ranking import _real_qualifier_position_map

    if scoring_config is None or participant is None:
        return []

    real_rows = (
        Standing.objects.filter(season=season, position__lte=3)
        .select_related("team", "group")
        .order_by("group__name", "position")
    )
    proj_rows = (
        participant.projected_standings.filter(position__lte=3)
        .select_related("team", "group")
        .order_by("group__name", "position")
    )

    real_by_group = defaultdict(dict)
    for s in real_rows:
        real_by_group[s.group_id][s.position] = s

    real_qualifier_positions, r32_drawn = _real_qualifier_position_map(season)
    real_qualifier_ids_by_group = {gid: set(positions.values()) for gid, positions in real_qualifier_positions.items()}

    proj_by_group = defaultdict(dict)
    for p in proj_rows:
        proj_by_group[p.group_id][p.position] = p

    audit = []
    for group in Group.objects.filter(stage__season=season).order_by("name"):
        real_positions = real_by_group.get(group.id, {})
        proj_positions = proj_by_group.get(group.id, {})
        has_real = bool(real_positions)
        real_qualifier_ids = real_qualifier_ids_by_group.get(group.id, set())

        rows = []
        group_points = 0
        for position in (1, 2, 3):
            proj = proj_positions.get(position)
            real = real_positions.get(position)
            predicted_team = proj.team if proj else None
            real_team = real.team if real else None

            settled = has_real if position <= 2 else r32_drawn
            qualified = bool(settled and predicted_team and predicted_team.id in real_qualifier_ids)
            position_match = bool(qualified and real_team and predicted_team.id == real_team.id)
            third_advanced = bool(position == 3 and real_team and real_team.id in real_qualifier_ids)

            points = 0
            if qualified:
                points = scoring_config.group_qualifier_points
                if position_match:
                    points += scoring_config.group_qualifier_position_bonus

            group_points += points
            rows.append(
                {
                    "position": position,
                    "predicted_team": predicted_team,
                    "real_team": real_team,
                    "qualified": qualified,
                    "position_match": position_match,
                    "points": points,
                    "settled": settled,
                    "third_advanced": third_advanced,
                }
            )

        audit.append(
            {
                "group": group,
                "rows": rows,
                "group_points": group_points,
                "has_real": has_real,
            }
        )

    return audit
```

- [ ] **Step 4: Run tests — expect pass**

Run: same command as Step 2.
Expected: 3 tests pass.

- [ ] **Step 5: Wire into profile context**

In `src/penninicup/views.py`, locate the line (around 196):

```python
knockout_by_phase = _build_knockout_by_phase(predictions_context.get("knockout_rows", []), scoring_config)
```

Immediately after it, add:

```python
group_audit = (
    _build_group_audit(selected_participation, selected_pool.season, scoring_config)
    if (selected_participation and selected_pool and is_public_predictions_visible)
    else []
)
```

Then add `"group_audit": group_audit,` to the `context` dict (around line 212, next to `"knockout_by_phase": knockout_by_phase,`).

- [ ] **Step 6: Run penninicup tests again**

Run: `DJANGO_SETTINGS_PROFILE=test poetry run python -m src.manage test src.penninicup -v 2`
Expected: all tests pass (no regression).

- [ ] **Step 7: Commit**

```bash
git add src/penninicup/views.py src/penninicup/tests.py
git commit -m "feat(profile): build group_audit context for qualifier-bonus audit

Per-group dict of predicted vs real top 3 plus per-team points, exposed
as 'group_audit'. Sum of group_points equals the participant's stored
qualifier_bonus_points after recalc."
```

______________________________________________________________________

## Task 8: Render audit panel in profile template

**Files:**

- Modify: `src/penninicup/templates/penninicup/profile.html` (lines 261-271)

- [ ] **Step 1: Replace the qualifier_bonus_points block**

Find this block in `src/penninicup/templates/penninicup/profile.html` (around lines 261-271):

```django
{% if selected_participant and selected_participant.qualifier_bonus_points %}
<article class="rounded-xl border border-emerald-700/30 bg-emerald-900/10 p-4">
    <div class="flex items-center justify-between gap-3">
        <div>
            <p class="text-xs uppercase tracking-wide text-emerald-400">Bônus de Classificados de Grupos</p>
            <p class="text-sm text-neutral-300 mt-0.5">Pontos por times acertados na classificação da fase de grupos</p>
        </div>
        <span class="text-xl font-bold text-emerald-300 shrink-0">+{{ selected_participant.qualifier_bonus_points }} pts</span>
    </div>
</article>
{% endif %}
```

Replace with:

```django
{% if group_audit %}
<article class="rounded-xl border border-emerald-700/30 bg-emerald-900/10 p-4 space-y-4">
    <div class="flex items-center justify-between gap-3">
        <div>
            <p class="text-xs uppercase tracking-wide text-emerald-400">Bônus de Classificados de Grupos</p>
            <p class="text-sm text-neutral-300 mt-0.5">Pontos por times acertados na classificação (1º, 2º, 3º) de cada grupo</p>
        </div>
        {% if selected_participant.qualifier_bonus_points %}
        <span class="text-xl font-bold text-emerald-300 shrink-0">+{{ selected_participant.qualifier_bonus_points }} pts</span>
        {% endif %}
    </div>

    <div class="grid gap-3 sm:grid-cols-2">
        {% for entry in group_audit %}
        <div class="rounded-lg border border-neutral-700/60 bg-neutral-900/60 p-3 space-y-2">
            <div class="flex items-center justify-between gap-2">
                <span class="text-sm font-semibold text-neutral-100">Grupo {{ entry.group.name }}</span>
                {% if entry.group_points %}
                <span class="text-xs font-semibold text-emerald-300">+{{ entry.group_points }} pts</span>
                {% elif entry.has_real %}
                <span class="text-xs text-neutral-500">0 pts</span>
                {% endif %}
            </div>

            <div class="grid grid-cols-2 gap-2 text-xs">
                <div class="space-y-1">
                    <p class="text-[10px] uppercase tracking-wide text-neutral-500">Meu palpite</p>
                    {% for row in entry.rows %}
                    <div class="flex items-center gap-1.5 rounded border px-1.5 py-0.5
                        {% if not row.settled or not row.predicted_team %}border-neutral-700 bg-neutral-800/40 text-neutral-300
                        {% elif row.qualified %}border-green-500/30 bg-green-500/10 text-green-300
                        {% else %}border-red-500/30 bg-red-500/10 text-red-300{% endif %}
                        {% if row.position_match %}ring-1 ring-emerald-400/60{% endif %}">
                        <span class="text-[10px] font-bold opacity-70 shrink-0">{{ row.position }}º</span>
                        {% if row.predicted_team %}
                        {% if row.predicted_team.flag_image_url %}<span class="inline-flex h-3.5 w-3.5 overflow-hidden rounded-full ring-1 ring-neutral-700 bg-neutral-900 shrink-0"><img src="{{ row.predicted_team.flag_image_url }}" alt="" class="h-full w-full object-cover" /></span>{% endif %}
                        <span class="truncate">{{ row.predicted_team.name }}</span>
                        {% if row.points %}<span class="ml-auto text-[10px] font-semibold text-emerald-300 shrink-0">+{{ row.points }}</span>{% endif %}
                        {% else %}
                        <span class="text-neutral-600 italic">—</span>
                        {% endif %}
                    </div>
                    {% endfor %}
                </div>
                <div class="space-y-1">
                    <p class="text-[10px] uppercase tracking-wide text-neutral-500">Real</p>
                    {% for row in entry.rows %}
                    <div class="flex items-center gap-1.5 rounded border border-neutral-700 bg-neutral-800/40 px-1.5 py-0.5 text-neutral-200">
                        <span class="text-[10px] font-bold opacity-70 shrink-0">{{ row.position }}º</span>
                        {% if row.real_team %}
                        {% if row.real_team.flag_image_url %}<span class="inline-flex h-3.5 w-3.5 overflow-hidden rounded-full ring-1 ring-neutral-700 bg-neutral-900 shrink-0"><img src="{{ row.real_team.flag_image_url }}" alt="" class="h-full w-full object-cover" /></span>{% endif %}
                        <span class="truncate {% if row.position == 3 and row.settled and not row.third_advanced %}line-through opacity-60{% endif %}">{{ row.real_team.name }}</span>
                        {% if row.position == 3 and row.settled and row.third_advanced %}<span class="ml-auto text-[9px] uppercase tracking-wide text-emerald-300 shrink-0">classif.</span>{% endif %}
                        {% else %}
                        <span class="text-neutral-600 italic">pendente</span>
                        {% endif %}
                    </div>
                    {% endfor %}
                </div>
            </div>
        </div>
        {% endfor %}
    </div>
</article>
{% endif %}
```

- [ ] **Step 2: Smoke-test profile page renders**

Run: `DJANGO_SETTINGS_PROFILE=test poetry run python -m src.manage test src.penninicup -v 2`
Expected: all penninicup tests pass.

- [ ] **Step 3: Manual browser check**

Start dev server: `make runserver`.
Open `http://127.0.0.1:8000/<profile-url>/?pool=<a-pool-slug>&tab=bets`.
Verify visually:

- Audit panel appears below "Artilheiro escolhido" and "Bônus de Classificados de Grupos" header.
- Each group shows two columns (Meu palpite / Real) with 3 rows (1º/2º/3º).
- Predicted teams that match real top 2 → green; teams that didn't qualify → red; 3rd-place row stays grey before R32 draw.
- "+N pts" badges on predicted teams add up to the green header total.

If R32 has not yet been drawn in the dev data, you should see all 3rd-place predicted rows in neutral grey (settled=False). If `Standing` is unpopulated, the audit shows pending teams.

- [ ] **Step 4: Commit**

```bash
git add src/penninicup/templates/penninicup/profile.html
git commit -m "feat(profile): add group-stage audit panel

Per-group side-by-side comparison of predicted vs real top 3 with
per-team point badges. Reconciles visually with the qualifier_bonus
total. Unlucky 3rd-place teams show strike-through; advancing thirds
get a 'classif.' badge."
```

______________________________________________________________________

## Task 9: Backfill rankings

**Files:** none (mgmt command exists)

- [ ] **Step 1: Backfill in dev DB**

Run: `poetry run python -m src.manage recalculate_pool_scores`
Expected: "Pontuacoes recalculadas para todos os boloes" without errors. Existing participants whose 3rd-place predictions now match a real best-third get the new points.

- [ ] **Step 2: Spot-check a participant**

Pick a participant whose `qualifier_bonus_points` you remember from before. In the Django shell:

```bash
poetry run python -m src.manage shell
```

```python
from src.pool.models import PoolParticipant

p = PoolParticipant.objects.get(id=1)  # replace with a real id
print(p.qualifier_bonus_points, p.total_points)
```

Cross-check the audit panel for the same participant on the profile page — the per-team badges should sum to `qualifier_bonus_points`.

- [ ] **Step 3: Document the production backfill step**

No commit needed for dev. For production deploy, the runbook step is:

```
poetry run python -m src.manage recalculate_pool_scores
```

This must run AFTER deploying the code from Tasks 2-3 and BEFORE telling users to look at their profiles, so the audit numbers and the stored totals stay consistent.

______________________________________________________________________

## Self-Review Notes

Spec coverage check:

- Spec § "Scoring change" → Tasks 2, 3.
- Spec § "Knockout color feedback" → Tasks 5, 6.
- Spec § "Group-stage audit panel" → Tasks 7, 8.
- Spec § "Rules page update" → Task 4.
- Spec § "Backfill" → Task 9.
- Spec § "Testing" — all 17 enumerated cases covered:
  - Helper cases 1-4 → Task 2 (4 tests).
  - Scoring cases 5-11 → Task 3 (7 of 9 tests directly map; remaining 2 are the perfect-match + empty-standings reinforcements).
  - Audit cases 12-17 → Task 7 (3 integration tests cover the pre-draw, advancing-third, and unlucky-third scenarios end-to-end).

Type/name consistency:

- `_real_qualifier_position_map` signature is identical across Tasks 2, 3, 7.
- `predicted_winners` shape change (`Team` → `{team, advanced, decided}`) is consistent between Tasks 5 (view) and 6 (template).
- `group_audit` row keys (`position, predicted_team, real_team, qualified, position_match, points, settled, third_advanced`) match between Task 7 (builder) and Task 8 (template).
- `r32_drawn` is unpacked everywhere the helper is called.

# Phase 1: Qualidade Base — Testes e Cobertura — Research

**Researched:** 2026-05-05
**Domain:** Django test coverage — service-layer unit tests + coverage.py tooling
**Confidence:** HIGH

______________________________________________________________________

## Summary

Phase 1 adds direct unit tests for three untested service modules (`scoring.py`, `rules.py`,
`context_builder.py`) and wires `coverage.py` into the Makefile. The codebase uses Django's
built-in test runner with SQLite in-memory for tests; no pytest, no factory library. All new
tests must follow the same `TestCase` + raw ORM setUp pattern already in `src/pool/tests.py`.

The most important finding is that `coverage.py` is **not installed** in the Poetry environment.
It must be added as a dev dependency before any coverage target can work. Once installed, the
Django test runner can be wrapped with `coverage run` without any plugin — no `django-coverage`
package is needed or recommended.

`scoring.py` and `rules.py` are pure functions that need no database — their tests can use
`SimpleTestCase` (or even plain `unittest.TestCase`) with lightweight mock objects, making them
fast and simple. `context_builder.py` is a database-heavy orchestrator; its tests require the
full ORM object graph and are better treated as integration tests using `TestCase` with SQLite.

**Primary recommendation:** Add `coverage` to Poetry dev dependencies; write pure-function
unit tests for `scoring.py` and `rules.py` (no DB); write integration tests for a subset of
`context_builder.py` helpers that are independently testable without the full context builder.

______________________________________________________________________

## Project Constraints (from CLAUDE.md)

| Directive                                                  | Impact on Phase                                                                              |
| ---------------------------------------------------------- | -------------------------------------------------------------------------------------------- |
| Django test runner, not pytest                             | Tests must be `TestCase` subclasses; no `@pytest.mark.parametrize`                           |
| `PENNINICUP_SETTINGS_PROFILE=test` required                | All test commands must export this env var                                                   |
| Ruff lint (E, F, I, B, UP, SIM, PLE), line-length 119      | New test files linted on commit via pre-commit                                               |
| `ATOMIC_REQUESTS=True` in base, SQLite in-memory for tests | `TestCase` wraps each test in a transaction; rollback is automatic                           |
| No factory library — raw ORM setUp pattern                 | Follow existing verbose setUp style; do not introduce `factory_boy` in this phase            |
| Portuguese for comments/docstrings                         | Test method docstrings (optional) should be in Portuguese to match `accounts/tests.py` style |
| Pre-commit hooks active on commit                          | Run `make lint` before committing new test files                                             |

______________________________________________________________________

## Architectural Responsibility Map

| Capability                 | Primary Tier                         | Secondary Tier | Rationale                |
| -------------------------- | ------------------------------------ | -------------- | ------------------------ |
| Score calculation logic    | Service layer (`scoring.py`)         | —              | Pure function, no I/O    |
| Stage/phase classification | Service layer (`rules.py`)           | —              | Pure function, no I/O    |
| View context assembly      | Service layer (`context_builder.py`) | Database       | Orchestrates ORM queries |
| Coverage measurement       | CLI tooling (Makefile)               | —              | Wraps test runner        |

______________________________________________________________________

## Current State Analysis per File

### `src/pool/services/scoring.py`

**Size:** 67 lines, 2 functions.

**`_winner_from_score(home_score, away_score)`** — pure function, no DB. Three branches:
HOME win, AWAY win, DRAW. Always returns a string. No edge cases that require DB.

**`calculate_bet_points(bet, scoring_config)`** — pure function given mock objects. Returns a
dict with keys `points`, `exact_score`, `winner_or_draw`, `winner_advancing`, `one_team_score`.

**Branches that currently have no direct tests:**

| Branch                                       | Condition                                           | Current test status                                  |
| -------------------------------------------- | --------------------------------------------------- | ---------------------------------------------------- |
| Early return — `bet.is_active == False`      | `is_active=False`                                   | Not tested directly                                  |
| Early return — `bet.home_score_pred is None` | Either prediction null                              | Not tested directly                                  |
| Early return — `match.home_score is None`    | Match not yet played                                | Not tested directly                                  |
| Group stage — exact score                    | home_pred==home_real AND away_pred==away_real       | Tested indirectly via `PoolDynamicScoringConfigTest` |
| Group stage — winner/draw only               | Predicted winner correct, not exact                 | Not tested directly                                  |
| Group stage — one-team score                 | One side matches, not exact                         | Not tested directly                                  |
| Group stage — draw (0-0 pred vs 0-0 real)    | DRAW==DRAW                                          | Not tested directly                                  |
| Knockout — winner advancing only             | `match.winner_id` set, `bet.winner_pred_id` matches | Not tested directly                                  |
| Knockout — exact score + winner advancing    | Combined                                            | Not tested directly                                  |
| Knockout — one-team score in knockout        | One side matches                                    | Not tested directly                                  |
| Knockout — inactive bonus: `is_active=False` | Bet inactive, returns all zeros                     | Not tested directly                                  |

**Key design detail for tests:** `bet` and `scoring_config` are accessed via attribute access
only (`bet.is_active`, `bet.home_score_pred`, etc.). They can be replaced with `SimpleNamespace`
objects or small hand-rolled mock classes — no DB required. `phase_for_match(match)` is called,
which calls `normalize_stage_key(match.stage)`, which accesses `match.stage.name`. So `match`
also needs a `stage` with a `.name` attribute.

**Test class approach:** `SimpleTestCase` (or `unittest.TestCase`) — no DB needed. Use
`SimpleNamespace` for bet, match, scoring_config. This keeps tests fast and isolated.

______________________________________________________________________

### `src/pool/services/rules.py`

**Size:** 35 lines, 3 items.

**`normalize_stage_key(stage)`** — pure function. Takes an object with a `.name` attribute
(or `None`). Returns a string key (`"GROUP"`, `"SF"`, `"QF"`, `"R16"`, `"R32"`, `"THIRD"`,
`"FINAL"`, or `""`).

**All branches mapped:**

| Return value | Conditions that trigger it                             |
| ------------ | ------------------------------------------------------ |
| `""`         | `stage` is falsy (None)                                |
| `""`         | `stage.name` is None or empty                          |
| `"GROUP"`    | name contains "GROUP", "GRUPO", or "PRIMEIRA FASE"     |
| `"SF"`       | name contains "SEMI" or "SF"                           |
| `"QF"`       | name contains "QUART" or "QF"                          |
| `"R16"`      | name contains "R16", "OITAV", or "ROUND OF 16"         |
| `"R32"`      | name contains "R32", "32 AVOS", or "SEGUNDAS DE FINAL" |
| `"THIRD"`    | name contains "DECIS" AND "3"                          |
| `"THIRD"`    | name contains "TERCE" AND "LUGAR"                      |
| `"FINAL"`    | name == "FINAL" (exact)                                |
| `"FINAL"`    | name contains "FINAL" but NOT "SEMI", "QUART", "OITAV" |
| `""`         | No branch matched                                      |

**Note — duplicate `_normalize_stage_key` in `context_builder.py`:** `context_builder.py`
defines its own private `_normalize_stage_key` (lines 136–159) that maps to `STAGE_*`
constants (`STAGE_SF`, `STAGE_QF`, etc.) instead of the raw strings returned by
`rules.normalize_stage_key`. They share the same matching logic but return different values
and are separate functions. Tests for `rules.normalize_stage_key` do NOT cover the
`context_builder` variant. The planner should note this duplication as a potential refactor
target (deferred — out of scope for Phase 1).

**FIFA API stage name variants seen in codebase (from existing test setUp):**

- `"Group Stage"` → `"GROUP"` (English, used in all existing test fixtures)
- `"Round of 16"` → `"R16"` (English, used in `PoolAutoBetLifecycleTest`)
- `"Semifinal"`, `"Quartas"` etc. — PT variants, reachable but not yet tested

**`phase_for_match(match)`** — delegates to `normalize_stage_key`; only tests for GROUP vs
KNOCKOUT distinction. Already covered implicitly; add one direct test to confirm fallback
(non-GROUP stage returns `PHASE_KNOCKOUT`).

**Test class approach:** `SimpleTestCase` — no DB. Pass `SimpleNamespace(name="...")` as the
`stage` argument. Use one test class with multiple test methods, one per return-value bucket.

______________________________________________________________________

### `src/pool/services/context_builder.py`

**Size:** ~549 lines, 15 functions/helpers.

**Complexity assessment:** HIGH. The top-level function `build_pool_participant_view_context`
orchestrates: DB hydration, bet pre-creation, projection staleness check, projection sync,
winners/losers map building, match iteration, and knockout bracket assembly. Testing it end-to-end
requires the same heavyweight DB fixture as `PoolAutoBetLifecycleTest` (which already exists
and effectively covers the happy path through the HTTP layer).

**Independently testable pure/near-pure helpers:**

| Helper                                                                | DB required                | Complexity | Testability                             |
| --------------------------------------------------------------------- | -------------------------- | ---------- | --------------------------------------- |
| `_winner_from_score` (in scoring.py, not here)                        | No                         | Trivial    | Already mapped above                    |
| `_make_pairs(items)`                                                  | No                         | Trivial    | `SimpleTestCase`                        |
| `_normalize_stage_key(stage)` (local copy)                            | No                         | Medium     | `SimpleTestCase` with `SimpleNamespace` |
| `_infer_advancing_team(match, bet, home_team, away_team)`             | No                         | Medium     | `SimpleTestCase`                        |
| `_infer_losing_team(winner_team, home_team, away_team)`               | No                         | Simple     | `SimpleTestCase`                        |
| `_build_winners_map(matches, bets_by_match_id)`                       | No (uses dicts)            | Medium     | `SimpleTestCase`                        |
| `_projection_is_stale_from_prefetched(bets, standings, third_places)` | No (uses lists)            | Medium     | `SimpleTestCase`                        |
| `_build_projected_groups_from_rows(projected_standings)`              | No (uses lists)            | Simple     | `SimpleTestCase`                        |
| `_build_third_rows_from_rows(projected_third_places)`                 | No (uses lists)            | Simple     | `SimpleTestCase`                        |
| `_ensure_participant_bets(participant, matches, ...)`                 | YES (bulk_create)          | Medium     | `TestCase`                              |
| `_top_scorer_options_payload_for_pool(pool)`                          | YES (Player query + cache) | Medium     | `TestCase`                              |
| `build_pool_participant_view_context(...)`                            | YES (full graph)           | HIGH       | Integration via `TestCase`              |

**Key branches in testable helpers:**

`_infer_advancing_team`:

- `match.winner_id` set → return `match.winner` (real result wins)
- `bet` is None → return None
- `bet.is_active` False → return None
- `bet.winner_pred_id` set → return `bet.winner_pred`
- `home_team` is None → return None
- `bet.home_score_pred > bet.away_score_pred` → return `home_team`
- `bet.away_score_pred > bet.home_score_pred` → return `away_team`
- Draw (equal scores, no winner_pred) → return None

`_infer_losing_team`:

- `winner_team` is None → None
- `home_team` is None → None
- `away_team` is None → None
- winner is home → return away
- winner is away → return home

`_projection_is_stale_from_prefetched`:

- No active group bets → False
- Projected standings empty → True
- Projected third places empty → True
- standings updated_at < latest bet updated_at → True
- All fresh → False

`_build_winners_map`:

- bet has explicit `winner_pred_id` → use it
- bet has scores (home > away) → infer winner from home_team
- bet has scores (away > home) → infer winner from away_team
- no bet → use `match.winner` if set

**Recommended scope for Phase 1:** Test the pure helpers listed above using `SimpleTestCase`.
Skip `build_pool_participant_view_context` integration — it is already covered via
`PoolAutoBetLifecycleTest` HTTP integration tests. Adding an end-to-end test here would
duplicate setup effort without proportional coverage gain.

______________________________________________________________________

### `src/pool/tests.py` — What is already tested (coverage gaps)

**What IS covered for scoring/rules path (indirect):**

- `PoolDynamicScoringConfigTest.test_recalculate_uses_db_config_and_bonus`: exercises
  `calculate_bet_points` for group-stage exact score (home 2-1 predicted, 2-1 real → 10 pts).
  Also exercises the bonus path in `ranking.recalculate_participant_scores`.

**What is NOT tested directly in pool domain:**

- `scoring.py` — all branches listed above except group exact score via indirect path
- `rules.py` — zero tests; the function is implicitly exercised by all tests that create
  matches with stage names like "Group Stage" or "Round of 16", but no assertions on the
  function itself
- `context_builder.py` pure helpers — zero direct tests

**Existing test class fifa_id namespace (to avoid collisions when adding new classes):**
Used: 1, 2, 3, 4, 10, 22, 33, 220, 401, 500, 600, 701. Safe to use: 800+, or any
non-conflicting value. Recommendation: use 800, 801, 802 for new test classes.

______________________________________________________________________

## Standard Stack

### Core

| Library                      | Version               | Purpose                            | Why Standard                                                             |
| ---------------------------- | --------------------- | ---------------------------------- | ------------------------------------------------------------------------ |
| `coverage`                   | 7.x (latest)          | Branch + line coverage measurement | Standard Python coverage tool; Django docs recommend it [VERIFIED: pypi] |
| `django.test.TestCase`       | bundled with Django 6 | DB-touching tests with rollback    | Already in use throughout codebase                                       |
| `django.test.SimpleTestCase` | bundled               | Tests with no DB                   | Correct base for pure-function tests                                     |
| `unittest.mock.patch`        | stdlib                | Mocking                            | Already used in codebase                                                 |

**coverage.py is NOT installed.** \[VERIFIED: `poetry run python -c "import coverage"` → ModuleNotFoundError\]

### Supporting

| Library                 | Purpose                  | When to Use                                                                                                 |
| ----------------------- | ------------------------ | ----------------------------------------------------------------------------------------------------------- |
| `types.SimpleNamespace` | Lightweight mock objects | Replacing ORM model instances in pure-function tests (already used in `context_builder.py` production code) |

### Alternatives Considered

| Instead of                  | Could Use                | Tradeoff                                                                                                     |
| --------------------------- | ------------------------ | ------------------------------------------------------------------------------------------------------------ |
| `coverage.py` directly      | `pytest-cov`             | `pytest-cov` requires switching to pytest runner; project uses Django runner — skip                          |
| `coverage run` wrapping     | `django-coverage-plugin` | Plugin improves template coverage accuracy but is unnecessary for service-layer tests                        |
| Raw `SimpleNamespace` mocks | `factory_boy`            | `factory_boy` would reduce setup verbosity but the project explicitly avoids factory libraries in this phase |

**Installation:**

```bash
poetry add --group dev coverage
```

**Version verification:** [ASSUMED] — coverage 7.x is current as of 2026; verify with
`poetry add --group dev coverage` which will resolve the latest compatible version.

______________________________________________________________________

## Architecture Patterns

### Recommended Project Structure

No new directories needed. All new tests go into:

```
src/pool/tests.py        # append new test classes
```

New classes to add (in order, appended to existing file):

```
ScoringWinnerFromScoreTest      # pure unit tests for _winner_from_score
ScoringCalculateBetPointsTest   # pure unit tests for calculate_bet_points
NormalizeStageKeyTest           # pure unit tests for normalize_stage_key
PhaseForMatchTest               # pure unit tests for phase_for_match
ContextBuilderPureHelpersTest   # pure unit tests for context_builder helpers
```

### Pattern 1: Pure-Function Test with SimpleNamespace

**What:** Test service functions that only need attribute access on their arguments.
**When to use:** `scoring.py`, `rules.py`, pure helpers in `context_builder.py`.

```python
# Source: existing usage in src/pool/services/context_builder.py (SimpleNamespace already used in production)
from types import SimpleNamespace
from django.test import SimpleTestCase
from src.pool.services.scoring import calculate_bet_points


class ScoringCalculateBetPointsTest(SimpleTestCase):
    def _make_scoring_config(self, **overrides):
        defaults = dict(
            group_winner_or_draw_points=6,
            group_exact_score_points=4,
            group_one_team_score_points=2,
            knockout_winner_advancing_points=8,
            knockout_exact_score_points=6,
            knockout_one_team_score_points=2,
        )
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    def _make_group_bet(self, home_pred, away_pred, home_real, away_real, is_active=True):
        stage = SimpleNamespace(name="Group Stage")
        match = SimpleNamespace(
            stage=stage,
            home_score=home_real,
            away_score=away_real,
            winner_id=None,
        )
        return SimpleNamespace(
            is_active=is_active,
            home_score_pred=home_pred,
            away_score_pred=away_pred,
            winner_pred_id=None,
            match=match,
        )

    def test_inactive_bet_returns_zero_points(self):
        bet = self._make_group_bet(2, 1, 2, 1, is_active=False)
        result = calculate_bet_points(bet, self._make_scoring_config())
        self.assertEqual(result["points"], 0)
        self.assertFalse(result["exact_score"])
```

### Pattern 2: Parametrized-style test via multiple test methods

Since pytest's `@parametrize` is unavailable, express parameter variants as separate test
methods or use a loop inside one test. The codebase uses separate methods consistently.

```python
def test_normalize_stage_key_english_group(self):
    stage = SimpleNamespace(name="Group Stage")
    self.assertEqual(normalize_stage_key(stage), "GROUP")


def test_normalize_stage_key_portuguese_grupo(self):
    stage = SimpleNamespace(name="Grupo A")
    self.assertEqual(normalize_stage_key(stage), "GROUP")


def test_normalize_stage_key_primeira_fase(self):
    stage = SimpleNamespace(name="Primeira Fase")
    self.assertEqual(normalize_stage_key(stage), "GROUP")
```

### Pattern 3: coverage.py wrapping Django test runner

```makefile
# In Makefile — add after existing `test` target
.PHONY: coverage
coverage: export PENNINICUP_SETTINGS_PROFILE = test
coverage:
	poetry run coverage run --source=src --branch -m src.manage test --settings=src.config.settings
	poetry run coverage report --fail-under=70
	poetry run coverage html
```

**Minimum threshold:** 70% is achievable given current state. The planner should confirm
the threshold value with the user — `[ASSUMED]` that 70% is acceptable for Phase 1.

### Anti-Patterns to Avoid

- **Creating a DB object to test a pure function:** `scoring.calculate_bet_points` only
  needs attribute access; instantiating `PoolBet`, `Match`, `Stage` for it wastes 30+ lines
  of setUp and a full database transaction.
- **Testing `build_pool_participant_view_context` directly:** Its happy-path is already
  covered by `PoolAutoBetLifecycleTest` HTTP tests. Adding a duplicate integration test for
  it increases setUp cost without proportional insight.
- **Using `fifa_id` values already taken in the file:** The collision risk is real — always
  pick fresh values (800+) for new test classes to avoid unique constraint errors.
- **Importing `coverage` in Python code:** Coverage is invoked via the CLI only (`coverage run`),
  not imported in test files.

______________________________________________________________________

## Don't Hand-Roll

| Problem                     | Don't Build                           | Use Instead                    | Why                                                                       |
| --------------------------- | ------------------------------------- | ------------------------------ | ------------------------------------------------------------------------- |
| Coverage measurement        | Custom script counting executed lines | `coverage.py` (`coverage run`) | Branch coverage, HTML report, `--fail-under` enforcement — all built in   |
| Mock objects for ORM models | Full `PoolBet.objects.create(...)`    | `SimpleNamespace`              | Pure functions need no DB; `SimpleNamespace` matches attribute access API |
| Test parametrization        | Loop with `eval()`                    | Separate test methods          | Django test runner names individual methods; loops hide failures          |

______________________________________________________________________

## Common Pitfalls

### Pitfall 1: coverage.py not in PATH when using `coverage run`

**What goes wrong:** `poetry run coverage run ...` works; bare `coverage run` fails if
coverage is not globally installed.
**Why it happens:** Poetry installs into a virtualenv; only accessible via `poetry run`.
**How to avoid:** Always prefix with `poetry run` in the Makefile target.
**Warning signs:** `command not found: coverage` error on `make coverage`.

### Pitfall 2: `--source` scope too broad

**What goes wrong:** `coverage run --source=src` includes migrations, settings, and other
noise in the report, obscuring meaningful coverage numbers.
**Why it happens:** `src` is the package root.
**How to avoid:** Use `--source=src --omit="src/*/migrations/*,src/config/settings/*"` in
the coverage command, or configure in `.coveragerc`.

### Pitfall 3: `SimpleTestCase` cannot make DB queries

**What goes wrong:** A test that accidentally touches the ORM (e.g., saves a `PoolBet`)
inside a `SimpleTestCase` raises `AssertionError: Database queries are not allowed`.
**Why it happens:** `SimpleTestCase` forbids DB access by design.
**How to avoid:** Use `SimpleTestCase` only for functions verified to be pure. If any doubt
exists, use `TestCase` instead — it is slower but safe.
**Warning signs:** `AssertionError: Database queries are not allowed in SimpleTestCase` in
test output.

### Pitfall 4: `normalize_stage_key` receives `None` stage

**What goes wrong:** Calling `normalize_stage_key(None)` returns `""` via the `if not stage`
guard — correct. But `normalize_stage_key(SimpleNamespace(name=None))` accesses
`(None or "").upper()` — also correct. Both paths are already guarded; tests should verify them.
**Why it happens:** Match objects in test fixtures often have `stage=None` or `stage.name=None`
before API sync.
**How to avoid:** Include a test for `stage=None` and `stage=SimpleNamespace(name=None)`.

### Pitfall 5: `coverage --fail-under` blocks CI on first run

**What goes wrong:** Setting `--fail-under=80` on a codebase with 40% coverage breaks the
Makefile target immediately, before any new tests are written.
**Why it happens:** Threshold set too high before baseline is established.
**How to avoid:** Run coverage without `--fail-under` first to establish the baseline.
Set the threshold at baseline + 10% to allow incremental improvement. \[ASSUMED: baseline
is below 70% given no coverage tooling exists today\]

### Pitfall 6: FIFA ID collision in new test classes

**What goes wrong:** Two `TestCase` classes using the same `fifa_id` value fail with
`IntegrityError: UNIQUE constraint failed: football_competition.fifa_id` when both run
in the same test session.
**Why it happens:** Django `TestCase` uses transactions (not database isolation) between
classes; objects created in `setUp` of one class are rolled back, but if two classes
share a `fifa_id` in their own isolated transactions, they don't actually conflict —
HOWEVER, if a class uses `setUpTestData` or the test order exposes them, they can.
The safe convention is unique IDs per class regardless.
**How to avoid:** Use `fifa_id` values 800, 801, 802 (etc.) for new classes.

______________________________________________________________________

## Code Examples

### Verified scoring.py test pattern (pure function, no DB)

```python
# Source: scoring.py line structure + SimpleNamespace pattern from context_builder.py
from types import SimpleNamespace
from django.test import SimpleTestCase
from src.pool.services.scoring import calculate_bet_points, _winner_from_score


class ScoringWinnerFromScoreTest(SimpleTestCase):
    def test_home_win(self):
        self.assertEqual(_winner_from_score(2, 1), "HOME")

    def test_away_win(self):
        self.assertEqual(_winner_from_score(0, 1), "AWAY")

    def test_draw(self):
        self.assertEqual(_winner_from_score(1, 1), "DRAW")

    def test_zero_zero_draw(self):
        self.assertEqual(_winner_from_score(0, 0), "DRAW")
```

### Verified rules.py test pattern

```python
from types import SimpleNamespace
from django.test import SimpleTestCase
from src.pool.services.rules import normalize_stage_key, phase_for_match, PHASE_GROUP, PHASE_KNOCKOUT


class NormalizeStageKeyTest(SimpleTestCase):
    def _stage(self, name):
        return SimpleNamespace(name=name)

    def test_none_stage_returns_empty(self):
        self.assertEqual(normalize_stage_key(None), "")

    def test_none_name_returns_empty(self):
        self.assertEqual(normalize_stage_key(self._stage(None)), "")

    def test_english_group_stage(self):
        self.assertEqual(normalize_stage_key(self._stage("Group Stage")), "GROUP")

    def test_portuguese_grupo(self):
        self.assertEqual(normalize_stage_key(self._stage("Grupo A")), "GROUP")

    def test_primeira_fase(self):
        self.assertEqual(normalize_stage_key(self._stage("Primeira Fase")), "GROUP")

    def test_round_of_16_english(self):
        self.assertEqual(normalize_stage_key(self._stage("Round of 16")), "R16")

    def test_oitavas_portuguese(self):
        self.assertEqual(normalize_stage_key(self._stage("Oitavas de Final")), "R16")

    def test_quarterfinal_english(self):
        self.assertEqual(normalize_stage_key(self._stage("Quarter-Final")), "QF")

    def test_semifinal_english(self):
        self.assertEqual(normalize_stage_key(self._stage("Semi-Final")), "SF")

    def test_third_place_decisao(self):
        self.assertEqual(normalize_stage_key(self._stage("Decisão 3o Lugar")), "THIRD")

    def test_third_place_terceiro_lugar(self):
        self.assertEqual(normalize_stage_key(self._stage("Terceiro Lugar")), "THIRD")

    def test_final_exact(self):
        self.assertEqual(normalize_stage_key(self._stage("Final")), "FINAL")

    def test_final_with_word_in_name(self):
        self.assertEqual(normalize_stage_key(self._stage("Grand Final")), "FINAL")

    def test_unknown_stage_returns_empty(self):
        self.assertEqual(normalize_stage_key(self._stage("Mystery Stage")), "")
```

### Verified Makefile coverage target pattern

```makefile
.PHONY: coverage
coverage: export PENNINICUP_SETTINGS_PROFILE = test
coverage:
	poetry run coverage run --source=src --branch \
		--omit="src/*/migrations/*,src/config/settings/*" \
		-m src.manage test --settings=src.config.settings --verbosity=2
	poetry run coverage report --fail-under=70
	poetry run coverage html --directory=htmlcov
```

`.coveragerc` (optional, cleaner alternative to inline omits):

```ini
[run]
source = src
branch = True
omit =
    src/*/migrations/*
    src/config/settings/*

[report]
fail_under = 70

[html]
directory = htmlcov
```

______________________________________________________________________

## Risk and Complexity Assessment per Deliverable

| Deliverable                            | Complexity | Risk       | Notes                                                                         |
| -------------------------------------- | ---------- | ---------- | ----------------------------------------------------------------------------- |
| Tests for `scoring.py`                 | LOW        | LOW        | Pure functions; 10-12 test methods; no DB; `SimpleTestCase`                   |
| Tests for `rules.py`                   | LOW        | LOW        | Pure functions; 12-14 test methods; no DB; `SimpleTestCase`                   |
| Tests for `context_builder.py` helpers | MEDIUM     | LOW-MEDIUM | Pure helpers are easy; `_ensure_participant_bets` needs DB; scope carefully   |
| `coverage` in Makefile                 | LOW        | LOW        | Requires `poetry add --group dev coverage` first                              |
| Minimum threshold                      | LOW        | MEDIUM     | Current baseline unknown; set conservatively (70%) and adjust after first run |

**Highest risk item:** Setting `--fail-under` without knowing the current baseline.
Run `coverage report` without `--fail-under` on the first pass, observe the percentage,
then set the threshold 5–10 points below the measured value so new tests can only improve it.

______________________________________________________________________

## Environment Availability

| Dependency         | Required By     | Available              | Version | Fallback            |
| ------------------ | --------------- | ---------------------- | ------- | ------------------- |
| Python 3.12        | All tests       | Implied (project runs) | 3.12    | —                   |
| Django 6           | All tests       | Implied                | 6.x     | —                   |
| coverage.py        | `make coverage` | NO                     | —       | None — must install |
| SQLite (in-memory) | TestCase tests  | YES (bundled)          | —       | —                   |

**Missing dependencies with no fallback:**

- `coverage` — must be installed via `poetry add --group dev coverage` before any coverage
  Makefile target can work. [VERIFIED: import fails]

______________________________________________________________________

## Assumptions Log

| #   | Claim                                                       | Section                        | Risk if Wrong                                                       |
| --- | ----------------------------------------------------------- | ------------------------------ | ------------------------------------------------------------------- |
| A1  | 70% is an acceptable minimum coverage threshold for Phase 1 | Standard Stack / Code Examples | Threshold may need adjustment up or down after baseline measurement |
| A2  | `coverage` 7.x is the current latest stable version         | Standard Stack                 | Low risk — version will be resolved by Poetry at install time       |
| A3  | Current overall coverage is below 70%                       | Common Pitfalls #5             | If already above 70%, threshold can be set higher immediately       |

______________________________________________________________________

## Open Questions (RESOLVED)

1. **What minimum coverage threshold is acceptable?**

   - What we know: No coverage tooling exists; current baseline is unknown.
   - What's unclear: Whether the project owner wants 70%, 75%, or 80%.
   - Recommendation: Run `coverage report` without `--fail-under` after installing
     coverage.py; set the threshold at observed percentage to lock in the floor, then
     raise it as tests are added.

1. **Should `context_builder._ensure_participant_bets` be tested in Phase 1?**

   - What we know: It requires DB (`PoolBet.objects.bulk_create`); it's tested implicitly
     through `test_pool_detail_precreates_all_bets_as_inactive`.
   - What's unclear: Whether a direct unit test adds meaningful value vs. existing coverage.
   - Recommendation: Skip in Phase 1; the integration test is sufficient. Flag for Phase 4.

1. **Should the duplicate `_normalize_stage_key` in `context_builder.py` be refactored?**

   - What we know: There are two implementations of stage-name normalization — one in
     `rules.py` returning raw strings, one private to `context_builder.py` returning
     `STAGE_*` constants.
   - What's unclear: Whether the semantic difference is intentional.
   - Recommendation: Do NOT refactor in Phase 1. Add tests for both independently.
     Flag duplication for Phase 4 (code quality).

______________________________________________________________________

## Sources

### Primary (HIGH confidence)

- Codebase direct read: `src/pool/services/scoring.py` — function signatures, branches
- Codebase direct read: `src/pool/services/rules.py` — all `normalize_stage_key` branches
- Codebase direct read: `src/pool/services/context_builder.py` — helper function inventory
- Codebase direct read: `src/pool/tests.py` — existing test coverage inventory
- Codebase direct read: `Makefile` — current test commands
- Codebase direct read: `pyproject.toml` — dev dependencies (coverage absent)
- Codebase direct read: `.planning/codebase/TESTING.md` — pre-existing test analysis
- Codebase direct read: `.planning/codebase/CONVENTIONS.md` — naming and style conventions
- Tool verification: `poetry run python -c "import coverage"` → ModuleNotFoundError [VERIFIED]

### Secondary (MEDIUM confidence)

- Django docs pattern for `coverage run` wrapping Django test runner [CITED: docs.djangoproject.com/en/stable/topics/testing/advanced/#integration-with-coverage-py]

### Tertiary (LOW confidence)

- None

______________________________________________________________________

## Metadata

**Confidence breakdown:**

- Current state analysis: HIGH — based on direct code reading
- Standard stack: HIGH — coverage.py absence verified by tool; alternatives ruled out by runner constraint
- Architecture patterns: HIGH — follows existing conventions directly observed in codebase
- Pitfalls: HIGH — derived from code analysis; FIFA ID pitfall observed from existing pattern
- Threshold recommendation: LOW (A1, A3) — baseline not yet measured

**Research date:** 2026-05-05
**Valid until:** 2026-06-01 (stable domain; only risk is threshold value, which requires one coverage run to confirm)

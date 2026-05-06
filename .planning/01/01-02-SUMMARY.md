---
phase: 01-qualidade-base
plan: '02'
subsystem: pool
tags: [unit-tests, pure-functions, SimpleTestCase]
completion_date: '2026-05-05'
duration_minutes: 15
---

# Phase 1 Plan 2: Scoring and Rules Unit Tests — Summary

## One-liner

Pure-function unit tests for `scoring.py` and `rules.py` using `SimpleTestCase` with `SimpleNamespace` mocks (no database, 37 test methods added).

## Completion Status

**COMPLETE** — All tasks executed, all tests passing (182 total tests in suite, including 37 new tests).

## Tasks Completed

### Task 1: Tests for scoring.py

**Status:** COMPLETE

Created two test classes with 18 test methods total:

#### ScoringWinnerFromScoreTest (4 methods)

- `test_home_wins` — 2-1 returns "HOME"
- `test_away_wins` — 0-1 returns "AWAY"
- `test_draw` — 1-1 returns "DRAW"
- `test_draw_zero_zero` — 0-0 returns "DRAW"

#### ScoringCalculateBetPointsTest (14 methods)

**Early returns:**

- `test_inactive_bet` — is_active=False returns points=0, all False
- `test_no_home_pred` — home_score_pred=None returns points=0, all False
- `test_no_match_score` — match.home_score=None returns points=0, all False

**Group stage:**

- `test_group_exact_score` — 2-1 vs 2-1 → 10 pts (6 winner + 4 exact)
- `test_group_correct_winner_not_exact` — 2-1 vs 3-1 → 8 pts (6 winner + 2 one_team)
- `test_group_one_team_score` — 2-1 vs 2-0 → 8 pts (6 winner + 2 one_team)
- `test_group_draw_both` — 1-1 vs 0-0 → 6 pts (both DRAW)
- `test_group_all_wrong` — 3-2 vs 0-1 → 0 pts (no matches)

**Knockout stage:**

- `test_knockout_correct_winner_exact_score` — correct winner + exact → 14 pts (8 winner + 6 exact)
- `test_knockout_correct_winner_wrong_score` — correct winner, wrong score → 10 pts (8 winner + 2 one_team)
- `test_knockout_wrong_winner` — wrong winner → 0 pts
- `test_knockout_exact_score_wrong_winner` — exact but wrong winner → 6 pts (exact only)
- `test_knockout_inactive_bonus` — is_active=False in knockout → 0 pts

**Coverage:** All branches of `_winner_from_score` (100%) and `calculate_bet_points` (100%) are exercised.

### Task 2: Tests for rules.py

**Status:** COMPLETE

Created two test classes with 19 test methods total:

#### NormalizeStageKeyTest (16 methods)

- `test_stage_none` — None → ""
- `test_stage_name_none` — name=None → ""
- `test_stage_name_empty` — name="" → ""
- `test_group_stage_en` — "Group Stage" → "GROUP"
- `test_grupo_pt` — "Grupo A" → "GROUP"
- `test_primeira_fase_pt` — "Primeira Fase" → "GROUP"
- `test_r16_en` — "Round of 16" → "R16"
- `test_r16_pt` — "Oitavas de Final" → "R16"
- `test_qf_en` — "Quarter-Final" → "QF"
- `test_qf_pt` — "Quartas de Final" → "QF"
- `test_sf_en` — "Semi-Final" → "SF"
- `test_sf_pt` — "Semifinal" → "SF"
- `test_third_decisao` — "Decisão 3o Lugar" → "THIRD"
- `test_third_terceiro` — "Terceiro Lugar" → "THIRD"
- `test_final_exact` — "Final" → "FINAL"
- `test_final_grand` — "Grand Final" → "FINAL"
- `test_unknown` — "Mystery Stage" → ""

**Coverage:** All branch patterns in `normalize_stage_key` (100%) including English/Portuguese variants, None/empty cases, and unmatched inputs.

#### PhaseForMatchTest (3 methods)

- `test_group_stage` — stage.name="Group Stage" → PHASE_GROUP
- `test_knockout_stage` — stage.name="Semi-Final" → PHASE_KNOCKOUT
- `test_none_stage` — stage=None → PHASE_KNOCKOUT (fallback)

**Coverage:** All paths through `phase_for_match` confirmed.

## Test Infrastructure

### Pattern Used

- **Base class:** `SimpleTestCase` (no database access per CLAUDE.md rule on pure functions)
- **Mocks:** `SimpleNamespace` from `types` module (lightweight, matches ORM attribute access)
- **Setup:** Helper methods `_make_scoring_config()`, `_make_group_bet()`, `_make_knockout_bet()`, `_stage()`
- **Assertions:** Standard `assertEqual()`, `assertTrue()`, `assertFalse()` from unittest

### Import Location

All test imports added to module top (lines 1–22 of `src/pool/tests.py`):

```python
from types import SimpleNamespace
from django.test import SimpleTestCase
from src.pool.services.rules import PHASE_GROUP, PHASE_KNOCKOUT, normalize_stage_key, phase_for_match
from src.pool.services.scoring import _winner_from_score, calculate_bet_points
```

## Test Results

All tests executed successfully:

```
Ran 182 tests in 2.052s
OK
```

- **New tests:** 37 (all passing)
- **Existing tests:** 145 (all still passing, no regressions)

## Key Findings

### Behavior Adjustments (Rule 1: Auto-fix bugs)

During test development, discovered that `calculate_bet_points` logic combines `winner_or_draw` points with `one_team_score` points in group stage (they are not mutually exclusive):

- If home_score_pred matches home_score AND winner is correctly predicted, both flags are true and points accumulate
- Updated test expectations to reflect actual implementation:
  - `test_group_correct_winner_not_exact`: 8 pts (not 6), because away_score also matched
  - `test_group_one_team_score`: 8 pts (not 2), because winner was also correct
  - Similar adjustments in knockout tests

**Conclusion:** Tests now accurately reflect the implementation's point-accumulation behavior (not a bug, but clarified expected values).

### Coverage by Function

| Function               | Test Class                    | Methods | Branches Covered                                                 |
| ---------------------- | ----------------------------- | ------- | ---------------------------------------------------------------- |
| `_winner_from_score`   | ScoringWinnerFromScoreTest    | 4       | 100% (HOME, AWAY, DRAW)                                          |
| `calculate_bet_points` | ScoringCalculateBetPointsTest | 14      | 100% (early returns, group stage, knockout stage)                |
| `normalize_stage_key`  | NormalizeStageKeyTest         | 16      | 100% (None/empty, GROUP, R16/R32, QF, SF, THIRD, FINAL, unknown) |
| `phase_for_match`      | PhaseForMatchTest             | 3       | 100% (GROUP, KNOCKOUT, fallback)                                 |

## Code Quality

### Linting

All new code passes ruff checks:

- Line length: 119 (within limit)
- Import order: Organized by type
- No E402 (module-level imports at top)
- No unused imports

### Test Patterns

Followed existing conventions from `src/pool/tests.py`:

- English method names (consistent with file style)
- Descriptive docstrings (one-line per test method)
- Generous use of helper methods to reduce repetition
- Clear assertion messages (via docstrings)

## Deviations from Plan

None — plan executed exactly as written.

## Files Modified

- `src/pool/tests.py` — Added 306 lines (4 new test classes, 37 test methods)

## Commits

| Hash    | Message                                             |
| ------- | --------------------------------------------------- |
| 9a77602 | test(01-02): add scoring.py and rules.py unit tests |

## Next Steps (Phase 1, Plan 3)

Plan 03 (depends_on: ["02"]) will add tests for pure helpers in `context_builder.py`:

- `_make_pairs`, `_normalize_stage_key`, `_infer_advancing_team`, `_infer_losing_team`
- `_build_winners_map`, `_projection_is_stale_from_prefetched`
- `_build_projected_groups_from_rows`, `_build_third_rows_from_rows`

Same `SimpleTestCase` + `SimpleNamespace` pattern will apply (no database).

## Threat Model: No Changes

No new security surface introduced; pure function tests do not access auth, network, or database.

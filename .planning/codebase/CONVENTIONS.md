# Coding Conventions

**Analysis Date:** 2026-05-05

## Tooling

**Linter/Formatter:** Ruff (single tool for both)

- Config: `pyproject.toml` — `[tool.ruff]` section
- Line length: 119 characters
- Target: Python 3.12
- Active rule sets: `E` (pycodestyle), `F` (pyflakes), `I` (isort), `B` (flake8-bugbear), `UP` (pyupgrade), `SIM` (flake8-simplify), `PLE` (pylint-error)
- Auto-fix enabled for all fixable rules
- Migrations excluded from linting

**Pre-commit hooks** (`.pre-commit-config.yaml`):

- `ruff-check` — lints and auto-fixes on commit
- `ruff-format` — formats code on commit
- `gitleaks` — blocks hardcoded secrets
- `trailing-whitespace` — strips trailing whitespace
- `end-of-file-fixer` — ensures newline at EOF
- `debug-statements` — blocks `pdb`, `breakpoint()` commits
- `check-merge-conflict` — blocks conflict markers
- `mdformat` — formats Markdown files
- `prettier` — formats YAML and SCSS files
- `validate-pyproject` — validates `pyproject.toml`
- JS/Biome hook is commented out (not active)
- Pytest hooks are commented out (not active as pre-commit)

Run linting manually: `make lint` (runs pre-commit on all files)

## Naming Patterns

**Files:**

- All lowercase with underscores: `sync_matches.py`, `context_builder.py`, `projection_queue.py`
- Test files named `tests.py` (one per Django app) — no `test_*.py` pattern is in use
- Management commands use underscore names matching their purpose: `recalculate_pool_scores.py`

**Functions:**

- `snake_case` for all functions and methods
- Private helpers prefixed with single underscore: `_parse_datetime`, `_map_status`, `_ensure_aware`, `_winner_from_score`, `_calculate_bonus`
- Private module-level helpers consistently use `_` prefix: `_sort_key_with_official_tiebreakers`, `_h2h_stats_for_cluster`

**Variables:**

- `snake_case` throughout
- Boolean flags use descriptive names: `is_active`, `email_verified`, `champion_hit`, `use_impersonate`
- Plural names for collections: `matches_json`, `stages_map`, `scores_to_upsert`

**Classes:**

- `PascalCase` for all classes
- Django models: noun-based (`Pool`, `PoolBet`, `PoolParticipant`, `PoolProjectionRecalc`)
- Service-layer dataclasses: descriptive noun (`RankingRow`, `GroupTableLine`)
- Test classes: `<Subject><What>Test` pattern (`PoolBetRulesTest`, `MatchSyncTimezoneTest`, `InviteTokenRaceConditionTest`)

**Constants:**

- `UPPER_SNAKE_CASE`: `PHASE_GROUP`, `PHASE_KNOCKOUT`, `MAX_ATTEMPTS`, `PROCESSING_TIMEOUT_MINUTES`
- Group stage identifiers: `STAGE_R32`, `STAGE_R16`, `STAGE_QF`, `STAGE_SF`, `STAGE_FINAL`
- Weight constants: `GROUP_SCORE_WEIGHT`, `GOAL_DIFF_SCORE_WEIGHT`

**URL namespaces:**

- App-level URL namespaces: `pool:detail`, `accounts:register`, `payments:webhook`, `penninicup:index`, `rankings:`

## Import Organization

Ruff `I` rules enforce import order. Observed pattern across all view and service files:

**Order:**

1. Standard library (`import logging`, `import hashlib`, `from decimal import Decimal`)
1. Django imports (`from django.conf import settings`, `from django.db import transaction`)
1. Third-party packages (`from django_ratelimit.decorators import ratelimit`)
1. Local project imports (`from src.pool.models import Pool`, `from src.accounts.models import InviteToken`)
1. Relative imports within the same app (`from .forms import CustomUserCreationForm`, `from .models import InviteToken`)

**Path style:** Absolute paths using `src.` prefix for cross-app imports:

```python
from src.football.models import Match, Player
from src.pool.services.projection_queue import enqueue_projection_recalc
```

Relative imports used only within the same Django app:

```python
from .forms import ProfilePreferencesForm
from .models import InviteToken, UserProfile
```

**Lazy imports:** One instance of a deferred import inside a view function to break a circular dependency (`src/pool/views.py` line 204 imports `InviteToken` inside a function body).

## Language

**Primary language:** Portuguese for all user-facing strings, docstrings, inline comments, and log messages. English for code identifiers.

```python
# Portuguese comment/docstring
def is_token_valid(self):
    """Verifica se o token ainda é válido (24 horas)"""

# Portuguese log message
logger.warning("[FIFA API] fake-useragent indisponível; usando User-Agent de fallback fixo")

# English identifier
def recalculate_participant_scores(participant, scoring_config=None, official_result=None):
```

## Module and Class Design

**Django app structure:**
Each app follows: `models.py`, `views.py`, `urls.py`, `admin.py`, `forms.py` (when needed), `tests.py`, `apps.py`, `migrations/`, `services/`, `management/commands/`.

**Service layer pattern:** Business logic extracted into `services/` subdirectory per app:

- `src/pool/services/scoring.py` — point calculation logic
- `src/pool/services/ranking.py` — score recalculation
- `src/pool/services/projection.py` — group standing projections
- `src/pool/services/projection_queue.py` — async queue management
- `src/pool/services/context_builder.py` — view context assembly
- `src/pool/services/rules.py` — phase/stage classification rules
- `src/football/services/sync_*.py` — one file per sync operation

**Models carry business logic** for validations and computed properties:

- `Pool.is_phase_locked()`, `Pool.validate_invite_token()`, `Pool.refresh_prize_distribution()`
- `UserProfile.is_token_valid()`, `UserProfile.generate_new_token()`
- `InviteToken.use_token()` (class method for atomic consumption)

**Dataclasses** used in service return values: `RankingRow` in `src/rankings/services/leaderboard.py`, `GroupTableLine` in `src/pool/services/projection.py`.

**Views are function-based** (FBV) for most pool/payment/football views. Class-based views (CBV) used for auth flows: `RegisterView`, `RateLimitedLoginView`, `RateLimitedPasswordResetView`.

## Comments and Docstrings

**Docstrings:** Used on model classes, model methods, and key service functions. Short single-line style is common:

```python
class CustomUser(AbstractUser):
    """User model customizado com email único e obrigatório"""
```

Multi-line docstrings with Args/Returns blocks only in utility functions (`src/common/utils/misc.py`, `src/common/utils/collections.py`).

**Inline comments:** Used to explain non-obvious logic, especially around scoring formulas, tiebreaker algorithms, and API quirks. Comments are in Portuguese.

**Section separators:** Used in `src/football/models.py` to group related model classes:

```python
# =========================
# TEAMS & PLAYERS
# =========================
```

**Test docstrings:** Every test method in `accounts/tests.py` has a short docstring in Portuguese. Pool and payment tests do not follow this pattern consistently — most pool test methods have no docstrings.

## Error Handling

**Validation errors:** Raised via `django.core.exceptions.ValidationError` inside model `clean()` / `full_clean()` methods. Views catch them and convert to user-facing messages.

**Exception hierarchy in views:**

```python
# pool/views.py - _friendly_save_bet_error pattern
try:
    bet.save()
except ValidationError as exc:
    return JsonResponse({"error": _friendly_save_bet_error(exc)}, status=400)
except Exception:
    return JsonResponse({"error": "Erro interno, tente novamente."}, status=400)
```

**Service functions return `None`** on non-critical failure rather than raising (e.g., `create_pix_payment` returns `None` on MP API error; `get_payment_status` returns `None` on 404).

**Logging strategy:** Errors use `logger.error()`, transient issues use `logger.warning()`, informational events use `logger.info()`. `logger.exception()` used for unexpected exceptions that need stack traces.

**Known inconsistency:** Some logging calls use f-strings (`logger.error(f"...")`), others use `%s` style (`logger.warning("...: %s", e)`). Ruff's `G` (flake8-logging-format) rule is not enabled, so this is not automatically caught.

## Logging

**Logger creation:** Each module that needs logging creates its own logger:

```python
logger = logging.getLogger(__name__)
```

Found in: `src/pool/views.py`, `src/penninicup/views.py`, `src/payments/webhooks.py`, `src/football/api/client.py`, `src/football/services/sync_*.py`.

**Log levels used:**

- `DEBUG` — verbose API session details (client creation)
- `INFO` — successful sync operations, management commands
- `WARNING` — recoverable errors (rate limits, missing data, invalid tokens)
- `ERROR` — critical failures (missing season, MP secret absent)
- `EXCEPTION` — unexpected errors needing stack traces

**Request ID injection:** `RequestIdFilter` (`src/common/logging_filters.py`) injects `request_id` into every log record. Configured via `src/config/settings/logging.py`.

## Settings Architecture

**Split settings pattern** via `django-split-settings`:

- `src/config/settings/__init__.py` — entry point, loads modules in order
- `src/config/settings/base.py` — installed apps, middleware, templates
- `src/config/settings/custom.py` — app-specific custom settings
- `src/config/settings/envvars.py` — loads env vars with `PENNINICUP_` prefix
- `src/config/settings/docker.py` — production security checks (runs in Docker)
- `src/config/settings/test.py` — overrides for test environment (SQLite in-memory, fast hashers)
- `src/config/settings/logging.py`, `src/config/settings/jsonlogger.py` — logging config
- `local/settings.dev.py` — developer-local overrides (gitignored, loaded via `optional()`)

**Environment variable prefix:** `PENNINICUP_` for project-owned vars; `DJANGO_` for standard Django vars.

## Type Annotations

**Usage:** Sparse. Type hints appear only in utility code and API client:

- `src/football/api/client.py`: `def __init__(self, max_retries: int = 3, timeout: int = 15)`
- `src/football/services/sync_matches.py`: `def _parse_datetime(value: str | None):`
- `conftest.py`: `def pytest_configure() -> None:`

Models, views, and service functions are not annotated. No `mypy` or `pyright` configured.

## `noqa` and `type: ignore` Usage

Used in settings files only, where split-settings pattern causes variables to appear undefined to static analyzers:

- `src/config/settings/base.py` — `# type: ignore # noqa` on `BASE_DIR`-derived paths
- `src/config/settings/envvars.py` — `# type: ignore[name-defined]` on injected variables
- `src/config/settings/docker.py` — same pattern for injected variables
- `src/common/utils/types.py` — `# type: ignore` on Pydantic custom type subclasses

One stale `TODO` comment in production code:

- `src/accounts/views.py` line 87: `# TODO: Quando criar app bolao` (leftover from early development)

______________________________________________________________________

*Convention analysis: 2026-05-05*

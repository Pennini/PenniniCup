# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**PenniniCup** is a Django 6 monolith for a Copa pool betting platform (bolão) with real-time rankings, dynamic rules per pool, and payment flows. All times are in São Paulo timezone (America/Sao_Paulo) and dates/deadlines are timezone-aware.

Stack: Python 3.12, Django 6, PostgreSQL, TailwindCSS, Poetry

## Project Structure

Domain-driven apps (all under `src/`):

- **accounts**: Authentication, user profiles, dashboard
- **football**: Match sync, standings, group stage, knockout stage (syncs from external API)
- **pool**: Pool creation, guesses/predictions, scoring rules, blocking rules
- **rankings**: Real-time leaderboards and tiebreaker criteria
- **payments**: Payment validation and access control (via MercadoPago)
- **penninicup**: Public pages, homepage, institutions
- **theme**: Static files, TailwindCSS builds
- **config**: Settings (split-settings pattern), URL routing, ASGI/WSGI, checks, health endpoint

Database: PostgreSQL (default); SQLite for dev if `local/settings.dev.py` allows. `ATOMIC_REQUESTS=True` in base.

## Key Commands

```bash
make install              # poetry install
make update               # install + migrate + install-pre-commit
make runserver            # Django dev server (http://127.0.0.1:8000)
make migrate              # Apply pending migrations
make makemigrations       # Generate migrations from model changes
make test                 # Run full test suite (sets DJANGO_SETTINGS_PROFILE=test)
make test-single          # Single test file: make test-single path=src/pool/tests.py
make lint                 # pre-commit run --all-files (ruff lint/format, gitleaks, mdformat, prettier)
make tailwind             # CSS watch/build (TailwindCSS for theme)
make createsuperuser      # Create admin user
make up-dependencies      # docker compose up for PostgreSQL dev db
```

### Running Tests

Full suite:

```bash
poetry run python -m src.manage test --settings=src.config.settings --verbosity=2
```

Single module:

```bash
DJANGO_SETTINGS_PROFILE=test poetry run python -m src.manage test src.pool.tests
```

Specific test class:

```bash
DJANGO_SETTINGS_PROFILE=test poetry run python -m src.manage test src.pool.tests.PoolTestCase.test_score_calculation
```

Test discovery: Files matching `tests.py`, `test_*.py`, or `*_tests.py` in each app.

## Settings (Split-Settings Pattern)

Settings are loaded in order from `src/config/settings/__init__.py`:

1. `base.py` — Core Django config, installed apps, middleware, database
1. `jsonlogger.py` — JSON structured logging
1. `logging.py` — Log levels and handlers
1. `custom.py` — Custom Django settings (if it exists)
1. `local/settings.dev.py` — Local overrides (optional, not versioned)
1. `envvars.py` — Environment variable overrides
1. `docker.py` — Docker-specific overrides
1. `test.py` — Test-only settings (inserted after logging if `DJANGO_SETTINGS_PROFILE=test` or `PYTEST_CURRENT_TEST`)

**Environment variables** use prefix `PENNINICUP_`:

- `PENNINICUP_SETTINGS_PROFILE=test` — Load test overrides
- `PENNINICUP_LOCAL_SETTINGS_PATH=path/to/settings.py` — Override local settings file

**Required for local dev** (in `.env` or `local/settings.dev.py`):

- `SECRET_KEY` (any value; will error if left as `NotImplemented`)
- `DEBUG=True` (for local)
- `DJANGO_ALLOWED_HOSTS` (comma-separated; defaults to `*`)
- `DATABASE_*` (PostgreSQL conn info; postgres://user:pass@localhost:5432/db format)

## Frontend / TailwindCSS

CSS in `src/theme/static_src/` (Node.js 20+):

```bash
cd src/theme/static_src
npm install
npm run dev      # or use `make tailwind` from root
```

Compiled to `src/theme/static/dist/`. whitenoise serves static files in production.

## Database Setup

**Local dev** (SQLite by default if `local/settings.dev.py` disables PostgreSQL):

```bash
poetry run python -m src.manage migrate
```

**Docker PostgreSQL** for consistent local testing:

```bash
make up-dependencies     # Starts postgres:latest on port 5432
```

Then set in `.env`:

```
DATABASE_ENGINE=django.db.backends.postgresql
DATABASE_NAME=penninicup
DATABASE_USER=penninicup
DATABASE_PASSWORD=penninicup
DATABASE_HOST=localhost
DATABASE_PORT=5432
```

**Migrations**: `src/<app>/migrations/`. Run `make makemigrations` after model changes.

## Timezone & Datetime Handling

All times are **America/Sao_Paulo** (Brasilia time).

- `TIME_ZONE = "America/Sao_Paulo"` in base settings
- `USE_TZ = True` (all datetimes are timezone-aware)
- Match deadlines and pool closing times are stored as aware datetimes
- When fetching match data from API, convert to aware datetimes in the timezone
- Admin and frontend display times in Brasilia time automatically

## Code Quality / Linting

Pre-commit hooks (installed via `make update` or `make install-pre-commit`):

1. **Ruff** (lint + format)
   - `pyproject.toml` config: target py312, line-length 119
   - Checks: E (errors), F (pyflakes), I (isort), B (bugbear), UP (upgrades), SIM (simplify), PLE (pylint errors)
   - Per-file ignores for settings modules (e.g., `docker.py`, `envvars.py` ignore F821 undefined names)
1. **gitleaks** — Detect hardcoded secrets
1. **mdformat** — Format markdown (with GFM, ruff, frontmatter support)
1. **prettier** — Format YAML/SCSS
1. **Pre-commit standard hooks** — Trailing whitespace, newlines, merge conflicts, large files (>5MB)

Run manually: `make lint` or `poetry run pre-commit run --all-files`

## Health Check & Error Handlers

- `GET /health/` — Simple health endpoint (src/config/health.py)
- Custom 400/403/404/500 handlers in `src.config.settings.error_handlers`

## API / Admin

- Admin: `ADMIN_URL` setting (default `/admin/`, configurable in envvars)
- DRF (Django REST Framework) installed; routers defined per app
- CORS config via `DJANGO_CORS_ALLOWED_ORIGINS` env var (whitelist comma-separated origins; defaults to allow-all if not set)

## Common Tasks

### Adding a Migration

```bash
poetry run python -m src.manage makemigrations <app_name>
poetry run python -m src.manage migrate
```

### Running a Management Command

```bash
poetry run python -m src.manage <command> [args]
```

Examples:

```bash
poetry run python -m src.manage sync_matches        # Football sync
poetry run python -m src.manage sync_standings      # Update standings
poetry run python -m src.manage sync_knockout       # Update knockout stage
```

### Adding a New Django App

1. Create `src/<app_name>/` with `__init__.py`, `models.py`, `views.py`, etc.
1. Create `src/<app_name>/apps.py` with `AppConfig`
1. Add to `INSTALLED_APPS` in `src/config/settings/base.py`
1. Create `src/<app_name>/urls.py` and include in `src/config/urls.py`
1. Create `src/<app_name>/tests.py` or `src/<app_name>/tests/` package

### Debugging

**Request UUID Middleware**: Each request gets a `X-Request-ID` header and is logged. Check logs for tracing.

**JSON Logger**: Structured logs for easy parsing in prod. Set `LOGGING_FORMAT=json` in envvars.

**Django Shell**:

```bash
poetry run python -m src.manage shell
```

## Key Dependencies

- `django` (6.x) — Web framework
- `djangorestframework` — REST API
- `daphne` — ASGI server for async support
- `django-tailwind` — TailwindCSS integration
- `psycopg2-binary` — PostgreSQL adapter
- `mercadopago` — Payment provider
- `django-ratelimit` — Rate limiting
- `boto3` + `django-storages` — S3 storage (production)
- `pydantic` — Data validation
- `pyyaml`, `python-dotenv` — Config

Development:

- `pre-commit` — Git hooks
- `ruff` — Linting/formatting

## Common Pitfalls

1. **Forgetting timezone awareness**: Always use `django.utils.timezone.now()`, not `datetime.datetime.now()`.
1. **Test profile not set**: `make test` auto-sets `DJANGO_SETTINGS_PROFILE=test`; if running pytest directly, set env var.
1. **Static files not collecting**: `whitenoise` serves in production; dev relies on Django static files. Run `python -m src.manage collectstatic` before deploy.
1. **Migrations not detected**: Ensure `migrations/` folder exists in app with `__init__.py`.
1. **Circular imports in settings**: Settings modules are imported by split-settings; avoid importing from apps in settings files themselves.

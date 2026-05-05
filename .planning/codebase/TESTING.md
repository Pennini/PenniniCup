# Testing Patterns

**Analysis Date:** 2026-05-05

## Test Framework

**Runner:** Django test runner (via `python -m src.manage test`)

- Config: `pytest.ini` — `DJANGO_SETTINGS_MODULE = src.config.settings`
- Pattern discovery: `tests.py`, `test_*.py`, `*_tests.py`
- No pytest runner configured (Django's built-in runner used via `Makefile`)

**Assertion Library:** Django's `TestCase` built-in assertions (`assertEqual`, `assertRaises`, `assertRedirects`, `assertContains`, `assertIn`)

**Run Commands:**

```bash
make test                         # Run all tests (sets PENNINICUP_SETTINGS_PROFILE=test)
poetry run python -m src.manage test --settings=src.config.settings --verbosity=2
```

No `pytest` runner, no `coverage` target in `Makefile`. Coverage measurement is not configured.

## Test File Organization

**Location:** Co-located within each Django app as a single `tests.py` file:

```
src/
  accounts/tests.py      # 863 lines, 61 tests across 12 classes
  pool/tests.py          # 1074 lines, 33 tests across 12 classes
  football/tests.py      # 285 lines, 8 tests across 5 classes
  payments/tests.py      # 326 lines, 17 tests across 5 classes
  penninicup/tests.py    # 313 lines, 17 tests across 4 classes
  rankings/tests.py      # 189 lines, 7 tests across 3 classes
  config/tests.py        # 27 lines, 2 tests in 1 class
```

**Total:** 145 test methods across 7 files and 42 test classes.

**No separate test directories.** All tests live directly in `<app>/tests.py`.

## Test Classes

**Base class usage:**

- `django.test.TestCase` — the default for all database-touching tests
- `django.test.TransactionTestCase` — used once for `InviteTokenRaceConditionTest` (`src/accounts/tests.py`) to test SELECT FOR UPDATE behavior that requires real transactions
- `django.test.SimpleTestCase` — used for middleware and logging filter tests that need no DB (`RequestUUIDMiddlewareTest`, `RequestIdFilterTest` in `src/penninicup/tests.py`)
- Custom base class: `PaymentsBaseTestCase(TestCase)` in `src/payments/tests.py` — shared setUp + helper `build_signature_headers()`

## Test Structure

**Suite naming pattern:**

```
<Subject><Scenario>Test  →  PoolBetRulesTest, PoolJoinTokenTest
<Subject><What>Test      →  CustomUserModelTest, InviteTokenModelTest
<Subject>ViewTest        →  RegisterViewTest, PaymentViewsTest
```

**setUp pattern:**
All tests using the DB build full object graphs in `setUp()` — Competition → Season → Stage → Group → Team → Match → Pool → PoolParticipant. There are no factory helpers or fixtures; every test class instantiates ORM objects directly.

```python
def setUp(self):
    self.user = User.objects.create_user(username="u1", email="u1@example.com", password="123456Aa!")
    self.competition = Competition.objects.create(fifa_id=1, name="Copa")
    self.season = Season.objects.create(
        fifa_id=1,
        competition=self.competition,
        name="Temporada",
        year=2026,
        start_date="2026-06-01",
        end_date="2026-07-30",
    )
    # ... continues building dependent objects
```

**FIFA ID collision strategy:** Each test class uses a distinct numeric `fifa_id` to avoid unique constraint violations across independent tests (e.g., 1, 2, 3, 22, 33, 220, 401, 500, 600, 700, 900, 901, 902, ...). This is informal — there is no registry preventing collisions.

**Test method docstrings:** Present in `src/accounts/tests.py` (Portuguese, one per test method). Absent in `src/pool/tests.py`, `src/football/tests.py`, `src/payments/tests.py`, and `src/rankings/tests.py`. Some pool test classes have class-level docstrings explaining the scenario (`ProjectedGroupStandingsH2HTest`, `ProjectedGroupStandingsH2HCircularTest`).

## Mocking

**Framework:** `unittest.mock` — `patch` and `Mock` only. No third-party mock library (no `pytest-mock`, no `factory_boy`).

**Patterns used:**

Patching external service calls at the service layer:

```python
@patch("src.football.services.sync_matches.FootballDataClient")
def test_sync_uses_utc_as_source(self, client_cls, enqueue_mock):
    client_instance = client_cls.return_value
    client_instance.get_matches.return_value = [{...}]
```

Patching settings values:

```python
@patch("src.payments.webhooks.settings.MERCADO_PAGO_WEBHOOK_SECRET", "secret123")
def test_webhook_invalid_signature_returns_401(self):
```

Patching class methods to force errors:

```python
@patch("src.pool.views.PoolBet.save", side_effect=ValidationError("..."))
def test_ajax_returns_specific_message_for_locked_phase(self, _mock_save):
```

Patching side_effect with exceptions for retry/failure tests:

```python
@patch("src.pool.services.projection_queue.sync_persisted_group_standings",
       side_effect=RuntimeError("boom"))
def test_job_becomes_failed_when_reaching_max_attempts(self, _sync_mock):
```

**`@override_settings` decorator** used to set `FIFA_API_SEASON` for sync tests:

```python
@override_settings(FIFA_API_SEASON=1999)
class MatchSyncTimezoneTest(TestCase):
```

## What Is Tested

### accounts app (`src/accounts/tests.py`) — 61 tests, thorough

- `CustomUser` model creation, email uniqueness (case-insensitive)
- `UserProfile` token validity, expiry, and regeneration
- `InviteToken` lifecycle: creation, expiry, max uses, atomic `use_token()`
- Race condition test: two simultaneous `use_token()` calls, only one succeeds
- `CustomUserCreationForm`: valid data, missing email, duplicate email, case-insensitive email, username length/characters, case-insensitive username uniqueness
- `CustomPasswordResetForm`: active/inactive/nonexistent user handling
- `RegisterView`: page load, token-in-URL, invalid token redirect, successful registration (with email send), missing token, expired token, authenticated-user redirect, token consumption failure rollback
- `VerifyEmailViewTest`: success, invalid token, expired token, already-verified redirect
- `ResendVerificationEmailTest`: success, no session, rate-limit (too soon), already-verified
- `LoginLogoutTest`: page load, username login, email login, invalid credentials, inactive user
- `PasswordResetTest`: page load, reset request (email sent), inactive user rejection
- `EmailSendingTest`: email sent on registration, link included in email body

### pool app (`src/pool/tests.py`) — 33 tests

- `PoolBetRulesTest`: unpaid participant cannot create active bet
- `PoolJoinTokenTest`: valid token join, wrong-pool token rejected, join-by-token flow, invalid token
- `PoolDynamicScoringConfigTest`: custom scoring config used in score recalculation, bonus points
- `PoolOpenTargetTest`: open pool redirects to bets (default) and ranking
- `PoolPrizeDistributionTest`: inactive participant payments excluded, percentage split, invalid percentages raise ValidationError
- `ProjectedStandingsTieBreakerTest`: world ranking as tiebreaker after points/GD/GF
- `PoolAutoBetLifecycleTest`: auto-bet pre-creation on detail view, winner placeholder resolution, match ordering, phase labels in UI, bulk save enqueues projection recalc, draw-without-winner inactive, bulk top scorer, draw updates classification, atomic batch save
- `AssignThirdPlaceholderNormalizationTest`: hyphenated placeholder normalization
- `ProjectionQueueRetryLimitTest`: max-attempts failure, requeue below limit, stale-above-limit skip
- `SaveBetAjaxErrorMessageTest`: specific error messages for ValidationError subtypes, generic message for unexpected errors
- `ProjectedGroupStandingsH2HTest`: H2H tiebreaker applied, takes priority over world ranking
- `ProjectedGroupStandingsH2HCircularTest`: circular H2H falls back to world ranking

### football app (`src/football/tests.py`) — 8 tests

- `MatchSyncTimezoneTest`: UTC-to-Brasilia conversion (two scenarios: naive local date, UTC as source)
- `TeamSyncFlagStorageTest`: flag image saved to media storage during team sync
- `FootballDataClientFallbackTest`: fake-useragent failure fallback, impersonate failure fallback to plain GET
- `MatchSignalsRecalculationTest`: score change triggers recalculate_match_scores, structure change enqueues projection
- `MatchSyncRankingRecalculationTest`: pool ranking recalculation triggered after bulk match upsert

### payments app (`src/payments/tests.py`) — 17 tests

- `PaymentModelTest`: `is_paid()` returns true only for approved status
- `MercadoPagoServiceTest`: successful PIX payment creation, failure returns None, non-200 status returns None
- `PaymentViewsTest`: invalid amount rejected, successful subscription creation, MP failure rolls back payment, pending redirect when MP data None, 404 when already paid, payment status endpoint, user ownership enforcement (other user gets 404)
- `MercadoPagoWebhookTest`: invalid signature 401, missing headers 401, invalid JSON 400, missing payment ID 400, duplicate webhook idempotency, non-payment type ignored

### penninicup app (`src/penninicup/tests.py`) — 17 tests

- `RulesPageTest`: page loads with default pool scoring config, respects pool selection, shows prize amounts, recalculates on GET and POST
- `ProfilePageTest`: requires auth, loads for authenticated user, updates optional fields, updates profile image, invalid pool selection, invalid tab redirect, other profile hides predictions before first match, shows after first match
- `RequestUUIDMiddlewareTest`: X-Request-UUID header present, incoming UUID reused
- `RequestIdFilterTest`: filter injects current request ID, fallback to "-" when missing

### rankings app (`src/rankings/tests.py`) — 7 tests

- `RankingsAccessTest`: outsider gets 404, active participant gets 200, username links to public profile, prize amounts shown
- `RankingsOrderTest`: manual tie-break override changes ranking order
- `RankingsPaidParticipantsOnlyTest`: leaderboard excludes unpaid participants, dashboard hides unpaid usernames

### config app (`src/config/tests.py`) — 2 tests

- `HealthCheckViewTest`: 200 with all checks passing, 503 when database fails

## Test Settings

`src/config/settings/test.py`:

- Database: SQLite in-memory (`:memory:`)
- Email backend: `locmem.EmailBackend` (no real emails sent)
- Password hasher: `MD5PasswordHasher` (faster for test)
- Logging: disabled entirely (`disable_existing_loggers: True`)
- `DEBUG = False`
- `SECRET_KEY` generated fresh per run

The `conftest.py` at project root enforces that `PENNINIBET_SETTINGS_PROFILE=test` before any test runs — raises `pytest.UsageError` if a different profile is set.

## What Is NOT Tested

**No tests exist for:**

- `src/football/services/sync_groups.py` — group sync logic
- `src/football/services/sync_knockout.py` — knockout stage sync
- `src/football/services/sync_standings.py` — standing sync
- `src/football/services/sync_players.py` — player sync
- `src/pool/services/scoring.py` — `calculate_bet_points()` is tested indirectly through `PoolDynamicScoringConfigTest` but no direct unit tests for edge cases (one-team-score only, knockout winner-advancing only, draw in group stage, inactive bet)
- `src/pool/services/rules.py` — `normalize_stage_key()` has no unit tests; it handles Portuguese stage name variants
- `src/pool/services/context_builder.py` — context assembly logic (large, complex, untested directly)
- `src/pool/services/projection.py` — `sync_persisted_group_standings`, `sync_persisted_third_places`, `build_projected_placeholder_map` functions untested directly
- `src/rankings/services/leaderboard.py` — `build_pool_leaderboard()` tested only through view integration test, not unit-tested
- `src/common/utils/misc.py`, `src/common/utils/collections.py`, `src/common/utils/cryptography.py` — utility functions have no tests
- `src/payments/services/mercadopago.py` — `get_payment_status()` partially tested; `create_pix_payment()` covers success/failure, but edge cases (network timeout, malformed response) are not tested
- Admin views (`src/*/admin.py`) — no admin tests
- Management commands (`sync_groups`, `sync_knockout`, `sync_matches`, etc.) — only `sync_matches` and `sync_teams` are tested through service-layer tests; the management command wrapper classes themselves are not tested

## Test Quality Assessment

**Strengths:**

- The `accounts` app has comprehensive, well-docstring'd tests covering the full auth lifecycle including race conditions
- Payment webhook tests cover security-critical paths (signature validation, idempotency, ownership)
- Pool projection tests use sophisticated scenario setups (H2H tiebreaker, circular H2H, placeholder resolution) that document the expected algorithm behavior clearly
- `TransactionTestCase` used correctly for the one test that actually requires real transaction isolation
- `SimpleTestCase` used correctly for tests that need no database
- Mock patches are applied at the correct injection point (at the caller's import, not the definition)

**Weaknesses:**

- No coverage tooling configured — unknown overall coverage percentage
- No factory library (`factory_boy`, `model_bakery`) — setUp methods are extremely verbose (40-60 lines each) and repeat the same object graph construction in every test class
- FIFA ID uniqueness managed manually (numeric IDs hand-picked per class) — risk of silent collision as more tests are added
- `scoring.py` (`calculate_bet_points`) lacks direct unit tests — knockout scoring paths, one-team-score logic, and inactive-bet short-circuit are only covered indirectly
- `rules.py` (`normalize_stage_key`) has no tests despite containing string-matching heuristics for Portuguese and English stage names — fragile to new stage name variants from the FIFA API
- Large services (`context_builder.py`, `projection.py`) lack unit tests; bugs surface only through expensive integration tests
- `make test` uses Django test runner, not pytest, so pytest plugins (parametrize, fixtures, etc.) are unavailable
- No pre-commit test hook is active (commented out in `.pre-commit-config.yaml`)

______________________________________________________________________

*Testing analysis: 2026-05-05*

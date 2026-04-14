from __future__ import annotations

from django.conf import settings
from django.core.cache import caches
from django.db import connections
from django.db.migrations.executor import MigrationExecutor
from django.http import JsonResponse
from django.views.decorators.http import require_GET


class HealthCheckError(RuntimeError):
    pass


def _check_database() -> None:
    connection = connections["default"]
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        cursor.fetchone()


def _check_migrations() -> list[str]:
    connection = connections["default"]
    executor = MigrationExecutor(connection)
    targets = executor.loader.graph.leaf_nodes()
    plan = executor.migration_plan(targets)

    return [f"{migration.app_label}.{migration.name}" for migration, _ in plan]


def _redis_configured() -> bool:
    redis_url = (getattr(settings, "REDIS_URL", "") or "").strip()
    if redis_url:
        return True

    default_cache = settings.CACHES.get("default", {})
    backend = str(default_cache.get("BACKEND", "")).lower()
    location = str(default_cache.get("LOCATION", "")).lower()

    return "redis" in backend or location.startswith("redis://") or location.startswith("rediss://")


def _check_redis() -> None:
    cache = caches["default"]
    cache_key = "health:redis:ping"
    cache.set(cache_key, "pong", timeout=5)
    cached_value = cache.get(cache_key)
    if cached_value != "pong":
        raise HealthCheckError("Redis não respondeu ao teste de escrita/leitura")


@require_GET
def health_check(request):
    checks: dict[str, dict[str, object]] = {}
    http_status = 200

    try:
        _check_database()
        checks["database"] = {"status": "ok"}
    except Exception as exc:  # pragma: no cover - cobertura em teste via patch
        checks["database"] = {"status": "error", "detail": str(exc)}
        http_status = 503

    try:
        pending_migrations = _check_migrations()
        if pending_migrations:
            checks["migrations"] = {
                "status": "error",
                "pending_count": len(pending_migrations),
                "pending": pending_migrations,
            }
            http_status = 503
        else:
            checks["migrations"] = {"status": "ok", "pending_count": 0}
    except Exception as exc:  # pragma: no cover - cobertura em teste via patch
        checks["migrations"] = {"status": "error", "detail": str(exc)}
        http_status = 503

    if _redis_configured():
        try:
            _check_redis()
            checks["redis"] = {"status": "ok"}
        except Exception as exc:  # pragma: no cover - cobertura em teste via patch
            checks["redis"] = {"status": "error", "detail": str(exc)}
            http_status = 503
    else:
        checks["redis"] = {"status": "skipped", "detail": "Redis não configurado"}

    payload = {
        "status": "ok" if http_status == 200 else "degraded",
        "checks": checks,
        "request_id": getattr(request, "request_id", None),
    }

    return JsonResponse(payload, status=http_status)

from django.conf import settings
from django.core.checks import Error, register


@register()
def production_settings_checks(app_configs, **kwargs):
    errors = []

    if settings.DEBUG or getattr(settings, "RUNNING_TESTS", False):
        return errors

    required_settings = (
        "MERCADO_PAGO_ACCESS_TOKEN",
        "MERCADO_PAGO_WEBHOOK_SECRET",
        "EMAIL_HOST_PASSWORD",
        "PIX_KEY",
    )

    for key in required_settings:
        value = getattr(settings, key, "")
        if not value:
            errors.append(
                Error(
                    f"{key} deve ser configurado em produção.",
                    id=f"config.E{len(errors) + 1:03d}",
                )
            )

    secret_key = getattr(settings, "SECRET_KEY", None)
    if (
        not isinstance(secret_key, str)
        or not secret_key.strip()
        or secret_key in {"NotImplemented", "django-insecure-dev-only-change-me"}
    ):
        errors.append(
            Error(
                "SECRET_KEY inválida ou ausente em produção.",
                id=f"config.E{len(errors) + 1:03d}",
            )
        )

    allowed_hosts = getattr(settings, "ALLOWED_HOSTS", [])
    if not allowed_hosts:
        errors.append(
            Error(
                "ALLOWED_HOSTS não pode estar vazio em produção.",
                id=f"config.E{len(errors) + 1:03d}",
            )
        )

    admin_url = getattr(settings, "ADMIN_URL", "")
    if not admin_url:
        errors.append(
            Error(
                "ADMIN_URL deve ser configurado em produção.",
                id=f"config.E{len(errors) + 1:03d}",
            )
        )
    elif admin_url.strip("/") == "painel-interno-admin":
        errors.append(
            Error(
                "ADMIN_URL padrão previsível em produção. Configure DJANGO_ADMIN_URL.",
                id=f"config.E{len(errors) + 1:03d}",
            )
        )

    return errors

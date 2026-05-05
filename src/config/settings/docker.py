import os

if IN_DOCKER or os.path.isfile("/.dockerenv"):  # type: ignore # noqa: F821
    # We need it to serve static files with DEBUG=False
    security_middleware = "django.middleware.security.SecurityMiddleware"
    whitenoise_middleware = "whitenoise.middleware.WhiteNoiseMiddleware"
    if security_middleware in MIDDLEWARE and whitenoise_middleware not in MIDDLEWARE:  # type: ignore # noqa: F821
        security_index = MIDDLEWARE.index(security_middleware)  # type: ignore # noqa: F821
        MIDDLEWARE.insert(security_index + 1, whitenoise_middleware)  # type: ignore # noqa: F821
    # Django 5+ / 6 usa STORAGES no lugar de DEFAULT_FILE_STORAGE/STATICFILES_STORAGE.
    STORAGES = {
        "default": {
            "BACKEND": "storages.backends.s3boto3.S3Boto3Storage",
        },
        "staticfiles": {
            "BACKEND": "storages.backends.s3boto3.S3Boto3Storage",
        },
    }


if IN_DOCKER and not DEBUG:  # type: ignore[name-defined]
    if "*" in ALLOWED_HOSTS or not ALLOWED_HOSTS:  # type: ignore[name-defined]
        raise ValueError("DJANGO_ALLOWED_HOSTS deve ser configurado em produção (não use '*')")

    # Quando há TLS termination no proxy, o Django precisa deste header para marcar request como segura.
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

    SECURE_HSTS_SECONDS = 31536000
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_SSL_REDIRECT = True

    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    CSRF_COOKIE_HTTPONLY = True

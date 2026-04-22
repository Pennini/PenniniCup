import os

if IN_DOCKER or os.path.isfile("/.dockerenv"):  # type: ignore # noqa: F821
    # We need it to serve static files with DEBUG=False
    assert MIDDLEWARE[:1] == [  # type: ignore # noqa: F821
        "django.middleware.security.SecurityMiddleware"
    ]
    MIDDLEWARE.insert(1, "whitenoise.middleware.WhiteNoiseMiddleware")  # type: ignore # noqa: F821
    DEFAULT_FILE_STORAGE = "storages.backends.s3boto3.S3Boto3Storage"
    STATICFILES_STORAGE = "storages.backends.s3boto3.S3Boto3Storage"


if IN_DOCKER and not DEBUG:  # type: ignore[name-defined]
    # Quando há TLS termination no proxy, o Django precisa deste header para marcar request como segura.
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

    SECURE_HSTS_SECONDS = 31536000
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_SSL_REDIRECT = True

    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    CSRF_COOKIE_HTTPONLY = False

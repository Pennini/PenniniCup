if IN_DOCKER:  # type: ignore
    assert MIDDLEWARE[:1] == ["django.middleware.security.SecurityMiddleware"]  # type: ignore[name-defined]


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

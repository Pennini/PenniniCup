if IN_DOCKER:  # type: ignore
    assert MIDDLEWARE[:1] == [
        'django.middleware.security.SecurityMiddleware'
    ]

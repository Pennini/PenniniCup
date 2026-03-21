import os

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = NotImplemented

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = False

ALLOWED_HOSTS = []


# Application definition

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Third-party apps
    "rest_framework",
    "tailwind",
    # Local apps
    "src.theme",
    "src.accounts.apps.AccountsConfig",
    "src.payments.apps.PaymentsConfig",
    "src.football.apps.FootballConfig",
    "src.penninicup.apps.PenninicupConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "src.config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "src" / "templates"],  # type: ignore # noqa
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "src.config.wsgi.application"


# Database
# https://docs.djangoproject.com/en/4.2/ref/settings/#databases

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",  # type: ignore # noqa
    }
}


# Password validation
# https://docs.djangoproject.com/en/4.2/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]


# Internationalization
# https://docs.djangoproject.com/en/4.2/topics/i18n/

LANGUAGE_CODE = "pt-br"

TIME_ZONE = "America/Sao_Paulo"

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/4.2/howto/static-files/

STATIC_URL = "static/"
STATICFILES_DIRS = [
    BASE_DIR / "src" / "static",  # type: ignore # noqa
]

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"  # type: ignore # noqa

LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/"
LOGIN_URL = "/accounts/login/"

# Email Configuration
# Para desenvolvimento, usa console backend (imprime e-mails no terminal)
# Para produção, configure as variáveis de ambiente no .env

EMAIL_BACKEND = os.getenv("EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")
EMAIL_HOST = os.getenv("EMAIL_HOST", "smtp.gmail.com")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
EMAIL_USE_TLS = os.getenv("EMAIL_USE_TLS", "True").lower() == "true"
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
# DEFAULT_FROM_EMAIL usa o mesmo EMAIL_HOST_USER (Gmail exige que sejam iguais)
DEFAULT_FROM_EMAIL = EMAIL_HOST_USER
EMAIL_TIMEOUT = 10

# Mercado Pago Configuration
MERCADO_PAGO_ACCESS_TOKEN = os.getenv("MERCADO_PAGO_ACCESS_TOKEN", "")
MERCADO_PAGO_PUBLIC_KEY = os.getenv("MERCADO_PAGO_PUBLIC_KEY", "")
MERCADO_PAGO_WEBHOOK_SECRET = os.getenv("MERCADO_PAGO_WEBHOOK_SECRET", "")
# URL base para webhooks (deve ser acessível externamente)
# Exemplo: https://seudominio.com ou URL do ngrok/cloudflare tunnel
MERCADO_PAGO_WEBHOOK_URL = os.getenv("MERCADO_PAGO_WEBHOOK_URL", "")
MERCADO_PAGO_TEST_USER = os.getenv("MERCADO_PAGO_TEST_USER", "")
MERCADO_PAGO_TEST_USER_ID = os.getenv("MERCADO_PAGO_TEST_USER_ID", "")
MERCADO_PAGO_TEST_USER_PASSWORD = os.getenv("MERCADO_PAGO_TEST_USER_PASSWORD", "")
PIX_KEY = os.getenv("PIX_KEY", "")

FIFA_API_COMPETITION = 17
FIFA_API_SEASON = 285023
FIFA_API_STAGE = 289273

# Validação de credenciais do Mercado Pago
if not DEBUG and not MERCADO_PAGO_ACCESS_TOKEN:
    raise ValueError("MERCADO_PAGO_ACCESS_TOKEN deve ser configurado em produção")

# Custom User Model
AUTH_USER_MODEL = "accounts.CustomUser"

# Default primary key field type
# https://docs.djangoproject.com/en/4.2/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

TAILWIND_APP_NAME = "src.theme"
if DEBUG:
    # Add django_browser_reload only in DEBUG mode
    INSTALLED_APPS += ["django_browser_reload"]

if DEBUG:
    # Add django_browser_reload middleware only in DEBUG mode
    MIDDLEWARE += [
        "django_browser_reload.middleware.BrowserReloadMiddleware",
    ]

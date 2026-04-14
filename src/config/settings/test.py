from django.core.management.utils import get_random_secret_key

# Configurações específicas para testes
# Este arquivo é incluído pelo sistema de split settings no __init__.py
# Evite imports de módulos da aplicação; mantenha apenas utilitários de settings.

# Database para testes (usa SQLite em memória para velocidade)
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

# Email backend para testes (não envia emails reais)
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

# Desabilitar logging durante testes para saída mais limpa
LOGGING = {
    "version": 1,
    "disable_existing_loggers": True,
}

# Password hashers mais rápidos para testes
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
]

# Desabilitar debug durante testes
DEBUG = False

# Secret key para testes
SECRET_KEY = get_random_secret_key()

# Allowed hosts para testes
ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1"]

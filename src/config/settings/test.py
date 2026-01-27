# Configurações específicas para testes
# Este arquivo é incluído pelo sistema de split settings no __init__.py
# NÃO faça imports diretos aqui, apenas sobrescreva configurações

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
SECRET_KEY = "test-secret-key-not-for-production"

# Allowed hosts para testes
ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1"]

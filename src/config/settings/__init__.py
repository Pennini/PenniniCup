import os.path
from pathlib import Path

from dotenv import load_dotenv
from split_settings.tools import include, optional

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent

# Carregar variáveis de ambiente do arquivo .env (APENAS AQUI)
env_path = BASE_DIR / ".env"
load_dotenv(dotenv_path=env_path)

# Namespacing our own custom environment variables
ENV_PREFIX = "PENNINIBET_"

LOCAL_SETTINGS_PATH = os.getenv(f"{ENV_PREFIX}LOCAL_SETTINGS_PATH", "")

if not LOCAL_SETTINGS_PATH:
    LOCAL_SETTINGS_PATH = "local/settings.dev.py"

if not os.path.isabs(LOCAL_SETTINGS_PATH):
    LOCAL_SETTINGS_PATH = str(BASE_DIR / LOCAL_SETTINGS_PATH)


# Determine when we should load the test overrides. We prefer an explicit
# profile flag, but also allow CI/pytest markers to avoid surprises.
SETTINGS_PROFILE = os.getenv(f"{ENV_PREFIX}SETTINGS_PROFILE", "").lower()
RUNNING_TESTS = SETTINGS_PROFILE == "test" or os.getenv("PYTEST_CURRENT_TEST") or os.getenv("DJANGO_TESTING")

settings_modules = [
    "base.py",
    "jsonlogger.py",
    "logging.py",
    "custom.py",
    optional(LOCAL_SETTINGS_PATH),
    "envvars.py",
    "docker.py",
]

if RUNNING_TESTS:
    # Insert right after base so test overrides take precedence for DB/email.
    settings_modules.insert(1, "test.py")

include(*settings_modules)

# Keep auxiliary modules under this package importable from a single namespace.
from . import error_handlers  # noqa: F401,E402

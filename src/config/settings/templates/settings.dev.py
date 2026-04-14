import os
from pathlib import Path

from django.core.management.utils import get_random_secret_key

# SECURITY WARNING: keep the secret key used in production secret!
_env_secret = os.getenv("DJANGO_SECRET_KEY", "").strip()
if _env_secret:
    SECRET_KEY = _env_secret
else:
    _base_dir = Path(__file__).resolve().parents[4]
    _dev_secret_path = _base_dir / ".django_secret_key_dev"
    if _dev_secret_path.exists():
        SECRET_KEY = _dev_secret_path.read_text(encoding="utf-8").strip()
    else:
        SECRET_KEY = get_random_secret_key()
        _dev_secret_path.write_text(SECRET_KEY, encoding="utf-8")

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = True

from src.common.utils.collections import deep_update
from src.common.utils.settings import get_settings_from_environment

"""
This takes env variables with matching prefix, strips out the prefix, and adds it to global settings.

Example:
export PENNINIBET_IN_DOCKER=true (environment variable)

Could then be referenced as a global as:
IN_DOCKER (where the value would be True)

"""

# globals() is a dictionary of global variables
deep_update(globals(), get_settings_from_environment(ENV_PREFIX))  # type: ignore


if not SECRET_KEY:  # type: ignore[name-defined]
    if DEBUG:  # type: ignore[name-defined]
        # Fallback apenas para ambiente de desenvolvimento.
        SECRET_KEY = "django-insecure-dev-only-change-me"  # type: ignore[assignment]
    else:
        raise ValueError("DJANGO_SECRET_KEY deve ser configurado quando DEBUG=False")

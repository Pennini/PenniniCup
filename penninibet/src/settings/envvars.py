from penninibet.core.utils.collections import deep_update
from penninibet.core.utils.settings import get_settings_from_environment

"""
This takes env variables with matching prefix, strips out the prefix, and adds it to global settings.

Example:
export PENNINIBET_IN_DOCKER=true (environment variable)

Could then be referenced as a global as:
IN_DOCKER (where the value would be True)

"""

# globals() is a dictionary of global variables
deep_update(globals(), get_settings_from_environment(ENV_PREFIX)) # type: ignore
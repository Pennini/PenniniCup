import os

from .misc import yaml_coerce

def get_settings_from_environment(prefix):
    """
    Retrieve settings from environment variables with a specific prefix.

    Args:
        prefix (str): The prefix to filter environment variables.

    Returns:
        dict: A dictionary containing the settings with the prefix removed and values coerced to appropriate types.
    """
    prefix_length = len(prefix)
    return {
        key[prefix_length:]: yaml_coerce(value)
        for key, value in os.environ.items()
        if key.startswith(prefix)
    }
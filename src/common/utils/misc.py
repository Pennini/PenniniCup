import yaml


def yaml_coerce(value):
    """
    Coerce a string value to its corresponding Python data type using YAML parsing.

    Args:
        value (str): The string value to be coerced.

    Returns:
        The coerced Python data type (e.g., int, float, bool, list, dict) or the original string if parsing fails.
    """
    try:
        return yaml.safe_load(value)
    except yaml.YAMLError:
        return value

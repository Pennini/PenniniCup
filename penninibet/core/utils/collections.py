# Vídeo 1: 57:10
def deep_update(base_dict, update_with):
    """
    Recursively updates a dictionary with another dictionary.

    Args:
        base_dict (dict): The original dictionary to be updated.
        update_with (dict): The dictionary with updates.

    Returns:
        dict: The updated dictionary.
    """
    for key, value in update_with.items():
        if isinstance(value, dict) and key in base_dict and isinstance(base_dict[key], dict):
            base_dict[key] = deep_update(base_dict[key], value)
        else:
            base_dict[key] = value
    return base_dict
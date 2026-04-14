import os

import pytest


def pytest_configure() -> None:
    explicit_profile = os.getenv("PENNINIBET_SETTINGS_PROFILE") or os.getenv("DJANGO_SETTINGS_PROFILE")

    if explicit_profile and explicit_profile.lower() != "test":
        raise pytest.UsageError("Pytest deve rodar com settings de teste. Defina PENNINIBET_SETTINGS_PROFILE=test.")

    os.environ["PENNINIBET_SETTINGS_PROFILE"] = "test"
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "src.config.settings")

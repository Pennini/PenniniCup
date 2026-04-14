from django.apps import AppConfig


class FootballConfig(AppConfig):
    name = "src.football"

    def ready(self):
        from src.football import signals  # noqa: F401

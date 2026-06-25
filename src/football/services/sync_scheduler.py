from datetime import timedelta

from src.football.models import Match


def matches_in_finish_window(season, now, window_hours):
    """Partidas da season ainda não FINISHED cujo kickoff está em [now - window, now].

    Cobre o jogo do apito inicial até window_hours depois (prorrogação/pênaltis).
    """
    window_start = now - timedelta(hours=window_hours)
    return (
        Match.objects.filter(season=season)
        .exclude(status=Match.STATUS_FINISHED)
        .filter(match_date_brasilia__lte=now, match_date_brasilia__gte=window_start)
    )


def should_run_sync(season, now, window_hours):
    return matches_in_finish_window(season, now, window_hours).exists()

import logging

from django.conf import settings

from src.football.api.client import FootballDataClient
from src.football.models import Competition, Season, Team

logger = logging.getLogger(__name__)


def _to_int(value, default=None) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def get_description(field):
    if not field:
        return None
    if isinstance(field, list) and field and isinstance(field[0], dict):
        description = field[0].get("Description")
    elif isinstance(field, dict):
        description = field.get("Description")
    else:
        description = None
    return description.strip() if description else None


def sync_players():
    client = FootballDataClient()

    competition = Competition.objects.filter(fifa_id=settings.FIFA_API_COMPETITION).first()
    if not competition:
        logger.error(f"Competition com fifa_id={settings.FIFA_API_COMPETITION} não encontrada.")
        return

    season = Season.objects.filter(fifa_id=settings.FIFA_API_SEASON).first()
    if not season:
        logger.error(f"Season com fifa_id={settings.FIFA_API_SEASON} não encontrada.")
        return

    teams = Team.objects.filter(group__stage__season=season).distinct()

    if not teams.exists():
        logger.warning("Nenhum time encontrado para a season configurada.")
        return

    skipped_teams = 0
    officials_synced = 0
    players_synced = 0

    for team in teams:
        players_json, officials_json = client.get_players(
            team.fifa_id, settings.FIFA_API_COMPETITION, settings.FIFA_API_SEASON
        )

        if players_json is None and officials_json is None:
            logger.warning(
                f"Dados de jogadores ou oficiais não encontrados para team_id={team.fifa_id} - {team.name}."
            )
            skipped_teams += 1
            continue

        for official_data in officials_json or []:
            official_id = official_data.get("IdCoach")
            if not official_id:
                continue

            role = _to_int(official_data.get("Role"))
            role_description = "Técnico" if role == 0 else "Auxiliar"

            _, _ = team.officials.update_or_create(
                fifa_id=official_id,
                defaults={
                    "name": get_description(official_data.get("Name")),
                    "short_name": get_description(official_data.get("ShortName"))
                    or get_description(official_data.get("Alias"))
                    or "",
                    "role_code": role,
                    "role_description": role_description,
                },
            )
            officials_synced += 1

        for player_data in players_json or []:
            player_id = player_data.get("IdPlayer")
            if not player_id:
                continue

            position = (
                get_description(player_data.get("PositionLocalized"))
                or get_description(player_data.get("RealPositionLocalized"))
                or "Não informado"
            )

            _, _ = team.players.update_or_create(
                fifa_id=player_id,
                defaults={
                    "name": get_description(player_data.get("PlayerName")),
                    "short_name": get_description(player_data.get("ShortName")) or "",
                    "position": position,
                    "shirt_number": _to_int(player_data.get("JerseyNum")),
                },
            )
            players_synced += 1

    logger.info(
        f"Sync de elenco concluída. Times processados: {teams.count()} (ignorados: {skipped_teams}), "
        f"jogadores sincronizados: {players_synced}, oficiais sincronizados: {officials_synced}."
    )

import logging

from django.conf import settings

from src.football.api.client import FootballDataClient
from src.football.models import Group, Season, Stage, Team

logger = logging.getLogger(__name__)


def sync_groups():
    season = Season.objects.filter(fifa_id=settings.FIFA_API_SEASON).first()
    if not season:
        logger.error(
            f"Season com fifa_id={settings.FIFA_API_SEASON} não encontrada. Crie a season antes de rodar este comando."
        )
        return

    stage = Stage.objects.filter(order=1, season=season).first()
    if not stage:
        logger.error(
            f"Stage order=1 não encontrada para season={season.fifa_id}. Crie a stage antes de rodar este comando."
        )
        return

    logger.info(
        f"""Sincronizando groups para season={settings.FIFA_API_SEASON}, competition={settings.FIFA_API_COMPETITION},
        stage={stage.fifa_id}"""
    )

    client = FootballDataClient()
    standings_json = client.get_standings(settings.FIFA_API_COMPETITION, settings.FIFA_API_SEASON, stage.fifa_id)

    teams = set()
    updated = 0
    for standing in standings_json:
        team = standing.get("Team") or {}
        team_id = team.get("IdTeam")
        if not team_id:
            logger.warning(f"Team sem IdTeam encontrado em standing: {standing}")
            continue

        group_raw = standing.get("Group")
        group_name = group_raw[0].get("Description") if group_raw else None
        if group_name and group_name.startswith("Group "):
            group_name = group_name.replace("Group ", "")
        elif group_name and group_name.startswith("Grupo "):
            group_name = group_name.replace("Grupo ", "")

        if team_id in teams:
            logger.warning(f"Team com IdTeam={team_id} já processado, pulando. Dados do time: {team}")
            continue

        teams.add(team_id)

        group = Group.objects.filter(name=group_name).first() if group_name else None
        team_query = Team.objects.filter(fifa_id=team_id).first()
        if not team_query:
            logger.warning(f"Team não encontrado: IdTeam={team_id}")
            continue

        team_query.group = group
        team_query.save(update_fields=["group"])
        updated += 1
        logger.info(f"Team atualizado: {team_query.name} (IdTeam={team_id})")

    logger.info(f"Sync BD concluída: {updated} times atualizados.")

import logging

from django.conf import settings

from src.football.api.client import FootballDataClient
from src.football.models import Competition, Group, Season, Stage, Standing, Team

logger = logging.getLogger(__name__)


def _normalize_group_name(name: str | None) -> str | None:
    if not name:
        return None

    normalized = name.strip()
    if normalized.startswith("Group "):
        normalized = normalized.replace("Group ", "", 1)
    elif normalized.startswith("Grupo "):
        normalized = normalized.replace("Grupo ", "", 1)

    return normalized.strip() or None


def _to_int(value, default=None) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def sync_standings():
    client = FootballDataClient()

    competition = Competition.objects.filter(fifa_id=settings.FIFA_API_COMPETITION).first()
    if not competition:
        logger.error(f"Competition com fifa_id={settings.FIFA_API_COMPETITION} não encontrada.")
        return

    season = Season.objects.filter(fifa_id=settings.FIFA_API_SEASON).first()
    if not season:
        logger.error(f"Season com fifa_id={settings.FIFA_API_SEASON} não encontrada.")
        return

    stage = Stage.objects.filter(fifa_id=settings.FIFA_API_STAGE).first()
    if not stage:
        logger.error(f"Stage com fifa_id={settings.FIFA_API_STAGE} não encontrada.")
        return

    standings_json = client.get_standings(
        settings.FIFA_API_COMPETITION,
        settings.FIFA_API_SEASON,
        settings.FIFA_API_STAGE,
    )

    teams_map = {str(t.fifa_id): t for t in Team.objects.all()}
    groups_map = {_normalize_group_name(g.name): g for g in Group.objects.filter(stage=stage)}

    rows: list[Standing] = []
    skipped = 0

    for result in standings_json:
        team_data = result.get("Team") or {}
        group_data = result.get("Group") or []

        team_id = team_data.get("IdTeam")
        team = teams_map.get(str(team_id)) if team_id is not None else None

        group_name = group_data[0].get("Description") if group_data and isinstance(group_data[0], dict) else None
        group_name = _normalize_group_name(group_name)
        group = groups_map.get(group_name) if group_name else None

        if not group and team and team.group_id and team.group.stage_id == stage.id:
            group = team.group

        if not team or not group:
            skipped += 1
            continue

        rows.append(
            Standing(
                season=season,
                group=group,
                team=team,
                position=_to_int(result.get("Position")),
                played=_to_int(result.get("Played")),
                won=_to_int(result.get("Won")),
                drawn=_to_int(result.get("Drawn")),
                lost=_to_int(result.get("Lost")),
                goals_for=_to_int(result.get("For")),
                goals_against=_to_int(result.get("Against")),
                goal_difference=_to_int(result.get("GoalsDiference", result.get("GoalsDifference"))),
                points=_to_int(result.get("Points")),
            )
        )

    if not rows:
        logger.info("Nenhum standing elegível para sincronizar.")
        return

    Standing.objects.bulk_create(
        rows,
        update_conflicts=True,
        unique_fields=["season", "group", "team"],
        update_fields=[
            "position",
            "played",
            "won",
            "drawn",
            "lost",
            "goals_for",
            "goals_against",
            "goal_difference",
            "points",
        ],
    )

    logger.info(f"Standings sincronizados: {len(rows)} (ignorados: {skipped})")

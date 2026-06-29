import logging

from src.football.api.client import FootballDataClient
from src.football.models import Team

logger = logging.getLogger(__name__)


def _to_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def sync_rankings():
    """Sincroniza Team.world_ranking a partir do ranking mundial da FIFA.

    O ranking é o critério de desempate entre classificações iguais na fase de
    grupos. Vem de endpoint separado do cadastro de times (teamsModule não traz
    worldRanking); casa por IdTeam == Team.fifa_id.
    """
    client = FootballDataClient()
    rankings = client.get_world_rankings()

    rank_by_id = {}
    for row in rankings:
        team_id = row.get("IdTeam")
        rank = _to_int(row.get("Rank"))
        if team_id is None or rank is None:
            continue
        rank_by_id[str(team_id)] = rank

    to_update = []
    missing = 0
    for team in Team.objects.all():
        rank = rank_by_id.get(str(team.fifa_id))
        if rank is None:
            missing += 1
            continue
        if team.world_ranking != rank:
            team.world_ranking = rank
            to_update.append(team)

    if to_update:
        Team.objects.bulk_update(to_update, ["world_ranking"])

    logger.info("Ranking mundial sincronizado: %s atualizados (sem rank: %s)", len(to_update), missing)
    return len(to_update)

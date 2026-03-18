import logging
import os
import unicodedata

from curl_cffi import requests
from django.conf import settings

from src.football.api.client import FootballDataClient
from src.football.models import Group, Team

logger = logging.getLogger(__name__)


def limpar_nome(nome):
    return unicodedata.normalize("NFKD", nome).encode("ASCII", "ignore").decode("ASCII").replace(" ", "_")


def download_flag(url, filename):
    """Baixa a bandeira de um time e salva localmente."""
    if not url or not filename:
        return

    filepath = f"{settings.STATICFILES_DIRS[0]}/{filename}"
    os.makedirs(os.path.dirname(filepath), exist_ok=True)

    img = requests.get(url)
    img.raise_for_status()

    with open(filepath, "wb") as f:
        f.write(img.content)


def sync_teams():
    client = FootballDataClient()
    teams_json = client.get_teams()

    # Pré-carrega todos os grupos em memória (evita N+1)
    groups = {g.name: g for g in Group.objects.all()}

    rows = []
    for t in teams_json:
        raw_flag = t.get("teamFlag") or ""
        flag_url = raw_flag.replace("{format}", "sq").replace("{size}", "5") if raw_flag else ""

        stage = t.get("stage") or ""
        group_name = stage.replace("Group ", "")

        team_name = t.get("teamName", "")
        code = flag_url.split("/")[-1] if flag_url else ""
        flag_local = f"img/flags/{code}.png" if code else ""

        rows.append(
            Team(
                fifa_id=t.get("teamId"),
                name=team_name,
                name_norm=limpar_nome(team_name),
                code=code,
                confederation=t.get("confederationId", ""),
                flag_url=flag_url,
                flag_local=flag_local,
                page_url="https://www.fifa.com" + str(t.get("teamPageUrl", "")),
                group=groups.get(group_name),
                is_host=t.get("hostTeam", False),
                appearances=t.get("appearances", 0),
                world_ranking=t.get("worldRanking"),
            )
        )

    # 1. Salva todos os times no banco primeiro
    Team.objects.bulk_create(
        rows,
        update_conflicts=True,
        unique_fields=["fifa_id"],
        update_fields=[
            "name",
            "name_norm",
            "code",
            "confederation",
            "flag_url",
            "flag_local",
            "page_url",
            "group_id",
            "is_host",
            "appearances",
            "world_ranking",
        ],
    )
    logger.info(f"Sync BD concluída: {len(rows)} times salvos.")

    # 2. Depois baixa as bandeiras (se uma falhar, os times já estão salvos)
    for team in rows:
        try:
            if team.flag_url and team.flag_local:
                download_flag(team.flag_url, team.flag_local)
                logger.info(f"Bandeira baixada: {team.name}")
        except Exception as e:
            logger.warning(f"Falha ao baixar bandeira de {team.name}: {e}")

    logger.info("Sync completa (times + bandeiras).")

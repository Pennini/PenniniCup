import logging
import unicodedata

from curl_cffi import requests
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

from src.football.api.client import FootballDataClient
from src.football.models import Group, Team

logger = logging.getLogger(__name__)


def limpar_nome(nome):
    return unicodedata.normalize("NFKD", nome).encode("ASCII", "ignore").decode("ASCII").replace(" ", "_")


def download_flag(url, code):
    """Baixa a bandeira de um time e salva no storage de media."""
    if not url or not code:
        return ""

    img = requests.get(url)
    img.raise_for_status()

    filename = f"flags/{code}.png"
    if default_storage.exists(filename):
        default_storage.delete(filename)

    return default_storage.save(filename, ContentFile(img.content))


def sync_teams():
    client = FootballDataClient()
    teams_json = client.get_teams()

    existing_flag_images = {
        str(team.fifa_id): bool(team.flag_image) for team in Team.objects.only("fifa_id", "flag_image")
    }

    # Pré-carrega todos os grupos em memória (evita N+1)
    groups = {g.name: g for g in Group.objects.all()}

    rows = []
    flag_jobs = []
    for t in teams_json:
        raw_flag = t.get("teamFlag") or ""
        flag_url = raw_flag.replace("{format}", "sq").replace("{size}", "5") if raw_flag else ""

        stage = t.get("stage") or ""
        group_name = stage.replace("Group ", "")

        team_name = t.get("teamName", "")
        code = flag_url.split("/")[-1] if flag_url else ""
        flag_local = f"img/flags/{code}.png" if code else ""
        fifa_id = str(t.get("teamId"))

        rows.append(
            Team(
                fifa_id=fifa_id,
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

        flag_jobs.append(
            {
                "fifa_id": fifa_id,
                "team_name": team_name,
                "flag_url": flag_url,
                "code": code,
            }
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
            "flag_image",
            "page_url",
            "group_id",
            "is_host",
            "appearances",
            "world_ranking",
        ],
    )
    logger.info("Sync BD concluída: %s times salvos.", len(rows))

    # 2. Depois baixa as bandeiras (se uma falhar, os times já estão salvos)
    for job in flag_jobs:
        try:
            already_downloaded = existing_flag_images.get(job["fifa_id"], False)
            if job["flag_url"] and job["code"] and not already_downloaded:
                storage_name = download_flag(job["flag_url"], job["code"])
                if storage_name:
                    Team.objects.filter(fifa_id=job["fifa_id"]).update(flag_image=storage_name)
                    logger.info("Bandeira baixada: %s", job["team_name"])
        except Exception as e:
            logger.warning("Falha ao baixar bandeira de %s: %s", job["team_name"], e)

    logger.info("Sync completa (times + bandeiras).")

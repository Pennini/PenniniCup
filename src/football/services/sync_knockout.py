import logging

from django.conf import settings

from src.football.api.client import FootballDataClient
from src.football.models import Group, Season, Stadium, Stage

logger = logging.getLogger(__name__)


def _normalize_stage_name(name: str | None) -> str:
    return (name or "").strip().lower()


def _get_stage_order(stage_id, stage_name: str | None = None) -> int | None:
    stage_map_by_id = {
        "289273": 1,
        "289287": 2,
        "289288": 3,
        "289289": 4,
        "289290": 5,
        "289291": 6,
        "289292": 7,
    }

    order = stage_map_by_id.get(str(stage_id))
    if order is not None:
        return order

    stage_map_by_name = {
        "primeira fase": 1,
        "segundas de final": 2,
        "segunda de final": 2,
        "oitavas de final": 3,
        "quartas de final": 4,
        "semifinal": 5,
        "semi-final": 5,
        "decisão do 3° lugar": 6,
        "decisao do 3° lugar": 6,
        "decisão do 3º lugar": 6,
        "decisao do 3º lugar": 6,
        "disputa do 3° lugar": 6,
        "disputa do 3º lugar": 6,
        "final": 7,
    }
    return stage_map_by_name.get(_normalize_stage_name(stage_name))


def sync_knockout():
    """
    Extrai Stages e Groups a partir dos dados de matches da API.
    Deve rodar UMA VEZ antes do sync_matches e sync_teams.
    """
    season = Season.objects.filter(fifa_id=settings.FIFA_API_SEASON).first()
    if not season:
        logger.error(f"""
                     Season com fifa_id={settings.FIFA_API_SEASON} não encontrada.
                    Crie a season antes de rodar este comando.""")
        return

    client = FootballDataClient()
    matches_json = client.get_matches(settings.FIFA_API_SEASON)

    # 1. Coleta dados únicos de stages e groups a partir dos matches
    stages_set = set()
    groups_set = set()
    stadium_set = set()

    for match in matches_json:
        id_fase = match.get("IdStage")
        id_grupo = match.get("IdGroup")

        stage_names = match.get("StageName") or []
        fase = stage_names[0].get("Description") if stage_names else None

        estadio = match.get("Stadium") or {}

        if estadio:
            stadium_name = estadio.get("Name")
            stadium_city = estadio.get("CityName")

            stadium_name = stadium_name[0].get("Description") if stadium_name else None
            stadium_city = stadium_city[0].get("Description") if stadium_city else None

            stadium_set.add(
                (
                    estadio.get("IdStadium"),
                    stadium_name,
                    stadium_city,
                    estadio.get("IdCountry"),
                )
            )

        group_names = match.get("GroupName") or []
        grupo_raw = group_names[0].get("Description") if group_names else None

        grupo = grupo_raw.replace("Grupo ", "") if grupo_raw else None

        if id_fase and fase:
            stages_set.add((id_fase, fase))

        if id_grupo and grupo and id_fase:
            groups_set.add((id_grupo, grupo, id_fase))

    # 2. Salva os Stages
    stage_rows = [
        Stage(
            fifa_id=id_fase,
            name=fase,
            season=season,
            order=_get_stage_order(id_fase, fase),
        )
        for id_fase, fase in stages_set
    ]

    Stage.objects.bulk_create(
        stage_rows,
        update_conflicts=True,
        unique_fields=["fifa_id"],
        update_fields=["name", "season_id", "order"],
    )
    logger.info(f"Stages sincronizados: {len(stage_rows)}")

    Stadium.objects.bulk_create(
        [
            Stadium(
                fifa_id=id_estadio,
                name=nome,
                city=cidade,
                country_code=codigo_pais,
            )
            for id_estadio, nome, cidade, codigo_pais in stadium_set
        ],
        update_conflicts=True,
        unique_fields=["fifa_id"],
        update_fields=["name", "city", "country_code"],
    )
    logger.info(f"Stadiums sincronizados: {len(stadium_set)}")

    # 3. Pré-carrega stages para mapear fifa_id → objeto
    stages_map = {s.fifa_id: s for s in Stage.objects.all()}

    # 4. Salva os Groups
    group_rows = [
        Group(
            fifa_id=id_grupo,
            name=grupo,
            stage=stages_map.get(id_fase),
        )
        for id_grupo, grupo, id_fase in groups_set
    ]

    Group.objects.bulk_create(
        group_rows,
        update_conflicts=True,
        unique_fields=["fifa_id"],
        update_fields=["name", "stage_id"],
    )
    logger.info(f"Groups sincronizados: {len(group_rows)}")

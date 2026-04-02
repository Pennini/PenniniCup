import logging

from django.conf import settings
from django.utils.dateparse import parse_datetime

from src.football.api.client import FootballDataClient
from src.football.models import Group, Match, Season, Stadium, Stage, Team
from src.pool.services.ranking import recalculate_all_pools

logger = logging.getLogger(__name__)


def _parse_datetime(value: str | None):
    if not value:
        return None
    return parse_datetime(value)


def _map_status(value) -> int:
    if value is None:
        return Match.STATUS_SCHEDULED
    if isinstance(value, int):
        return Match.STATUS_FINISHED if value == 0 else Match.STATUS_SCHEDULED
    return Match.STATUS_SCHEDULED


def sync_matches():
    """
    Sincroniza partidas a partir da API da FIFA e grava no banco.
    Requer stages, grupos, times e estádios já sincronizados.
    """
    client = FootballDataClient()
    season = Season.objects.filter(fifa_id=settings.FIFA_API_SEASON).first()
    if not season:
        logger.error(
            f"Season com fifa_id={settings.FIFA_API_SEASON} não encontrada. Crie a season antes de rodar este comando."
        )
        return

    matches_json = client.get_matches(settings.FIFA_API_SEASON)

    stages_map = {s.fifa_id: s for s in Stage.objects.all()}
    groups_map = {g.fifa_id: g for g in Group.objects.all()}
    teams_map = {t.fifa_id: t for t in Team.objects.all()}
    stadiums_map = {s.fifa_id: s for s in Stadium.objects.all()}

    rows = []
    skipped = 0

    for match in matches_json:
        match_id = match.get("IdMatch")
        match_number = match.get("MatchNumber")
        stage = stages_map.get(match.get("IdStage"))

        if not match_id or not match_number or not stage:
            skipped += 1
            continue

        group = groups_map.get(match.get("IdGroup")) if match.get("IdGroup") else None

        home = match.get("Home") or {}
        away = match.get("Away") or {}

        home_team = teams_map.get(home.get("IdTeam")) if home else None
        away_team = teams_map.get(away.get("IdTeam")) if away else None

        winner_id = match.get("Winner")
        winner = teams_map.get(winner_id) if winner_id else None

        stadium_data = match.get("Stadium") or {}
        stadium = stadiums_map.get(stadium_data.get("IdStadium")) if stadium_data else None

        match_date_utc = _parse_datetime(match.get("Date"))
        match_date_local = _parse_datetime(match.get("LocalDate")) or match_date_utc
        match_date_brasilia = match_date_local or match_date_utc

        if not match_date_utc or not match_date_local or not match_date_brasilia:
            skipped += 1
            continue

        rows.append(
            Match(
                fifa_id=match_id,
                season=season,
                stage=stage,
                group=group,
                match_number=match_number,
                match_date_utc=match_date_utc,
                match_date_local=match_date_local,
                match_date_brasilia=match_date_brasilia,
                stadium=stadium,
                home_team=home_team,
                away_team=away_team,
                home_placeholder=match.get("PlaceHolderA") or "",
                away_placeholder=match.get("PlaceHolderB") or "",
                home_score=match.get("HomeTeamScore"),
                away_score=match.get("AwayTeamScore"),
                home_penalty_score=match.get("HomeTeamPenaltyScore"),
                away_penalty_score=match.get("AwayTeamPenaltyScore"),
                winner=winner,
                status=_map_status(match.get("MatchStatus")),
            )
        )

    if not rows:
        logger.info("Nenhuma partida elegível para sincronizar.")
        return

    Match.objects.bulk_create(
        rows,
        update_conflicts=True,
        unique_fields=["fifa_id"],
        update_fields=[
            "season_id",
            "stage_id",
            "group_id",
            "match_number",
            "match_date_utc",
            "match_date_local",
            "match_date_brasilia",
            "stadium_id",
            "home_team_id",
            "away_team_id",
            "home_placeholder",
            "away_placeholder",
            "home_score",
            "away_score",
            "home_penalty_score",
            "away_penalty_score",
            "winner_id",
            "status",
        ],
    )
    recalculate_all_pools(season=season)
    logger.info(f"Matches sincronizados: {len(rows)} (ignorados: {skipped})")

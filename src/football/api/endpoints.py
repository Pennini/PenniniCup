"""
Constantes de endpoints da API da FIFA.
Cada endpoint pode ter uma BASE_URL diferente.
Os métodos do client recebem a URL completa daqui.
"""

# ── Base URLs ──────────────────────────────────────────────────
# A API da FIFA usa subdomínios/paths diferentes para cada recurso
FIFA_API_BASE = "https://api.fifa.com/api/v3"
FIFA_API_SECTIONS = "https://cxm-api.fifa.com/fifaplusweb/api/sections"


# ── Endpoints (retornam URL completa) ─────────────────────────


def competition_url() -> tuple[str, dict]:
    url = f"{FIFA_API_SECTIONS}/fdcpTournamentRelatedSection/26sROiQOZIXlflFlS27FRv"

    params = {
        "locale": "pt",
    }

    return url, params


def teams_url() -> tuple[str, dict]:
    """URL para buscar times de uma competição."""
    url = f"{FIFA_API_SECTIONS}/teamsModule/4v5Yng3VdGD9c1cpnOIff1"

    params = {"locale": "pt", "limit": 200}

    return url, params


def matches_url(season_id: int) -> tuple[str, dict]:
    """URL para buscar partidas de uma competição."""
    params = {
        "language": "pt",
        "count": 500,
        "idSeason": season_id,
    }

    return f"{FIFA_API_BASE}/calendar/matches", params


def standings_url(competition_id: int, season_id: int, stage_id: int) -> tuple[str, dict]:
    """URL para buscar classificação de uma competição."""
    params = {"count": 200, "language": "pt"}

    return f"{FIFA_API_BASE}/calendar/{competition_id}/{season_id}/{stage_id}/standing", params


def players_url(team_id: int, competition_id: int, season_id: int) -> tuple[str, dict]:
    """URL para buscar detalhes de uma partida específica."""
    params = {"idCompetition": competition_id, "idSeason": season_id, "language": "pt"}

    return f"{FIFA_API_BASE}/teams/{team_id}/squad", params

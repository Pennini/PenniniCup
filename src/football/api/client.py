import logging
import time

from curl_cffi import requests
from fake_useragent import UserAgent

from src.football.api import endpoints

logger = logging.getLogger(__name__)


class FootballDataClient:
    """
    Client para a API pública da FIFA (api.fifa.com).
    Não requer API key — usa headers de navegador real para evitar bloqueios.
    Inclui retry automático com backoff exponencial.
    """

    def __init__(self, max_retries: int = 3, timeout: int = 15):
        self.max_retries = max_retries
        self.timeout = timeout
        self.session = requests.Session()

        # Gera UM User-Agent aleatório de Chrome por sessão
        # (não rotaciona a cada request — navegadores reais mantêm o mesmo UA)
        ua = UserAgent(browsers=["Chrome"], os=["Windows"])
        user_agent = ua.random

        self.session.headers.update(
            {
                "User-Agent": user_agent,
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
                "Accept-Encoding": "gzip, deflate, br",
                "Origin": "https://www.fifa.com",
                "Referer": "https://www.fifa.com/",
                "Sec-Ch-Ua-Mobile": "?0",
                "Sec-Ch-Ua-Platform": '"Windows"',
                "Sec-Fetch-Dest": "empty",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Site": "same-site",
                "Connection": "keep-alive",
            }
        )
        logger.debug(f"[FIFA API] Session criada com UA: {user_agent}")

    def _request(self, url: str, params: dict | None = None) -> dict:
        """
        Faz um GET com retry e backoff exponencial.
        Espera automaticamente em caso de rate limit (HTTP 429).
        """

        for attempt in range(1, self.max_retries + 1):
            try:
                logger.info(f"[FIFA API] GET {url} (tentativa {attempt}/{self.max_retries})")
                response = self.session.get(
                    url,
                    params=params,
                    timeout=self.timeout,
                    impersonate="chrome131",  # curl_cffi: TLS fingerprint de Chrome real
                )

                if response.status_code == 429:
                    wait = 2**attempt
                    logger.warning(f"[FIFA API] Rate limit (429). Aguardando {wait}s...")
                    time.sleep(wait)
                    continue

                response.raise_for_status()
                return response.json()

            except requests.errors.RequestsError as e:
                if attempt == self.max_retries:
                    logger.error(f"[FIFA API] Falha após {self.max_retries} tentativas: {e}")
                    raise

                wait = 2**attempt
                logger.warning(f"[FIFA API] Erro na tentativa {attempt}: {e}. Retry em {wait}s...")
                time.sleep(wait)

        return {}

    # ── Endpoints públicos ─────────────────────────────────────
    def get_teams(self) -> list[dict]:
        """Busca todos os times de uma competição."""
        url, params = endpoints.teams_url()

        data = self._request(url, params)
        return data.get("teams", [])

    def get_matches(self, competition_id: int, **filters) -> list[dict]:
        """Busca partidas de uma competição com filtros opcionais."""
        url, params = endpoints.matches_url(competition_id)

        data = self._request(url, params)
        return data.get("Results", [])

    def get_standings(self, competition_id: int, season_id: int, stage_id: int) -> list[dict]:
        """Busca classificação dos grupos de uma competição."""
        url, params = endpoints.standings_url(competition_id, season_id, stage_id)

        data = self._request(url, params)
        return data.get("Results", [])

    def get_players(self, team_id: int, competition_id: int, season_id: int) -> tuple[list[dict], list[dict]]:
        url, params = endpoints.players_url(team_id, competition_id, season_id)

        data = self._request(url, params)
        return data.get("Players", []), data.get("Officials", [])

from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from src.football.api import client as fifa_client_module
from src.football.api.client import FootballDataClient
from src.football.models import Competition, Group, Match, Season, Stage, Team
from src.football.services.sync_matches import sync_matches
from src.football.services.sync_teams import sync_teams

User = get_user_model()


@override_settings(FIFA_API_SEASON=1999)
class MatchSyncTimezoneTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="sync-owner", email="sync@example.com", password="123456Aa!")
        competition = Competition.objects.create(fifa_id=1999, name="Copa TZ")
        self.season = Season.objects.create(
            fifa_id=1999,
            competition=competition,
            name="Temporada TZ",
            year=2026,
            start_date="2026-06-01",
            end_date="2026-07-30",
        )
        self.stage = Stage.objects.create(fifa_id="STAGE-TZ", season=self.season, name="Group Stage", order=1)

    @patch("src.football.services.sync_matches.enqueue_projection_recalc_for_season")
    @patch("src.football.services.sync_matches.FootballDataClient")
    def test_sync_uses_utc_as_source_for_brasilia_when_local_is_naive(self, client_cls, enqueue_mock):
        client_instance = client_cls.return_value
        client_instance.get_matches.return_value = [
            {
                "IdMatch": "TZ-1",
                "MatchNumber": 1,
                "IdStage": "STAGE-TZ",
                "Date": "2026-06-14T16:00:00Z",
                "LocalDate": "2026-06-14T18:00:00",
                "MatchStatus": 1,
            }
        ]

        sync_matches()

        enqueue_mock.assert_called_once_with(season=self.season)

        match = Match.objects.get(fifa_id="TZ-1")
        brasilia = match.match_date_brasilia.astimezone(ZoneInfo("America/Sao_Paulo"))
        utc = match.match_date_utc.astimezone(ZoneInfo("UTC"))

        self.assertEqual(utc.hour, 16)
        self.assertEqual(brasilia.hour, 13)

    @patch("src.football.services.sync_matches.enqueue_projection_recalc_for_season")
    @patch("src.football.services.sync_matches.FootballDataClient")
    def test_sync_builds_utc_and_brasilia_from_local_offset_when_utc_missing(self, client_cls, enqueue_mock):
        client_instance = client_cls.return_value
        client_instance.get_matches.return_value = [
            {
                "IdMatch": "TZ-2",
                "MatchNumber": 2,
                "IdStage": "STAGE-TZ",
                "Date": None,
                "LocalDate": "2026-06-14T18:00:00+02:00",
                "MatchStatus": 1,
            }
        ]

        sync_matches()

        enqueue_mock.assert_called_once_with(season=self.season)

        match = Match.objects.get(fifa_id="TZ-2")
        brasilia = match.match_date_brasilia.astimezone(ZoneInfo("America/Sao_Paulo"))
        utc = match.match_date_utc.astimezone(ZoneInfo("UTC"))

        self.assertEqual(utc.hour, 16)
        self.assertEqual(brasilia.hour, 13)


class TeamSyncFlagStorageTest(TestCase):
    @patch("src.football.services.sync_teams.default_storage")
    @patch("src.football.services.sync_teams.requests.get")
    @patch("src.football.services.sync_teams.FootballDataClient")
    def test_sync_teams_saves_flag_in_media_storage(self, client_cls, requests_get_mock, storage_mock):
        competition = Competition.objects.create(fifa_id=3000, name="Copa Flags")
        season = Season.objects.create(
            fifa_id=3000,
            competition=competition,
            name="Temporada Flags",
            year=2026,
            start_date="2026-06-01",
            end_date="2026-07-30",
        )
        stage = Stage.objects.create(fifa_id="STAGE-FLAG", season=season, name="Group Stage", order=1)
        Group.objects.create(fifa_id="GROUP-A", stage=stage, name="A")

        client_instance = client_cls.return_value
        client_instance.get_teams.return_value = [
            {
                "teamId": "TEAM-BR",
                "teamName": "Brasil",
                "teamFlag": "https://cdn.example/{size}/{format}/BRA",
                "stage": "Group A",
                "confederationId": "CONMEBOL",
                "teamPageUrl": "/teams/brasil",
                "hostTeam": False,
                "appearances": 22,
                "worldRanking": 1,
            }
        ]

        response_mock = Mock()
        response_mock.content = b"fake-image-bytes"
        response_mock.raise_for_status.return_value = None
        requests_get_mock.return_value = response_mock

        storage_mock.exists.return_value = False
        storage_mock.save.return_value = "flags/BRA.png"

        sync_teams()

        team = Team.objects.get(fifa_id="TEAM-BR")
        self.assertEqual(team.flag_image.name, "flags/BRA.png")
        self.assertEqual(team.flag_local, "img/flags/BRA.png")
        storage_mock.save.assert_called_once()
        requests_get_mock.assert_called_once_with("https://cdn.example/5/sq/BRA")


class FootballDataClientFallbackTest(TestCase):
    @patch("src.football.api.client.UserAgent")
    @patch("src.football.api.client.requests.Session")
    def test_uses_default_user_agent_when_fake_useragent_fails(self, session_cls, ua_cls):
        session = Mock()
        session.headers = {}
        session_cls.return_value = session
        ua_cls.side_effect = RuntimeError("ua backend offline")

        client = FootballDataClient(max_retries=1)

        self.assertIn("Mozilla/5.0", client.session.headers["User-Agent"])

    @patch("src.football.api.client.UserAgent")
    @patch("src.football.api.client.requests.get")
    @patch("src.football.api.client.requests.Session")
    def test_fallbacks_to_plain_get_when_impersonate_fails(self, session_cls, requests_get_mock, ua_cls):
        session = Mock()
        session.headers = {}
        session_cls.return_value = session
        ua_instance = Mock()
        ua_instance.random = "UA Test"
        ua_cls.return_value = ua_instance

        session.get.side_effect = [fifa_client_module.requests.errors.RequestsError("tls fingerprint blocked")]

        response = Mock()
        response.status_code = 200
        response.raise_for_status.return_value = None
        response.json.return_value = {"teams": [{"id": 1}]}
        requests_get_mock.return_value = response

        client = FootballDataClient(max_retries=2)
        data = client._request("https://api.fifa.com/example")

        self.assertFalse(client.use_impersonate)
        self.assertEqual(data, {"teams": [{"id": 1}]})
        session.get.assert_called_once()
        requests_get_mock.assert_called_once()


class MatchSignalsRecalculationTest(TestCase):
    def setUp(self):
        competition = Competition.objects.create(fifa_id=8100, name="Copa Signals")
        self.season = Season.objects.create(
            fifa_id=8100,
            competition=competition,
            name="Temporada Signals",
            year=2026,
            start_date="2026-06-01",
            end_date="2026-07-30",
        )
        self.stage = Stage.objects.create(fifa_id="STAGE-SIGNAL", season=self.season, name="Group Stage", order=1)
        self.group = Group.objects.create(fifa_id="GROUP-SIGNAL", stage=self.stage, name="A")
        self.home = Team.objects.create(
            fifa_id="HOME-SIGNAL",
            name="Home Signal",
            name_norm="home signal",
            code="HSG",
            group=self.group,
        )
        self.away = Team.objects.create(
            fifa_id="AWAY-SIGNAL",
            name="Away Signal",
            name_norm="away signal",
            code="ASG",
            group=self.group,
        )
        kickoff = timezone.now() + timezone.timedelta(days=5)
        self.match = Match.objects.create(
            fifa_id="MATCH-SIGNAL",
            season=self.season,
            stage=self.stage,
            group=self.group,
            match_number=1,
            match_date_utc=kickoff,
            match_date_local=kickoff,
            match_date_brasilia=kickoff,
            home_team=self.home,
            away_team=self.away,
        )

    @patch("src.football.signals.enqueue_projection_recalc_for_season")
    @patch("src.football.signals.recalculate_match_scores")
    def test_match_score_change_recalculates_points(self, recalculate_match_scores_mock, enqueue_mock):
        self.match.home_score = 2
        self.match.away_score = 1
        self.match.winner = self.home
        self.match.status = Match.STATUS_FINISHED
        self.match.save()

        recalculate_match_scores_mock.assert_called_once()
        called_match = recalculate_match_scores_mock.call_args.kwargs.get("match")
        self.assertEqual(called_match.id, self.match.id)
        enqueue_mock.assert_called_once_with(season=self.season)

    @patch("src.football.signals.enqueue_projection_recalc_for_season")
    @patch("src.football.signals.recalculate_match_scores")
    def test_match_structure_change_requeues_projection(self, recalculate_match_scores_mock, enqueue_mock):
        new_team = Team.objects.create(
            fifa_id="NEW-SIGNAL",
            name="New Signal",
            name_norm="new signal",
            code="NSG",
            group=self.group,
        )
        self.match.home_team = new_team
        self.match.save(update_fields=["home_team"])

        recalculate_match_scores_mock.assert_not_called()
        enqueue_mock.assert_called_once_with(season=self.season)


@override_settings(FIFA_API_SEASON=9100)
class MatchSyncRankingRecalculationTest(TestCase):
    def setUp(self):
        competition = Competition.objects.create(fifa_id=9100, name="Copa Ranking")
        self.season = Season.objects.create(
            fifa_id=9100,
            competition=competition,
            name="Temporada Ranking",
            year=2026,
            start_date="2026-06-01",
            end_date="2026-07-30",
        )
        Stage.objects.create(fifa_id="STAGE-RANKING", season=self.season, name="Group Stage", order=1)

    @patch("src.football.services.sync_matches.recalculate_all_pools")
    @patch("src.football.services.sync_matches.enqueue_projection_recalc_for_season")
    @patch("src.football.services.sync_matches.FootballDataClient")
    def test_sync_recalculates_pool_ranking_after_bulk_match_upsert(
        self,
        client_cls,
        enqueue_mock,
        recalculate_all_pools_mock,
    ):
        client_instance = client_cls.return_value
        client_instance.get_matches.return_value = [
            {
                "IdMatch": "SYNC-RANKING-1",
                "MatchNumber": 1,
                "IdStage": "STAGE-RANKING",
                "Date": "2026-06-14T16:00:00Z",
                "LocalDate": "2026-06-14T18:00:00",
                "MatchStatus": 1,
            }
        ]

        sync_matches()

        enqueue_mock.assert_called_once_with(season=self.season)
        recalculate_all_pools_mock.assert_called_once_with(season=self.season)

from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from src.football.models import Competition, Match, Season, Stage
from src.football.services.sync_matches import sync_matches

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

    @patch("src.football.services.sync_matches.recalculate_all_pools")
    @patch("src.football.services.sync_matches.FootballDataClient")
    def test_sync_uses_utc_as_source_for_brasilia_when_local_is_naive(self, client_cls, _recalc_mock):
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

        match = Match.objects.get(fifa_id="TZ-1")
        brasilia = match.match_date_brasilia.astimezone(ZoneInfo("America/Sao_Paulo"))
        utc = match.match_date_utc.astimezone(ZoneInfo("UTC"))

        self.assertEqual(utc.hour, 16)
        self.assertEqual(brasilia.hour, 13)

    @patch("src.football.services.sync_matches.recalculate_all_pools")
    @patch("src.football.services.sync_matches.FootballDataClient")
    def test_sync_builds_utc_and_brasilia_from_local_offset_when_utc_missing(self, client_cls, _recalc_mock):
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

        match = Match.objects.get(fifa_id="TZ-2")
        brasilia = match.match_date_brasilia.astimezone(ZoneInfo("America/Sao_Paulo"))
        utc = match.match_date_utc.astimezone(ZoneInfo("UTC"))

        self.assertEqual(utc.hour, 16)
        self.assertEqual(brasilia.hour, 13)

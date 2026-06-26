from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from src.football.models import Competition, Match, Season, Stage
from src.football.services.sync_scheduler import should_run_sync


class FinishWindowTest(TestCase):
    def setUp(self):
        self.competition = Competition.objects.create(fifa_id=1999, name="Copa")
        self.season = Season.objects.create(
            fifa_id=1999,
            competition=self.competition,
            name="T",
            year=2026,
            start_date="2026-06-01",
            end_date="2026-07-30",
        )
        self.stage = Stage.objects.create(fifa_id="GS", season=self.season, name="Grupos", order=1)
        self.now = timezone.now()

    def _match(self, fifa_id, kickoff, status=Match.STATUS_SCHEDULED):
        return Match.objects.create(
            fifa_id=fifa_id,
            season=self.season,
            stage=self.stage,
            match_number=1,
            match_date_utc=kickoff,
            match_date_local=kickoff,
            match_date_brasilia=kickoff,
            status=status,
        )

    def test_match_in_window_is_detected(self):
        self._match("A", self.now - timedelta(hours=1))  # começou há 1h, dentro de 3h
        self.assertTrue(should_run_sync(self.season, self.now, window_hours=3))

    def test_finished_match_in_window_is_ignored(self):
        self._match("B", self.now - timedelta(hours=1), status=Match.STATUS_FINISHED)
        self.assertFalse(should_run_sync(self.season, self.now, window_hours=3))

    def test_future_kickoff_not_in_window(self):
        self._match("C", self.now + timedelta(minutes=30))
        self.assertFalse(should_run_sync(self.season, self.now, window_hours=3))

    def test_old_kickoff_past_window(self):
        self._match("D", self.now - timedelta(hours=4))  # passou da janela de 3h
        self.assertFalse(should_run_sync(self.season, self.now, window_hours=3))

    def test_extra_time_still_in_window(self):
        self._match("E", self.now - timedelta(hours=2, minutes=40))  # jogo longo, ainda < 3h
        self.assertTrue(should_run_sync(self.season, self.now, window_hours=3))


@override_settings(FIFA_API_SEASON=1999)
class SchedulerCommandTest(TestCase):
    def setUp(self):
        self.competition = Competition.objects.create(fifa_id=1999, name="Copa")
        self.season = Season.objects.create(
            fifa_id=1999,
            competition=self.competition,
            name="T",
            year=2026,
            start_date="2026-06-01",
            end_date="2026-07-30",
        )

    @patch("src.football.management.commands.run_match_sync_scheduler.sync_matches")
    @patch("src.football.management.commands.run_match_sync_scheduler.should_run_sync", return_value=True)
    def test_once_runs_sync_when_in_window(self, should_mock, sync_mock):
        from django.core.management import call_command

        call_command("run_match_sync_scheduler", "--once")
        sync_mock.assert_called_once()

    @patch("src.football.management.commands.run_match_sync_scheduler.sync_matches")
    @patch("src.football.management.commands.run_match_sync_scheduler.should_run_sync", return_value=False)
    def test_once_skips_sync_when_no_window(self, should_mock, sync_mock):
        from django.core.management import call_command

        call_command("run_match_sync_scheduler", "--once")
        sync_mock.assert_not_called()

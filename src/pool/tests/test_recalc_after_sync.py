from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from src.football.models import Competition, Group, Match, Season, Stage
from src.pool.models import Pool
from src.pool.services.ranking import recalculate_after_sync

User = get_user_model()


class RecalcAfterSyncRoutingTest(TestCase):
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
        self.group_stage = Stage.objects.create(fifa_id="GS_TEST", season=self.season, name="Grupos", order=101)
        self.final_stage = Stage.objects.create(fifa_id="FIN_TEST", season=self.season, name="Final", order=107)
        self.group = Group.objects.create(fifa_id="GA_TEST", stage=self.group_stage, name="A")

    def _match(self, fifa_id, stage, group=None):
        return Match.objects.create(
            fifa_id=fifa_id,
            season=self.season,
            stage=stage,
            group=group,
            match_number=int(fifa_id.split("-")[-1]),
            match_date_utc="2026-06-14T16:00:00Z",
            match_date_local="2026-06-14T16:00:00Z",
            match_date_brasilia="2026-06-14T13:00:00-03:00",
        )

    def _pool(self, name, username, email):
        owner = User.objects.create_user(username=username, email=email, password="123456Aa!")
        return Pool.objects.create(name=name, slug=name.lower(), season=self.season, created_by=owner)

    @patch("src.rankings.services.derived.refresh_pool_derived_data")
    @patch("src.pool.services.ranking.recalculate_pool_scores")
    @patch("src.pool.services.ranking.recalculate_match_scores")
    def test_no_changes_does_nothing(self, m_match, m_pool, m_refresh):
        recalculate_after_sync(self.season, [], podium_changed=False, group_stage_just_closed=False)
        m_match.assert_not_called()
        m_pool.assert_not_called()
        m_refresh.assert_not_called()

    @patch("src.rankings.services.derived.refresh_pool_derived_data")
    @patch("src.pool.services.ranking.recalculate_pool_scores")
    @patch("src.pool.services.ranking.recalculate_match_scores")
    def test_podium_change_recalcs_whole_pool(self, m_match, m_pool, m_refresh):
        pool = self._pool("P1", "owner1", "owner1@e.com")
        final = self._match("F-1", self.final_stage)
        recalculate_after_sync(self.season, [final], podium_changed=True, group_stage_just_closed=False)
        m_pool.assert_called_once_with(pool)
        m_refresh.assert_called_once_with(pool)
        m_match.assert_not_called()

    @patch("src.rankings.services.derived.refresh_pool_derived_data")
    @patch("src.pool.services.ranking.recalculate_pool_scores")
    @patch("src.pool.services.ranking.recalculate_match_scores")
    def test_group_stage_close_recalcs_whole_pool(self, m_match, m_pool, m_refresh):
        self._pool("P2", "owner2", "owner2@e.com")
        g = self._match("G-1", self.group_stage, self.group)
        recalculate_after_sync(self.season, [g], podium_changed=False, group_stage_just_closed=True)
        m_pool.assert_called_once()
        m_match.assert_not_called()

    @patch("src.rankings.services.derived.refresh_pool_derived_data")
    @patch("src.pool.services.ranking.recalculate_pool_scores")
    @patch("src.pool.services.ranking.recalculate_match_scores")
    def test_normal_change_recalcs_only_changed_matches(self, m_match, m_pool, m_refresh):
        g = self._match("G-2", self.group_stage, self.group)
        recalculate_after_sync(self.season, [g], podium_changed=False, group_stage_just_closed=False)
        m_match.assert_called_once_with(g)
        m_pool.assert_not_called()

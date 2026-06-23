from django.test import TestCase

from src.rankings.tests import make_pool_with_participants
from src.rankings.views import build_ranking_dashboard_context


class LeaderboardDivisionsContextTests(TestCase):
    def test_context_exposes_divisions_for_large_pool(self):
        pool, participants = make_pool_with_participants(10)
        context = build_ranking_dashboard_context(pool=pool, participant=participants[0])
        self.assertIn("leaderboard_divisions", context)
        keys = [d.key for d in context["leaderboard_divisions"]]
        self.assertEqual(keys[0], "liga")
        self.assertEqual(keys[-1], "zona")

    def test_context_plain_division_for_small_pool(self):
        pool, participants = make_pool_with_participants(4)
        context = build_ranking_dashboard_context(pool=pool, participant=participants[0])
        divisions = context["leaderboard_divisions"]
        self.assertEqual(len(divisions), 1)
        self.assertEqual(divisions[0].key, "plain")

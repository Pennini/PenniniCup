from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from src.football.models import Competition, Season
from src.pool.models import Pool, PoolParticipant
from src.rankings.models import RankingTieBreakOverride
from src.rankings.services.leaderboard import build_pool_leaderboard

User = get_user_model()


class RankingsAccessTest(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="owner", email="owner@example.com", password="123456Aa!")
        self.member = User.objects.create_user(username="member", email="member@example.com", password="123456Aa!")
        self.outsider = User.objects.create_user(
            username="outsider",
            email="outsider@example.com",
            password="123456Aa!",
        )

        competition = Competition.objects.create(fifa_id=900, name="Copa Ranking")
        season = Season.objects.create(
            fifa_id=900,
            competition=competition,
            name="Temporada Ranking",
            year=2026,
            start_date="2026-06-01",
            end_date="2026-07-30",
        )
        self.pool = Pool.objects.create(
            name="Pool Ranking",
            slug="pool-ranking",
            season=season,
            created_by=self.owner,
            requires_payment=False,
        )
        self.member_participant = PoolParticipant.objects.create(pool=self.pool, user=self.member, is_active=True)

    def test_only_active_participant_can_access_pool_dashboard(self):
        self.client.force_login(self.outsider)
        response = self.client.get(reverse("pool:ranking", kwargs={"slug": self.pool.slug}))
        self.assertEqual(response.status_code, 404)

    def test_active_participant_can_access_pool_dashboard(self):
        self.client.force_login(self.member)
        response = self.client.get(reverse("pool:ranking", kwargs={"slug": self.pool.slug}))
        self.assertEqual(response.status_code, 200)


class RankingsOrderTest(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="owner2", email="owner2@example.com", password="123456Aa!")
        self.user_a = User.objects.create_user(username="a", email="a@example.com", password="123456Aa!")
        self.user_b = User.objects.create_user(username="b", email="b@example.com", password="123456Aa!")

        competition = Competition.objects.create(fifa_id=901, name="Copa Ranking 2")
        season = Season.objects.create(
            fifa_id=901,
            competition=competition,
            name="Temporada Ranking 2",
            year=2026,
            start_date="2026-06-01",
            end_date="2026-07-30",
        )
        self.pool = Pool.objects.create(
            name="Pool Ranking 2",
            slug="pool-ranking-2",
            season=season,
            created_by=self.owner,
            requires_payment=False,
        )

        self.participant_a = PoolParticipant.objects.create(
            pool=self.pool,
            user=self.user_a,
            is_active=True,
            total_points=100,
            exact_score_hits=5,
            winner_or_draw_hits=8,
            knockout_points=40,
            group_points=60,
        )
        self.participant_b = PoolParticipant.objects.create(
            pool=self.pool,
            user=self.user_b,
            is_active=True,
            total_points=100,
            exact_score_hits=5,
            winner_or_draw_hits=8,
            knockout_points=40,
            group_points=60,
        )

    def test_manual_override_changes_order_inside_tie_group(self):
        RankingTieBreakOverride.objects.create(
            pool=self.pool,
            participant=self.participant_b,
            manual_position=1,
            updated_by=self.owner,
        )

        rows = build_pool_leaderboard(pool=self.pool)
        self.assertEqual(rows[0].participant.id, self.participant_b.id)
        self.assertTrue(rows[0].tie_resolved_manually)

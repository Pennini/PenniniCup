from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from src.football.models import Competition, Season
from src.pool.models import Pool

User = get_user_model()


class RulesPageTest(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="owner-rules",
            email="owner-rules@example.com",
            password="123456Aa!",
        )
        competition = Competition.objects.create(fifa_id=991, name="Copa Rules")
        self.season = Season.objects.create(
            fifa_id=991,
            competition=competition,
            name="Temporada Rules",
            year=2026,
            start_date="2026-06-01",
            end_date="2026-07-30",
        )

        self.pool_a = Pool.objects.create(
            name="Pool Regras A",
            slug="pool-regras-a",
            season=self.season,
            created_by=self.owner,
            requires_payment=False,
        )
        self.pool_b = Pool.objects.create(
            name="Pool Regras B",
            slug="pool-regras-b",
            season=self.season,
            created_by=self.owner,
            requires_payment=False,
        )

        config_a = self.pool_a.get_scoring_config()
        config_a.group_exact_score_points = 13
        config_a.knockout_winner_advancing_points = 11
        config_a.knockout_exact_score_points = 7
        config_a.save()

    def test_rules_page_loads_and_uses_default_pool(self):
        response = self.client.get(reverse("penninicup:rules"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Pool Regras A")
        self.assertContains(response, "Acertar placar exato: +13")

    def test_rules_page_respects_selected_pool(self):
        config_b = self.pool_b.get_scoring_config()
        config_b.group_exact_score_points = 21
        config_b.save()

        response = self.client.get(reverse("penninicup:rules"), data={"pool": self.pool_b.slug})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Pool Regras B")
        self.assertContains(response, "21")

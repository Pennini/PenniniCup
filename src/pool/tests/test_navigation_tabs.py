from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from src.football.models import Competition, Season
from src.pool.models import Pool, PoolParticipant

User = get_user_model()


class NavigationTabsTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="navuser", email="nav@example.com", password="123456Aa!")
        self.client.force_login(self.user)
        competition = Competition.objects.create(fifa_id=700, name="Copa Nav")
        self.season = Season.objects.create(
            fifa_id=700,
            competition=competition,
            name="Temporada Nav",
            year=2026,
            start_date="2026-06-01",
            end_date="2026-07-30",
        )
        # "Zebra" entrou primeiro, "Alpha" depois -> default deve ser Zebra (nao alfabetico).
        self.pool_zebra = Pool.objects.create(
            name="Zebra", slug="zebra", season=self.season, created_by=self.user, requires_payment=False
        )
        self.pool_alpha = Pool.objects.create(
            name="Alpha", slug="alpha", season=self.season, created_by=self.user, requires_payment=False
        )
        self.part_zebra = PoolParticipant.objects.create(pool=self.pool_zebra, user=self.user, is_active=True)
        self.part_alpha = PoolParticipant.objects.create(pool=self.pool_alpha, user=self.user, is_active=True)

    def test_bets_tab_defaults_to_first_joined(self):
        response = self.client.get(reverse("pool:bets-tab"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["selected_pool"].slug, "zebra")

    def test_bets_tab_respects_pool_param(self):
        response = self.client.get(reverse("pool:bets-tab"), data={"pool": "alpha"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["selected_pool"].slug, "alpha")

    def test_bets_tab_toggle_prefix_carries_selected_pool(self):
        # Links das abas internas devem carregar ?pool=<slug> para nao resetar ao default.
        response = self.client.get(reverse("pool:bets-tab"), data={"pool": "alpha"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["toggle_query_prefix"], "pool=alpha&")

    def test_bets_tab_invalid_pool_falls_back_to_default(self):
        response = self.client.get(reverse("pool:bets-tab"), data={"pool": "naoexiste"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["selected_pool"].slug, "zebra")

    def test_bets_tab_empty_state(self):
        other = User.objects.create_user(username="lonely", email="lonely@example.com", password="123456Aa!")
        self.client.force_login(other)
        response = self.client.get(reverse("pool:bets-tab"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ainda não participa")

    def test_ranking_tab_defaults_to_first_joined(self):
        response = self.client.get(reverse("pool:ranking-tab"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["selected_pool"].slug, "zebra")

    def test_ranking_tab_defaults_to_ranking_view(self):
        response = self.client.get(reverse("pool:ranking-tab"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["active_tab"], "ranking")
        self.assertIn("leaderboard_rows", response.context)

    def test_ranking_tab_palpites_switches_view(self):
        response = self.client.get(reverse("pool:ranking-tab"), data={"tab": "palpites", "pool": "alpha"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["active_tab"], "palpites")
        self.assertIn("selectable_match_groups", response.context)
        self.assertEqual(response.context["selected_pool"].slug, "alpha")
        # Seletor de jogo deve preservar o bolão selecionado ao trocar de jogo.
        self.assertContains(response, '<input type="hidden" name="pool" value="alpha"')

    def test_ranking_tab_toggle_prefix_carries_selected_pool(self):
        # Toggle Classificação/Palpites deve carregar ?pool=<slug> na pagina slugless.
        response = self.client.get(reverse("pool:ranking-tab"), data={"pool": "alpha"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["toggle_query_prefix"], "pool=alpha&")

    def test_bets_tab_skips_inactive_pool_for_default(self):
        # Zebra was joined first but is now inactive -> default should skip to Alpha.
        self.pool_zebra.is_active = False
        self.pool_zebra.save(update_fields=["is_active"])
        response = self.client.get(reverse("pool:bets-tab"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["selected_pool"].slug, "alpha")

    def test_navbar_has_palpites_and_ranking_links(self):
        response = self.client.get(reverse("pool:bets-tab"))
        self.assertContains(response, reverse("pool:bets-tab"))
        self.assertContains(response, reverse("pool:ranking-tab"))

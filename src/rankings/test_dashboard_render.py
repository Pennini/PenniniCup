from django.template.loader import render_to_string
from django.test import RequestFactory, TestCase

from src.rankings.services.divisions import build_divisions
from src.rankings.tests import make_pool_with_participants  # ver nota da Task 2


class DashboardDivisionRenderTests(TestCase):
    def _render(self, n):
        pool, participants = make_pool_with_participants(n)
        from src.rankings.services.leaderboard import build_pool_leaderboard

        rows = build_pool_leaderboard(pool=pool)
        request = RequestFactory().get("/")
        request.user = participants[0].user
        return render_to_string(
            "rankings/pool_dashboard.html",
            {
                "pool": pool,
                "active_tab": "ranking",
                "leaderboard_rows": rows,
                "leaderboard_divisions": build_divisions(rows),
                "podium_cards": [],
                "current_participant": participants[0],
                "current_position": 1,
                "total_participants": n,
                "points_gap": 0,
                "total_prize_amount": 0,
                "first_place_amount": 0,
                "second_place_amount": 0,
                "third_place_amount": 0,
                "participations": [],
            },
            request=request,
        )

    def test_large_pool_shows_division_labels(self):
        html = self._render(10)
        self.assertIn("Liga dos Campeões", html)
        self.assertIn("Série A", html)
        self.assertIn("Zona de Rebaixamento", html)
        self.assertIn("division-card-gold", html)
        self.assertIn("division-card-red", html)

    def test_rows_have_profile_photo(self):
        html = self._render(10)
        self.assertIn("rounded-full", html)
        self.assertIn("img/user.png", html)  # fallback quando sem profile_image

    def test_small_pool_renders_plain_without_band_labels(self):
        html = self._render(5)
        self.assertNotIn("Liga dos Campeões", html)
        self.assertIn("division-card-plain", html)

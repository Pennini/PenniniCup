from django.template.loader import render_to_string
from django.test import RequestFactory, TestCase
from django.utils import timezone

from src.football.models import Stage
from src.rankings.services.divisions import build_divisions
from src.rankings.tests import make_pool_with_participants


class MatchGuessesDivisionRenderTests(TestCase):
    def _render(self, n):
        pool, participants = make_pool_with_participants(n)
        # Need a real selected_match so the {% if selected_match %} guard passes
        stage = Stage.objects.create(
            fifa_id=f"stage-render-{n}-{id(pool)}",
            season=pool.season,
            name="Fase de Grupos",
        )
        from src.football.models import Match

        selected_match = Match.objects.create(
            fifa_id=f"match-render-{n}-{id(pool)}",
            season=pool.season,
            stage=stage,
            match_number=1,
            match_date_utc=timezone.now(),
            match_date_local=timezone.now(),
            match_date_brasilia=timezone.now(),
            status=Match.STATUS_SCHEDULED,
        )
        guess_rows = [{"position": i + 1, "participant": p, "bet": None} for i, p in enumerate(participants)]
        request = RequestFactory().get("/")
        request.user = participants[0].user
        return render_to_string(
            "rankings/partials/_match_guesses_body.html",
            {
                "pool": pool,
                "selected_match": selected_match,
                "guesses_locked": False,
                "match_finished": False,
                "guess_rows": guess_rows,
                "guess_divisions": build_divisions(guess_rows, position_getter=lambda r: r["position"]),
                "guess_aggregates": [],
            },
            request=request,
        )

    def test_by_participant_shows_divisions_and_photo(self):
        html = self._render(10)
        self.assertIn("Liga dos Campeões", html)
        self.assertIn("Zona de Rebaixamento", html)
        self.assertIn("division-card-gold", html)
        self.assertIn("rounded-full", html)
        self.assertIn("img/user.png", html)

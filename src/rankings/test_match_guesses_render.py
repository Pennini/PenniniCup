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


class MatchGuessesAdvancingTeamRenderTests(TestCase):
    """Mata-mata tipo 2: o "→ TIME" da linha vem de row.advancing_team (classificado
    projetado, presente mesmo em placar decisivo), com fallback no winner_pred
    do palpite para manter o comportamento antigo (tipo 1 / rows sem annotation).
    """

    def _render(self, rows, aggregates, pool, participants):
        from src.football.models import Match

        stage = Stage.objects.create(fifa_id=f"stage-adv-{id(pool)}", season=pool.season, name="Oitavas de Final")
        selected_match = Match.objects.create(
            fifa_id=f"match-adv-{id(pool)}",
            season=pool.season,
            stage=stage,
            match_number=1,
            match_date_utc=timezone.now(),
            match_date_local=timezone.now(),
            match_date_brasilia=timezone.now(),
            status=Match.STATUS_SCHEDULED,
        )
        request = RequestFactory().get("/")
        request.user = participants[0].user
        return render_to_string(
            "rankings/partials/_match_guesses_body.html",
            {
                "pool": pool,
                "selected_match": selected_match,
                "guesses_locked": False,
                "match_finished": False,
                "guess_rows": rows,
                "guess_divisions": build_divisions(rows, position_getter=lambda r: r["position"]),
                "guess_aggregates": aggregates,
            },
            request=request,
        )

    def test_decisive_score_renders_projected_advancing_team(self):
        from types import SimpleNamespace

        pool, participants = make_pool_with_participants(1)
        bet = SimpleNamespace(home_score_pred=2, away_score_pred=1, winner_pred=None, score=None)
        advancing = SimpleNamespace(code="BBB")
        rows = [{"position": 1, "participant": participants[0], "bet": bet, "advancing_team": advancing}]
        aggregates = [{"label": "2 x 1", "home": 2, "away": 1, "count": 1, "is_no_guess": False, "rows": rows}]

        html = self._render(rows, aggregates, pool, participants)

        # 3 pontos de render: card mobile, tabela desktop e lista "por palpite".
        self.assertEqual(html.count("→ BBB"), 3)

    def test_winner_pred_fallback_when_no_annotation(self):
        from types import SimpleNamespace

        pool, participants = make_pool_with_participants(1)
        winner = SimpleNamespace(code="CCC")
        bet = SimpleNamespace(home_score_pred=1, away_score_pred=1, winner_pred=winner, score=None)
        rows = [{"position": 1, "participant": participants[0], "bet": bet, "advancing_team": None}]

        html = self._render(rows, [], pool, participants)

        self.assertIn("→ CCC", html)

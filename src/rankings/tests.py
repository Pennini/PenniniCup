from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from src.football.models import Competition, Match, Season, Stage, Team
from src.payments.models import Payment
from src.pool.models import Pool, PoolBet, PoolBetScore, PoolParticipant
from src.rankings.models import RankingTieBreakOverride
from src.rankings.services.leaderboard import build_pool_leaderboard
from src.rankings.services.match_guesses import (
    _build_guess_rows,
    build_match_guesses_context,
    resolve_adjacent,
    resolve_default_match,
)

User = get_user_model()


def _make_match(season, stage, *, number, kickoff, status=Match.STATUS_SCHEDULED, home=None, away=None, group=None):
    return Match.objects.create(
        fifa_id=f"M{season.fifa_id}-{number}",
        season=season,
        stage=stage,
        group=group,
        match_number=number,
        match_date_utc=kickoff,
        match_date_local=kickoff,
        match_date_brasilia=kickoff,
        status=status,
        home_team=home,
        away_team=away,
    )


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

    def test_ranking_username_links_to_public_profile(self):
        self.client.force_login(self.member)
        response = self.client.get(reverse("pool:ranking", kwargs={"slug": self.pool.slug}))
        self.assertContains(response, f"/perfil/{self.member.username}/?pool={self.pool.slug}")

    def test_ranking_shows_total_collected_and_podium_amounts(self):
        Payment.objects.create(user=self.member, pool=self.pool, status="approved", amount=100, amount_received=100)
        self.client.force_login(self.member)

        response = self.client.get(reverse("pool:ranking", kwargs={"slug": self.pool.slug}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Premiação")
        self.assertContains(response, "R$ 95,00")
        self.assertContains(response, "R$ 65,00")
        self.assertContains(response, "R$ 20,00")
        self.assertContains(response, "R$ 10,00")


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
            advancing_hits=8,
            knockout_points=40,
            group_points=60,
        )
        self.participant_b = PoolParticipant.objects.create(
            pool=self.pool,
            user=self.user_b,
            is_active=True,
            total_points=100,
            exact_score_hits=5,
            advancing_hits=8,
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


class RankingsPaidParticipantsOnlyTest(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="owner3", email="owner3@example.com", password="123456Aa!")
        self.paid_user = User.objects.create_user(username="paid", email="paid@example.com", password="123456Aa!")
        self.unpaid_user = User.objects.create_user(
            username="unpaid",
            email="unpaid@example.com",
            password="123456Aa!",
        )

        competition = Competition.objects.create(fifa_id=902, name="Copa Ranking 3")
        season = Season.objects.create(
            fifa_id=902,
            competition=competition,
            name="Temporada Ranking 3",
            year=2026,
            start_date="2026-06-01",
            end_date="2026-07-30",
        )
        self.pool = Pool.objects.create(
            name="Pool Ranking 3",
            slug="pool-ranking-3",
            season=season,
            created_by=self.owner,
            requires_payment=True,
        )

        self.paid_participant = PoolParticipant.objects.create(
            pool=self.pool,
            user=self.paid_user,
            is_active=True,
            total_points=120,
        )
        self.unpaid_participant = PoolParticipant.objects.create(
            pool=self.pool,
            user=self.unpaid_user,
            is_active=True,
            total_points=999,
        )

        Payment.objects.create(
            user=self.paid_user,
            pool=self.pool,
            status="approved",
            amount=100,
            amount_received=100,
        )

    def test_build_pool_leaderboard_includes_only_paid_participants_when_required(self):
        rows = build_pool_leaderboard(pool=self.pool)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].participant.id, self.paid_participant.id)

    def test_ranking_dashboard_hides_unpaid_participants_when_required(self):
        self.client.force_login(self.paid_user)

        response = self.client.get(reverse("pool:ranking", kwargs={"slug": self.pool.slug}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.paid_user.username)
        self.assertNotContains(response, self.unpaid_user.username)
        self.assertEqual(response.context["total_participants"], 1)


class MatchGuessesServiceTest(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.owner = User.objects.create_user(username="mg-owner", email="mg-owner@example.com", password="123456Aa!")
        competition = Competition.objects.create(fifa_id=910, name="Copa Palpites")
        self.season = Season.objects.create(
            fifa_id=910,
            competition=competition,
            name="Temporada Palpites",
            year=2026,
            start_date="2026-06-01",
            end_date="2026-07-30",
        )
        self.group_stage = Stage.objects.create(fifa_id="ST910G", season=self.season, name="Group Stage", order=1)
        self.pool = Pool.objects.create(
            name="Pool Palpites",
            slug="pool-palpites",
            season=self.season,
            created_by=self.owner,
            requires_payment=False,
        )

    def test_resolve_default_match_prefers_live_within_2h(self):
        now = timezone.now()
        live = _make_match(self.season, self.group_stage, number=1, kickoff=now - timedelta(hours=1))
        _make_match(self.season, self.group_stage, number=2, kickoff=now + timedelta(hours=1))
        self.assertEqual(resolve_default_match(self.season, now=now), live)

    def test_resolve_default_match_returns_next_upcoming_when_no_live(self):
        now = timezone.now()
        _make_match(self.season, self.group_stage, number=1, kickoff=now - timedelta(hours=3))
        upcoming = _make_match(self.season, self.group_stage, number=2, kickoff=now + timedelta(hours=2))
        self.assertEqual(resolve_default_match(self.season, now=now), upcoming)

    def test_resolve_default_match_falls_back_to_last_played(self):
        now = timezone.now()
        _make_match(self.season, self.group_stage, number=1, kickoff=now - timedelta(hours=5))
        last_played = _make_match(self.season, self.group_stage, number=2, kickoff=now - timedelta(hours=3))
        self.assertEqual(resolve_default_match(self.season, now=now), last_played)

    def test_resolve_default_match_none_when_no_matches(self):
        self.assertIsNone(resolve_default_match(self.season))

    def test_context_hides_guesses_until_phase_locked(self):
        now = timezone.now()
        _make_match(self.season, self.group_stage, number=1, kickoff=now + timedelta(hours=1))
        context = build_match_guesses_context(pool=self.pool, request=self.factory.get("/"))
        self.assertTrue(context["guesses_locked"])
        self.assertEqual(context["guess_rows"], [])

    def test_context_reveals_guesses_after_phase_locked(self):
        now = timezone.now()
        match = _make_match(self.season, self.group_stage, number=1, kickoff=now - timedelta(hours=1))
        member = User.objects.create_user(username="mg-member", email="mg-member@example.com", password="123456Aa!")
        participant = PoolParticipant.objects.create(pool=self.pool, user=member, is_active=True)
        PoolBet.objects.create(
            participant=participant, match=match, home_score_pred=2, away_score_pred=1, is_active=True
        )
        context = build_match_guesses_context(pool=self.pool, request=self.factory.get("/"))
        self.assertFalse(context["guesses_locked"])
        self.assertEqual(context["selected_match"], match)
        self.assertEqual(len(context["guess_rows"]), 1)
        self.assertEqual(context["guess_rows"][0]["bet"].home_score_pred, 2)

    def test_build_guess_rows_includes_all_eligible_with_and_without_bet(self):
        now = timezone.now()
        match = _make_match(self.season, self.group_stage, number=1, kickoff=now - timedelta(hours=1))
        user_with = User.objects.create_user(username="has-bet", email="has@example.com", password="123456Aa!")
        user_without = User.objects.create_user(username="no-bet", email="no@example.com", password="123456Aa!")
        p_with = PoolParticipant.objects.create(pool=self.pool, user=user_with, is_active=True)
        PoolParticipant.objects.create(pool=self.pool, user=user_without, is_active=True)
        PoolBet.objects.create(participant=p_with, match=match, home_score_pred=3, away_score_pred=0, is_active=True)

        rows = _build_guess_rows(self.pool, match)
        by_user = {row["participant"].user.username: row for row in rows}
        self.assertEqual(len(rows), 2)
        self.assertEqual(by_user["has-bet"]["bet"].home_score_pred, 3)
        self.assertIsNone(by_user["no-bet"]["bet"])

    def test_build_guess_rows_excludes_inactive_participant(self):
        now = timezone.now()
        match = _make_match(self.season, self.group_stage, number=1, kickoff=now - timedelta(hours=1))
        active_user = User.objects.create_user(username="active", email="active@example.com", password="123456Aa!")
        inactive_user = User.objects.create_user(username="inact", email="inact@example.com", password="123456Aa!")
        PoolParticipant.objects.create(pool=self.pool, user=active_user, is_active=True)
        PoolParticipant.objects.create(pool=self.pool, user=inactive_user, is_active=False)

        rows = _build_guess_rows(self.pool, match)
        self.assertEqual([row["participant"].user.username for row in rows], ["active"])

    def test_build_guess_rows_excludes_unpaid_when_payment_required(self):
        now = timezone.now()
        match = _make_match(self.season, self.group_stage, number=1, kickoff=now - timedelta(hours=1))
        paid_pool = Pool.objects.create(
            name="Pool Palpites Pago",
            slug="pool-palpites-pago",
            season=self.season,
            created_by=self.owner,
            requires_payment=True,
        )
        paid_user = User.objects.create_user(username="mg-paid", email="mg-paid@example.com", password="123456Aa!")
        unpaid_user = User.objects.create_user(
            username="mg-unpaid", email="mg-unpaid@example.com", password="123456Aa!"
        )
        PoolParticipant.objects.create(pool=paid_pool, user=paid_user, is_active=True)
        PoolParticipant.objects.create(pool=paid_pool, user=unpaid_user, is_active=True)
        Payment.objects.create(user=paid_user, pool=paid_pool, status="approved", amount=100, amount_received=100)

        rows = _build_guess_rows(paid_pool, match)
        self.assertEqual([row["participant"].user.username for row in rows], ["mg-paid"])

    def test_build_guess_rows_ordered_by_ranking_with_position(self):
        now = timezone.now()
        match = _make_match(self.season, self.group_stage, number=1, kickoff=now - timedelta(hours=1))
        low = User.objects.create_user(username="mg-low", email="mg-low@example.com", password="123456Aa!")
        high = User.objects.create_user(username="mg-high", email="mg-high@example.com", password="123456Aa!")
        mid = User.objects.create_user(username="mg-mid", email="mg-mid@example.com", password="123456Aa!")
        PoolParticipant.objects.create(pool=self.pool, user=low, is_active=True, total_points=10)
        PoolParticipant.objects.create(pool=self.pool, user=high, is_active=True, total_points=30)
        PoolParticipant.objects.create(pool=self.pool, user=mid, is_active=True, total_points=20)

        rows = _build_guess_rows(self.pool, match)

        self.assertEqual([row["participant"].user.username for row in rows], ["mg-high", "mg-mid", "mg-low"])
        self.assertEqual([row["position"] for row in rows], [1, 2, 3])

    def test_resolve_selected_match_honors_valid_match_id(self):
        now = timezone.now()
        default_match = _make_match(self.season, self.group_stage, number=1, kickoff=now - timedelta(hours=1))
        other_match = _make_match(self.season, self.group_stage, number=2, kickoff=now - timedelta(hours=5))
        context = build_match_guesses_context(pool=self.pool, request=self.factory.get("/", {"match": other_match.id}))
        self.assertEqual(context["selected_match"], other_match)
        self.assertNotEqual(context["selected_match"], default_match)

    def test_selected_match_from_other_season_is_ignored(self):
        now = timezone.now()
        default_match = _make_match(self.season, self.group_stage, number=1, kickoff=now - timedelta(hours=1))
        other_comp = Competition.objects.create(fifa_id=912, name="Outra Copa")
        other_season = Season.objects.create(
            fifa_id=912,
            competition=other_comp,
            name="Outra",
            year=2026,
            start_date="2026-06-01",
            end_date="2026-07-30",
        )
        other_stage = Stage.objects.create(fifa_id="ST912G", season=other_season, name="Group Stage", order=2)
        foreign_match = _make_match(other_season, other_stage, number=1, kickoff=now - timedelta(hours=2))
        context = build_match_guesses_context(
            pool=self.pool, request=self.factory.get("/", {"match": foreign_match.id})
        )
        self.assertEqual(context["selected_match"], default_match)

    def test_context_exposes_finished_result_and_points(self):
        now = timezone.now()
        match = _make_match(
            self.season, self.group_stage, number=1, kickoff=now - timedelta(hours=3), status=Match.STATUS_FINISHED
        )
        match.home_score = 2
        match.away_score = 1
        match.save(update_fields=["home_score", "away_score"])
        member = User.objects.create_user(username="fin-user", email="fin@example.com", password="123456Aa!")
        participant = PoolParticipant.objects.create(pool=self.pool, user=member, is_active=True)
        bet = PoolBet.objects.create(
            participant=participant, match=match, home_score_pred=2, away_score_pred=1, is_active=True
        )
        PoolBetScore.objects.create(bet=bet, points=10, exact_score=True)
        context = build_match_guesses_context(pool=self.pool, request=self.factory.get("/"))
        self.assertTrue(context["match_finished"])
        self.assertEqual(context["guess_rows"][0]["bet"].score.points, 10)
        self.assertTrue(context["guess_rows"][0]["bet"].score.exact_score)

    def test_context_reveals_result_when_scores_present_but_status_not_finished(self):
        now = timezone.now()
        match = _make_match(
            self.season, self.group_stage, number=1, kickoff=now - timedelta(hours=3), status=Match.STATUS_SCHEDULED
        )
        match.home_score = 2
        match.away_score = 1
        match.save(update_fields=["home_score", "away_score"])
        context = build_match_guesses_context(pool=self.pool, request=self.factory.get("/"))
        self.assertTrue(context["match_finished"])

    def test_context_without_matches_returns_no_selection(self):
        context = build_match_guesses_context(pool=self.pool, request=self.factory.get("/"))
        self.assertIsNone(context["selected_match"])
        self.assertEqual(context["guess_rows"], [])
        self.assertFalse(context["guesses_locked"])

    def test_selectable_groups_split_by_phase_in_chronological_order(self):
        now = timezone.now()
        knockout_stage = Stage.objects.create(fifa_id="ST910K", season=self.season, name="Round of 16", order=3)
        _make_match(self.season, self.group_stage, number=1, kickoff=now - timedelta(days=2))
        _make_match(self.season, knockout_stage, number=2, kickoff=now + timedelta(days=2))
        context = build_match_guesses_context(pool=self.pool, request=self.factory.get("/"))
        labels = [group["label"] for group in context["selectable_match_groups"]]
        self.assertEqual(labels, ["Fase de Grupos", "Oitavas de Final"])

    def test_resolve_adjacent_returns_chronological_neighbors(self):
        now = timezone.now()
        m1 = _make_match(self.season, self.group_stage, number=1, kickoff=now - timedelta(days=2))
        m2 = _make_match(self.season, self.group_stage, number=2, kickoff=now - timedelta(days=1))
        m3 = _make_match(self.season, self.group_stage, number=3, kickoff=now + timedelta(days=1))
        matches = [m1, m2, m3]
        self.assertEqual(resolve_adjacent(matches, m2), (m1, m3))
        self.assertEqual(resolve_adjacent(matches, m1), (None, m2))
        self.assertEqual(resolve_adjacent(matches, m3), (m2, None))

    def test_resolve_adjacent_none_when_selected_missing(self):
        now = timezone.now()
        m1 = _make_match(self.season, self.group_stage, number=1, kickoff=now)
        self.assertEqual(resolve_adjacent([m1], None), (None, None))
        self.assertEqual(resolve_adjacent([], m1), (None, None))

    def test_context_exposes_prev_and_next_for_selected_match(self):
        now = timezone.now()
        m1 = _make_match(self.season, self.group_stage, number=1, kickoff=now - timedelta(days=2))
        m2 = _make_match(self.season, self.group_stage, number=2, kickoff=now - timedelta(days=1))
        m3 = _make_match(self.season, self.group_stage, number=3, kickoff=now + timedelta(days=1))
        context = build_match_guesses_context(pool=self.pool, request=self.factory.get("/", {"match": m2.id}))
        self.assertEqual(context["selected_match"], m2)
        self.assertEqual(context["prev_match"], m1)
        self.assertEqual(context["next_match"], m3)

    def test_context_prev_next_none_at_edges(self):
        now = timezone.now()
        first = _make_match(self.season, self.group_stage, number=1, kickoff=now - timedelta(days=1))
        second = _make_match(self.season, self.group_stage, number=2, kickoff=now + timedelta(days=1))
        ctx_first = build_match_guesses_context(pool=self.pool, request=self.factory.get("/", {"match": first.id}))
        self.assertIsNone(ctx_first["prev_match"])
        self.assertEqual(ctx_first["next_match"], second)
        ctx_last = build_match_guesses_context(pool=self.pool, request=self.factory.get("/", {"match": second.id}))
        self.assertEqual(ctx_last["prev_match"], first)
        self.assertIsNone(ctx_last["next_match"])

    def test_context_without_matches_has_no_neighbors(self):
        context = build_match_guesses_context(pool=self.pool, request=self.factory.get("/"))
        self.assertIsNone(context["prev_match"])
        self.assertIsNone(context["next_match"])


class MatchGuessesViewTest(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="v-owner", email="v-owner@example.com", password="123456Aa!")
        self.member = User.objects.create_user(username="v-member", email="v-member@example.com", password="123456Aa!")
        self.outsider = User.objects.create_user(username="v-out", email="v-out@example.com", password="123456Aa!")
        competition = Competition.objects.create(fifa_id=911, name="Copa View")
        self.season = Season.objects.create(
            fifa_id=911,
            competition=competition,
            name="Temporada View",
            year=2026,
            start_date="2026-06-01",
            end_date="2026-07-30",
        )
        self.group_stage = Stage.objects.create(fifa_id="ST911G", season=self.season, name="Group Stage", order=1)
        self.brazil = Team.objects.create(fifa_id="BRA911", name="Brasil 911", name_norm="brasil", code="BRA")
        self.argentina = Team.objects.create(fifa_id="ARG911", name="Argentina 911", name_norm="argentina", code="ARG")
        self.pool = Pool.objects.create(
            name="Pool View",
            slug="pool-view",
            season=self.season,
            created_by=self.owner,
            requires_payment=False,
        )
        self.participant = PoolParticipant.objects.create(pool=self.pool, user=self.member, is_active=True)
        now = timezone.now()
        self.match = _make_match(
            self.season,
            self.group_stage,
            number=1,
            kickoff=now - timedelta(hours=1),
            home=self.brazil,
            away=self.argentina,
        )
        PoolBet.objects.create(
            participant=self.participant, match=self.match, home_score_pred=2, away_score_pred=1, is_active=True
        )

    def _url(self):
        return reverse("pool:ranking", kwargs={"slug": self.pool.slug})

    def test_palpites_tab_renders_default_match_and_selector(self):
        self.client.force_login(self.member)
        response = self.client.get(self._url(), {"tab": "palpites"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["active_tab"], "palpites")
        self.assertEqual(response.context["selected_match"], self.match)
        self.assertContains(response, 'name="match"')
        self.assertContains(response, "Palpites por jogo")

    def test_palpites_tab_invalid_match_id_falls_back_to_default(self):
        self.client.force_login(self.member)
        for bad in ("abc", "999999"):
            response = self.client.get(self._url(), {"tab": "palpites", "match": bad})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.context["selected_match"], self.match)

    def test_invalid_tab_defaults_to_ranking(self):
        self.client.force_login(self.member)
        response = self.client.get(self._url(), {"tab": "bogus"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["active_tab"], "ranking")
        self.assertContains(response, "Classificação completa")

    def test_non_participant_cannot_access_palpites_tab(self):
        self.client.force_login(self.outsider)
        response = self.client.get(self._url(), {"tab": "palpites"})
        self.assertEqual(response.status_code, 404)

    def test_palpites_tab_shows_points_for_finished_match(self):
        self.match.status = Match.STATUS_FINISHED
        self.match.home_score = 2
        self.match.away_score = 1
        self.match.save(update_fields=["status", "home_score", "away_score"])
        PoolBetScore.objects.update_or_create(
            bet=self.match.pool_bets.first(), defaults={"points": 18, "exact_score": True}
        )

        self.client.force_login(self.member)
        response = self.client.get(self._url(), {"tab": "palpites"})

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["match_finished"])
        self.assertContains(response, "18")
        self.assertContains(response, "Exato")

    def test_palpites_tab_exposes_carousel_partial_url_and_arrow(self):
        _make_match(
            self.season,
            self.group_stage,
            number=2,
            kickoff=timezone.now() + timedelta(days=1),
            home=self.brazil,
            away=self.argentina,
        )
        self.client.force_login(self.member)
        response = self.client.get(self._url(), {"tab": "palpites"})
        self.assertContains(response, "match-guesses-body")
        self.assertContains(response, reverse("rankings:match-guesses-partial", kwargs={"slug": self.pool.slug}))
        self.assertContains(response, "data-match=")

    def _partial_url(self):
        return reverse("rankings:match-guesses-partial", kwargs={"slug": self.pool.slug})

    def test_partial_endpoint_reveals_guesses_for_locked_phase(self):
        self.client.force_login(self.member)
        response = self.client.get(self._partial_url(), {"match": self.match.id})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["guesses_locked"])
        self.assertEqual(len(response.context["guess_rows"]), 1)
        # Corpo parcial, nao a pagina inteira.
        self.assertNotContains(response, "Classificação completa")

    def test_partial_endpoint_keeps_open_phase_locked(self):
        # Bolão isolado cujo único jogo ainda não começou -> fase aberta -> travado.
        comp = Competition.objects.create(fifa_id=920, name="Copa Lock")
        season = Season.objects.create(
            fifa_id=920,
            competition=comp,
            name="Lock",
            year=2026,
            start_date="2026-06-01",
            end_date="2026-07-30",
        )
        stage = Stage.objects.create(fifa_id="ST920G", season=season, name="Group Stage", order=20)
        pool = Pool.objects.create(
            name="Pool Lock", slug="pool-lock", season=season, created_by=self.owner, requires_payment=False
        )
        PoolParticipant.objects.create(pool=pool, user=self.member, is_active=True)
        future = _make_match(
            season, stage, number=1, kickoff=timezone.now() + timedelta(days=3), home=self.brazil, away=self.argentina
        )
        self.client.force_login(self.member)
        url = reverse("rankings:match-guesses-partial", kwargs={"slug": pool.slug})
        response = self.client.get(url, {"match": future.id})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["guesses_locked"])
        self.assertContains(response, "travados")

    def test_partial_endpoint_non_participant_is_404(self):
        self.client.force_login(self.outsider)
        response = self.client.get(self._partial_url(), {"match": self.match.id})
        self.assertEqual(response.status_code, 404)


class RankingTabPoolSelectorTest(TestCase):
    """The slugless ranking-tab is the navbar/homepage entry. The pool selector
    must render on BOTH the ranking and palpites tabs, positioned above the
    ranking/palpites toggle.
    """

    def setUp(self):
        self.user = User.objects.create_user(username="rt-user", email="rt@example.com", password="123456Aa!")
        competition = Competition.objects.create(fifa_id=930, name="Copa Selector")
        self.season = Season.objects.create(
            fifa_id=930,
            competition=competition,
            name="Temporada Selector",
            year=2026,
            start_date="2026-06-01",
            end_date="2026-07-30",
        )
        self.pool_a = Pool.objects.create(
            name="Bolão A", slug="bolao-a", season=self.season, created_by=self.user, requires_payment=False
        )
        self.pool_b = Pool.objects.create(
            name="Bolão B", slug="bolao-b", season=self.season, created_by=self.user, requires_payment=False
        )
        PoolParticipant.objects.create(pool=self.pool_a, user=self.user, is_active=True)
        PoolParticipant.objects.create(pool=self.pool_b, user=self.user, is_active=True)

    def _url(self):
        return reverse("pool:ranking-tab")

    def test_pool_selector_renders_on_ranking_tab(self):
        self.client.force_login(self.user)
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="pool-selector"')
        self.assertContains(response, "Bolão A")
        self.assertContains(response, "Bolão B")

    def test_pool_selector_renders_on_palpites_tab(self):
        self.client.force_login(self.user)
        response = self.client.get(self._url(), {"tab": "palpites"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["active_tab"], "palpites")
        self.assertContains(response, 'id="pool-selector"')

    def test_pool_selector_is_above_toggle(self):
        self.client.force_login(self.user)
        for tab in ("ranking", "palpites"):
            response = self.client.get(self._url(), {"tab": tab})
            body = response.content.decode()
            selector_at = body.index('id="pool-selector"')
            toggle_at = body.index("Classificação")
            self.assertLess(selector_at, toggle_at, f"pool selector should be above the toggle on tab={tab}")


class ToggleSupporterStarsTest(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(username="ts-su", email="ts-su@example.com", password="123456Aa!")
        self.member = User.objects.create_user(username="ts-reg", email="ts-reg@example.com", password="123456Aa!")
        competition = Competition.objects.create(fifa_id=920, name="Copa Toggle")
        season = Season.objects.create(
            fifa_id=920,
            competition=competition,
            name="Temporada Toggle",
            year=2026,
            start_date="2026-06-01",
            end_date="2026-07-30",
        )
        self.pool = Pool.objects.create(
            name="Pool Toggle",
            slug="pool-toggle",
            season=season,
            created_by=self.admin,
            requires_payment=False,
            show_supporter_stars=True,
        )

    def _url(self):
        return reverse("rankings:toggle-stars", kwargs={"slug": self.pool.slug})

    def test_get_is_not_allowed(self):
        self.client.force_login(self.admin)
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 405)

    def test_non_superuser_is_forbidden_and_value_unchanged(self):
        self.client.force_login(self.member)
        response = self.client.post(self._url())
        self.assertEqual(response.status_code, 403)
        self.pool.refresh_from_db()
        self.assertTrue(self.pool.show_supporter_stars)

    def test_superuser_post_toggles_and_redirects(self):
        self.client.force_login(self.admin)

        response = self.client.post(self._url())
        self.assertEqual(response.status_code, 302)
        self.pool.refresh_from_db()
        self.assertFalse(self.pool.show_supporter_stars)

        self.client.post(self._url())
        self.pool.refresh_from_db()
        self.assertTrue(self.pool.show_supporter_stars)

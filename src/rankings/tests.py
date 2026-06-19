from datetime import timedelta
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from src.football.models import Competition, Match, Season, Stage, Team
from src.payments.models import Payment
from src.pool.models import Pool, PoolBet, PoolBetScore, PoolParticipant
from src.rankings.models import PoolRankingHistory, RankingTieBreakOverride
from src.rankings.services.leaderboard import build_pool_leaderboard
from src.rankings.services.match_guesses import (
    _build_guess_rows,
    build_guess_aggregates,
    build_match_guesses_context,
    resolve_adjacent,
    resolve_default_match,
)
from src.rankings.services.position_snapshot import snapshot_round_for_match

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

    def test_context_includes_guess_aggregates_when_revealed(self):
        now = timezone.now()
        match = _make_match(self.season, self.group_stage, number=1, kickoff=now - timedelta(hours=1))
        u1 = User.objects.create_user(username="agg1", email="agg1@example.com", password="123456Aa!")
        u2 = User.objects.create_user(username="agg2", email="agg2@example.com", password="123456Aa!")
        u3 = User.objects.create_user(username="agg3", email="agg3@example.com", password="123456Aa!")
        p1 = PoolParticipant.objects.create(pool=self.pool, user=u1, is_active=True, total_points=30)
        p2 = PoolParticipant.objects.create(pool=self.pool, user=u2, is_active=True, total_points=20)
        PoolParticipant.objects.create(pool=self.pool, user=u3, is_active=True, total_points=10)
        PoolBet.objects.create(participant=p1, match=match, home_score_pred=2, away_score_pred=0, is_active=True)
        PoolBet.objects.create(participant=p2, match=match, home_score_pred=2, away_score_pred=0, is_active=True)

        context = build_match_guesses_context(pool=self.pool, request=self.factory.get("/"))

        aggregates = context["guess_aggregates"]
        # Most-guessed first (2x0 with two), then "Sem palpite" (u3) last.
        self.assertEqual(aggregates[0]["label"], "2 x 0")
        self.assertEqual(aggregates[0]["count"], 2)
        self.assertEqual([r["participant"].user.username for r in aggregates[0]["rows"]], ["agg1", "agg2"])
        self.assertTrue(aggregates[-1]["is_no_guess"])
        self.assertEqual([r["participant"].user.username for r in aggregates[-1]["rows"]], ["agg3"])

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
        # Ambas as visões de palpites disponíveis via toggle.
        self.assertContains(response, 'data-guesses-view-btn="by-participant"')
        self.assertContains(response, 'data-guesses-view-btn="by-guess"')
        self.assertContains(response, 'data-guesses-view="by-guess"')

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


class BuildGuessAggregatesTest(TestCase):
    """Pure aggregation over ranking-ordered guess_rows: group by scoreline,
    order groups by popularity (count desc, then scoreline desc), 'sem palpite'
    always last, ranking order preserved within each group.
    """

    @staticmethod
    def _row(position, username, bet):
        participant = SimpleNamespace(user=SimpleNamespace(username=username))
        return {"position": position, "participant": participant, "bet": bet}

    @staticmethod
    def _bet(home, away, winner=None):
        return SimpleNamespace(home_score_pred=home, away_score_pred=away, winner_pred=winner)

    def test_groups_ordered_by_count_then_scoreline_with_no_guess_last(self):
        rows = [
            self._row(1, "a", self._bet(2, 0)),
            self._row(2, "b", self._bet(1, 0)),
            self._row(3, "c", self._bet(2, 0)),
            self._row(4, "d", None),
            self._row(5, "e", self._bet(1, 0)),
            self._row(6, "f", self._bet(3, 1)),
        ]

        aggregates = build_guess_aggregates(rows)

        # 2x0 (count 2) and 1x0 (count 2) tie -> scoreline desc puts 2x0 first;
        # 3x1 (count 1) next; "sem palpite" always last.
        self.assertEqual(
            [(g["label"], g["count"], g["is_no_guess"]) for g in aggregates],
            [("2 x 0", 2, False), ("1 x 0", 2, False), ("3 x 1", 1, False), (None, 1, True)],
        )

    def test_rows_within_group_keep_ranking_order_and_position(self):
        rows = [
            self._row(1, "leader", self._bet(2, 0)),
            self._row(4, "tail", self._bet(2, 0)),
        ]

        [group] = build_guess_aggregates(rows)

        self.assertEqual([r["position"] for r in group["rows"]], [1, 4])
        self.assertEqual([r["participant"].user.username for r in group["rows"]], ["leader", "tail"])

    def test_empty_rows_returns_empty(self):
        self.assertEqual(build_guess_aggregates([]), [])


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


class PoolRankingHistoryModelTest(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="hist-owner", email="ho@example.com", password="123456Aa!")
        self.member = User.objects.create_user(username="hist-member", email="hm@example.com", password="123456Aa!")
        competition = Competition.objects.create(fifa_id=940, name="Copa Hist")
        self.season = Season.objects.create(
            fifa_id=940,
            competition=competition,
            name="Temporada Hist",
            year=2026,
            start_date="2026-06-01",
            end_date="2026-07-30",
        )
        self.stage = Stage.objects.create(fifa_id="ST940G", season=self.season, name="Group Stage", order=1)
        self.pool = Pool.objects.create(
            name="Pool Hist", slug="pool-hist", season=self.season, created_by=self.owner, requires_payment=False
        )
        self.participant = PoolParticipant.objects.create(pool=self.pool, user=self.member, is_active=True)
        self.match = _make_match(self.season, self.stage, number=1, kickoff=timezone.now())

    def test_history_row_persists_ranking_snapshot(self):
        row = PoolRankingHistory.objects.create(
            pool=self.pool,
            participant=self.participant,
            match=self.match,
            round_index=1,
            position=3,
            total_points=42,
            group_points=20,
            knockout_points=22,
            exact_score_hits=4,
            advancing_hits=6,
            champion_hit=True,
            top_scorer_hit=False,
        )
        row.refresh_from_db()
        self.assertEqual(row.round_index, 1)
        self.assertEqual(row.position, 3)
        self.assertEqual(row.total_points, 42)
        self.assertTrue(row.champion_hit)

    def test_history_unique_per_pool_participant_match(self):
        PoolRankingHistory.objects.create(
            pool=self.pool, participant=self.participant, match=self.match, round_index=1, position=1
        )
        with self.assertRaises(IntegrityError):
            PoolRankingHistory.objects.create(
                pool=self.pool, participant=self.participant, match=self.match, round_index=2, position=2
            )


class SnapshotRoundForMatchTest(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="snap-owner", email="so@example.com", password="123456Aa!")
        self.u_high = User.objects.create_user(username="snap-high", email="sh@example.com", password="123456Aa!")
        self.u_low = User.objects.create_user(username="snap-low", email="sl@example.com", password="123456Aa!")
        competition = Competition.objects.create(fifa_id=941, name="Copa Snap")
        self.season = Season.objects.create(
            fifa_id=941,
            competition=competition,
            name="Temporada Snap",
            year=2026,
            start_date="2026-06-01",
            end_date="2026-07-30",
        )
        self.stage = Stage.objects.create(fifa_id="ST941G", season=self.season, name="Group Stage", order=1)
        self.pool = Pool.objects.create(
            name="Pool Snap", slug="pool-snap", season=self.season, created_by=self.owner, requires_payment=False
        )
        self.p_high = PoolParticipant.objects.create(pool=self.pool, user=self.u_high, is_active=True, total_points=30)
        self.p_low = PoolParticipant.objects.create(pool=self.pool, user=self.u_low, is_active=True, total_points=10)
        self.match = _make_match(self.season, self.stage, number=1, kickoff=timezone.now())
        PoolBet.objects.create(
            participant=self.p_high, match=self.match, home_score_pred=1, away_score_pred=0, is_active=True
        )

    def _finish(self, match, home=1, away=0):
        match.home_score = home
        match.away_score = away
        match.save(update_fields=["home_score", "away_score"])

    def test_no_score_writes_nothing(self):
        snapshot_round_for_match(self.match)
        self.assertEqual(PoolRankingHistory.objects.count(), 0)

    def test_finished_match_writes_one_row_per_participant(self):
        self._finish(self.match)
        snapshot_round_for_match(self.match)
        rows = PoolRankingHistory.objects.filter(pool=self.pool, match=self.match)
        self.assertEqual(rows.count(), 2)
        by_pid = {r.participant_id: r for r in rows}
        self.p_high.refresh_from_db()
        self.assertEqual(by_pid[self.p_high.id].position, 1)
        # Snapshot espelha o agregado vivo (pós-recálculo disparado pelo placar).
        self.assertEqual(by_pid[self.p_high.id].total_points, self.p_high.total_points)
        self.assertEqual(by_pid[self.p_low.id].position, 2)
        self.assertTrue(all(r.round_index == 1 for r in rows))

    def test_only_affected_pools_are_snapshotted(self):
        other_pool = Pool.objects.create(
            name="Pool Outro",
            slug="pool-outro",
            season=self.season,
            created_by=self.owner,
            requires_payment=False,
        )
        PoolParticipant.objects.create(pool=other_pool, user=self.u_low, is_active=True, total_points=5)
        self._finish(self.match)
        snapshot_round_for_match(self.match)
        self.assertEqual(PoolRankingHistory.objects.filter(pool=other_pool).count(), 0)

    def test_re_snapshot_same_match_updates_in_place(self):
        self._finish(self.match)
        snapshot_round_for_match(self.match)
        # Correção: inverte a liderança e re-snapshota.
        self.p_low.total_points = 99
        self.p_low.save(update_fields=["total_points"])
        snapshot_round_for_match(self.match)
        rows = PoolRankingHistory.objects.filter(pool=self.pool, match=self.match)
        self.assertEqual(rows.count(), 2)
        by_pid = {r.participant_id: r for r in rows}
        self.assertEqual(by_pid[self.p_low.id].position, 1)
        self.assertTrue(all(r.round_index == 1 for r in rows))

    def test_second_match_increments_round_index(self):
        self._finish(self.match)
        snapshot_round_for_match(self.match)
        match2 = _make_match(self.season, self.stage, number=2, kickoff=timezone.now())
        PoolBet.objects.create(
            participant=self.p_high, match=match2, home_score_pred=2, away_score_pred=2, is_active=True
        )
        self._finish(match2)
        snapshot_round_for_match(match2)
        self.assertEqual(
            set(PoolRankingHistory.objects.filter(pool=self.pool).values_list("round_index", flat=True)),
            {1, 2},
        )


class SnapshotSignalTest(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="sig-owner", email="sigo@example.com", password="123456Aa!")
        self.member = User.objects.create_user(username="sig-mem", email="sigm@example.com", password="123456Aa!")
        competition = Competition.objects.create(fifa_id=942, name="Copa Sig")
        self.season = Season.objects.create(
            fifa_id=942,
            competition=competition,
            name="Temporada Sig",
            year=2026,
            start_date="2026-06-01",
            end_date="2026-07-30",
        )
        self.stage = Stage.objects.create(fifa_id="ST942G", season=self.season, name="Group Stage", order=1)
        self.pool = Pool.objects.create(
            name="Pool Sig", slug="pool-sig", season=self.season, created_by=self.owner, requires_payment=False
        )
        self.participant = PoolParticipant.objects.create(pool=self.pool, user=self.member, is_active=True)
        self.match = _make_match(self.season, self.stage, number=1, kickoff=timezone.now())
        PoolBet.objects.create(
            participant=self.participant, match=self.match, home_score_pred=1, away_score_pred=0, is_active=True
        )

    def test_saving_match_with_score_creates_history(self):
        self.match.home_score = 1
        self.match.away_score = 0
        self.match.save(update_fields=["home_score", "away_score"])
        self.assertEqual(PoolRankingHistory.objects.filter(pool=self.pool, match=self.match).count(), 1)

    def test_saving_match_without_score_creates_no_history(self):
        self.match.match_number = 99
        self.match.save(update_fields=["match_number"])
        self.assertEqual(PoolRankingHistory.objects.filter(pool=self.pool).count(), 0)


class LeaderboardMovementTest(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="mov-owner", email="mvo@example.com", password="123456Aa!")
        self.u_a = User.objects.create_user(username="mov-a", email="mva@example.com", password="123456Aa!")
        self.u_b = User.objects.create_user(username="mov-b", email="mvb@example.com", password="123456Aa!")
        competition = Competition.objects.create(fifa_id=943, name="Copa Mov")
        self.season = Season.objects.create(
            fifa_id=943,
            competition=competition,
            name="Temporada Mov",
            year=2026,
            start_date="2026-06-01",
            end_date="2026-07-30",
        )
        self.stage = Stage.objects.create(fifa_id="ST943G", season=self.season, name="Group Stage", order=1)
        self.pool = Pool.objects.create(
            name="Pool Mov", slug="pool-mov", season=self.season, created_by=self.owner, requires_payment=False
        )
        # Estado atual: A líder (1º), B (2º).
        self.p_a = PoolParticipant.objects.create(pool=self.pool, user=self.u_a, is_active=True, total_points=50)
        self.p_b = PoolParticipant.objects.create(pool=self.pool, user=self.u_b, is_active=True, total_points=30)
        self.match1 = _make_match(self.season, self.stage, number=1, kickoff=timezone.now())
        self.match2 = _make_match(self.season, self.stage, number=2, kickoff=timezone.now())

    def _round(self, match, round_index, positions):
        # positions: {participant: position}
        for participant, position in positions.items():
            PoolRankingHistory.objects.create(
                pool=self.pool,
                participant=participant,
                match=match,
                round_index=round_index,
                position=position,
            )

    def test_movement_none_when_single_round(self):
        self._round(self.match1, 1, {self.p_a: 1, self.p_b: 2})
        rows = build_pool_leaderboard(pool=self.pool)
        self.assertTrue(all(row.movement is None for row in rows))

    def test_movement_up_down_and_equal(self):
        # Rodada anterior (round 1): B 1º, A 2º. Atual: A 1º, B 2º.
        self._round(self.match1, 1, {self.p_b: 1, self.p_a: 2})
        self._round(self.match2, 2, {self.p_a: 1, self.p_b: 2})
        rows = {row.participant.id: row for row in build_pool_leaderboard(pool=self.pool)}
        self.assertEqual(rows[self.p_a.id].movement, 1)  # 2 -> 1, subiu 1
        self.assertEqual(rows[self.p_b.id].movement, -1)  # 1 -> 2, caiu 1

    def test_movement_none_for_participant_without_previous_round(self):
        # Round anterior só tem A; B entrou depois.
        self._round(self.match1, 1, {self.p_a: 1})
        self._round(self.match2, 2, {self.p_a: 1, self.p_b: 2})
        rows = {row.participant.id: row for row in build_pool_leaderboard(pool=self.pool)}
        self.assertEqual(rows[self.p_a.id].movement, 0)
        self.assertIsNone(rows[self.p_b.id].movement)


class RankingBadgeTemplateTest(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="bdg-owner", email="bdo@example.com", password="123456Aa!")
        self.u_a = User.objects.create_user(username="bdg-a", email="bda@example.com", password="123456Aa!")
        self.u_b = User.objects.create_user(username="bdg-b", email="bdb@example.com", password="123456Aa!")
        competition = Competition.objects.create(fifa_id=944, name="Copa Bdg")
        self.season = Season.objects.create(
            fifa_id=944,
            competition=competition,
            name="Temporada Bdg",
            year=2026,
            start_date="2026-06-01",
            end_date="2026-07-30",
        )
        self.stage = Stage.objects.create(fifa_id="ST944G", season=self.season, name="Group Stage", order=1)
        self.pool = Pool.objects.create(
            name="Pool Bdg", slug="pool-bdg", season=self.season, created_by=self.owner, requires_payment=False
        )
        # Atual: A 1º (50), B 2º (30).
        self.p_a = PoolParticipant.objects.create(pool=self.pool, user=self.u_a, is_active=True, total_points=50)
        self.p_b = PoolParticipant.objects.create(pool=self.pool, user=self.u_b, is_active=True, total_points=30)
        self.match1 = _make_match(self.season, self.stage, number=1, kickoff=timezone.now())
        self.match2 = _make_match(self.season, self.stage, number=2, kickoff=timezone.now())
        # Rodada anterior: B 1º, A 2º -> A subiu 1 (▲1), B caiu 1 (▼1).
        for participant, position in {self.p_b: 1, self.p_a: 2}.items():
            PoolRankingHistory.objects.create(
                pool=self.pool, participant=participant, match=self.match1, round_index=1, position=position
            )
        for participant, position in {self.p_a: 1, self.p_b: 2}.items():
            PoolRankingHistory.objects.create(
                pool=self.pool, participant=participant, match=self.match2, round_index=2, position=position
            )

    def test_dashboard_renders_movement_badges(self):
        self.client.force_login(self.u_a)
        response = self.client.get(reverse("pool:ranking", kwargs={"slug": self.pool.slug}))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("▲1", body)
        self.assertIn("▼1", body)

    def test_dashboard_omits_badge_when_no_movement(self):
        # Sem rodada anterior distinta -> sem badge.
        PoolRankingHistory.objects.filter(pool=self.pool, round_index=1).delete()
        self.client.force_login(self.u_a)
        response = self.client.get(reverse("pool:ranking", kwargs={"slug": self.pool.slug}))
        body = response.content.decode()
        self.assertNotIn("▲", body)
        self.assertNotIn("▼", body)

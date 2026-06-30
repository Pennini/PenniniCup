import datetime
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone
from django.utils.timezone import now as tz_now

from src.accounts.models import InviteToken
from src.football.models import AssignThird, Competition, Group, Match, Player, Season, Stage, Standing, Team
from src.payments.models import Payment
from src.pool.models import Pool, PoolBet, PoolParticipant, PoolParticipantStanding, PoolProjectionRecalc
from src.pool.services.asof_standings import AsOfStanding, compute_asof_standings
from src.pool.services.context_builder import (
    _build_projected_groups_from_rows,
    _build_third_rows_from_rows,
    _build_winners_map,
    _infer_advancing_team,
    _infer_losing_team,
    _make_pairs,
    _projection_is_stale_from_prefetched,
    build_pool_participant_view_context,
)
from src.pool.services.context_builder import (
    _normalize_stage_key as _cb_normalize_stage_key,
)
from src.pool.services.projection import (
    load_assign_third_map,
    projected_group_standings,
    resolve_knockout_placeholder_team,
    sync_persisted_group_standings,
)
from src.pool.services.projection_queue import (
    MAX_ATTEMPTS,
    enqueue_projection_recalc,
    has_pending_projection_recalc,
    process_next_projection_recalc_job,
)
from src.pool.services.ranking import recalculate_participant_scores
from src.pool.services.rules import PHASE_GROUP, PHASE_KNOCKOUT, POOL_TYPE_2, normalize_stage_key, phase_for_match
from src.pool.services.scoring import _winner_from_score, calculate_bet_points

User = get_user_model()


class PoolBetRulesTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="u1", email="u1@example.com", password="123456Aa!")
        self.competition = Competition.objects.create(fifa_id=1, name="Copa")
        self.season = Season.objects.create(
            fifa_id=1,
            competition=self.competition,
            name="Temporada",
            year=2026,
            start_date="2026-06-01",
            end_date="2026-07-30",
        )
        self.stage = Stage.objects.create(fifa_id="GROUP", season=self.season, name="Group Stage", order=1)
        self.home = Team.objects.create(fifa_id="H", name="Home", name_norm="home", code="HOM")
        self.away = Team.objects.create(fifa_id="A", name="Away", name_norm="away", code="AWY")
        now = timezone.now()

        self.match = Match.objects.create(
            fifa_id="M1",
            season=self.season,
            stage=self.stage,
            match_number=1,
            match_date_utc=now,
            match_date_local=now,
            match_date_brasilia=now + timezone.timedelta(hours=2),
            home_team=self.home,
            away_team=self.away,
        )

        self.pool = Pool.objects.create(name="Pool 1", slug="pool-1", season=self.season, created_by=self.user)
        self.participant = PoolParticipant.objects.create(pool=self.pool, user=self.user, is_active=True)

    def test_participant_without_payment_cannot_bet(self):
        bet = PoolBet(participant=self.participant, match=self.match, home_score_pred=1, away_score_pred=0)
        with self.assertRaises(ValidationError):
            bet.full_clean()

    def test_admin_skip_lock_allows_bet_without_payment(self):
        bet = PoolBet(participant=self.participant, match=self.match, home_score_pred=1, away_score_pred=0)
        bet._admin_skip_lock = True
        bet.full_clean()  # admin bypassa pagamento/janela
        bet.save()
        self.assertTrue(PoolBet.objects.get(participant=self.participant, match=self.match).is_active)

    def test_admin_form_bypasses_lock_when_allowed(self):
        from src.pool.admin import PoolBetAdminForm

        data = {
            "participant": self.participant.id,
            "match": self.match.id,
            "home_score_pred": 1,
            "away_score_pred": 0,
        }
        form = PoolBetAdminForm(data=data, allow_skip_lock=True)
        self.assertTrue(form.is_valid(), form.errors)

    def test_admin_form_enforces_rules_when_not_allowed(self):
        from src.pool.admin import PoolBetAdminForm

        data = {
            "participant": self.participant.id,
            "match": self.match.id,
            "home_score_pred": 1,
            "away_score_pred": 0,
        }
        # Sem allow_skip_lock (ex.: usuário não-superuser) as regras valem:
        # participante sem pagamento não pode palpitar.
        form = PoolBetAdminForm(data=data)
        self.assertFalse(form.is_valid())


class PoolBetAdminPermissionTest(TestCase):
    def _req(self, user):
        return type("Req", (), {"user": user})()

    def test_only_superuser_can_add_change_delete(self):
        from django.contrib.admin.sites import site

        from src.pool.admin import PoolBetAdmin

        admin_obj = PoolBetAdmin(PoolBet, site)
        staff = User.objects.create_user(
            username="staff", email="staff@example.com", password="123456Aa!", is_staff=True
        )
        superuser = User.objects.create_superuser(username="root", email="root@example.com", password="123456Aa!")

        for perm in ("has_add_permission", "has_change_permission", "has_delete_permission"):
            self.assertFalse(getattr(admin_obj, perm)(self._req(staff)), perm)
            self.assertTrue(getattr(admin_obj, perm)(self._req(superuser)), perm)


class PoolJoinTokenTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="joinuser", email="join@example.com", password="123456Aa!")
        self.other = User.objects.create_user(username="other", email="other@example.com", password="123456Aa!")

        self.competition = Competition.objects.create(fifa_id=2, name="Copa 2")
        self.season = Season.objects.create(
            fifa_id=2,
            competition=self.competition,
            name="Temporada 2",
            year=2026,
            start_date="2026-06-01",
            end_date="2026-07-30",
        )
        self.pool = Pool.objects.create(name="Pool Join", slug="pool-join", season=self.season, created_by=self.other)
        self.pool_b = Pool.objects.create(name="Pool B", slug="pool-b", season=self.season, created_by=self.other)

        self.token = InviteToken.objects.create(created_by=self.other, pool=self.pool, max_uses=2)
        self.other_pool_token = InviteToken.objects.create(created_by=self.other, pool=self.pool_b, max_uses=2)

    def test_join_requires_valid_token_for_same_pool(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("pool:join", kwargs={"slug": self.pool.slug}), data={"invite_token": str(self.token.token)}
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(PoolParticipant.objects.filter(pool=self.pool, user=self.user).exists())

    def test_join_rejects_token_from_other_pool(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("pool:join", kwargs={"slug": self.pool.slug}),
            data={"invite_token": str(self.other_pool_token.token)},
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(PoolParticipant.objects.filter(pool=self.pool, user=self.user).exists())

    def test_join_by_token_from_home(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("pool:join-by-token"),
            data={"invite_token": str(self.token.token)},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(PoolParticipant.objects.filter(pool=self.pool, user=self.user).exists())

    def test_join_by_token_invalid(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("pool:join-by-token"),
            data={"invite_token": "invalid-token"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(PoolParticipant.objects.filter(pool=self.pool, user=self.user).exists())


class PoolDynamicScoringConfigTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="dyn", email="dyn@example.com", password="123456Aa!")
        self.owner = User.objects.create_user(username="dynowner", email="dynowner@example.com", password="123456Aa!")

        self.competition = Competition.objects.create(fifa_id=220, name="Copa Dinamica")
        self.season = Season.objects.create(
            fifa_id=220,
            competition=self.competition,
            name="Temporada Dinamica",
            year=2026,
            start_date="2026-06-01",
            end_date="2026-07-30",
        )
        self.stage_group = Stage.objects.create(fifa_id="GDYN", season=self.season, name="Group Stage", order=220)
        self.team_a = Team.objects.create(fifa_id="DYA", name="Dyn A", name_norm="dyna", code="DYA")
        self.team_b = Team.objects.create(fifa_id="DYB", name="Dyn B", name_norm="dynb", code="DYB")
        self.team_c = Team.objects.create(fifa_id="DYC", name="Dyn C", name_norm="dync", code="DYC")

        now = timezone.now()
        self.match = Match.objects.create(
            fifa_id="DYN-M1",
            season=self.season,
            stage=self.stage_group,
            match_number=1,
            match_date_utc=now,
            match_date_local=now,
            match_date_brasilia=now,
            home_team=self.team_a,
            away_team=self.team_b,
            home_score=2,
            away_score=1,
            status=Match.STATUS_FINISHED,
        )

        self.pool = Pool.objects.create(
            name="Pool Dinamico",
            slug="pool-dinamico",
            season=self.season,
            created_by=self.owner,
            requires_payment=False,
        )
        self.participant = PoolParticipant.objects.create(
            pool=self.pool,
            user=self.user,
            is_active=True,
            champion_pred=self.team_a,
            runner_up_pred=self.team_b,
            third_place_pred=self.team_c,
        )

        self.pool.get_scoring_config()
        self.pool.scoring_config.group_exact_score = 8
        self.pool.scoring_config.bonus_champion_points = 9
        self.pool.scoring_config.bonus_runner_up_points = 7
        self.pool.scoring_config.bonus_third_place_points = 5
        self.pool.scoring_config.bonus_top_scorer_points = 3
        self.pool.scoring_config.save()

        official = self.pool.get_official_results()
        official.champion = self.team_a
        official.runner_up = self.team_b
        official.third_place = self.team_c
        official.save()

        PoolBet.objects.create(
            participant=self.participant,
            match=self.match,
            home_score_pred=2,
            away_score_pred=1,
        )

    def test_recalculate_uses_db_config_and_bonus(self):
        recalculate_participant_scores(self.participant)
        self.participant.refresh_from_db()

        # 8 pontos do placar exato em grupos (group_exact_score=8) + 9 + 7 + 5 de bonus
        self.assertEqual(self.participant.group_points, 8)
        self.assertEqual(self.participant.bonus_points, 21)
        self.assertEqual(self.participant.total_points, 29)
        self.assertTrue(self.participant.champion_hit)


class PoolOpenTargetTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="openuser", email="open@example.com", password="123456Aa!")
        self.owner = User.objects.create_user(
            username="owneropen",
            email="owneropen@example.com",
            password="123456Aa!",
        )

        competition = Competition.objects.create(fifa_id=22, name="Copa Open")
        season = Season.objects.create(
            fifa_id=22,
            competition=competition,
            name="Temporada Open",
            year=2026,
            start_date="2026-06-01",
            end_date="2026-07-30",
        )
        self.pool = Pool.objects.create(
            name="Pool Open",
            slug="pool-open",
            season=season,
            created_by=self.owner,
            requires_payment=False,
        )
        PoolParticipant.objects.create(pool=self.pool, user=self.user, is_active=True)

    def test_open_pool_defaults_to_bets(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse("pool:open"), data={"pool_slug": self.pool.slug})
        self.assertRedirects(response, reverse("pool:detail", kwargs={"slug": self.pool.slug}))

    def test_open_pool_can_redirect_to_ranking(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("pool:open"),
            data={"pool_slug": self.pool.slug, "open_target": "ranking"},
        )
        self.assertRedirects(response, reverse("pool:ranking", kwargs={"slug": self.pool.slug}))


class PoolPrizeDistributionTest(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="ownerpd", email="ownerpd@example.com", password="123456Aa!")
        self.user_active = User.objects.create_user(
            username="activepd", email="activepd@example.com", password="123456Aa!"
        )
        self.user_inactive = User.objects.create_user(
            username="inactivepd", email="inactivepd@example.com", password="123456Aa!"
        )
        self.competition = Competition.objects.create(fifa_id=33, name="Copa 33")
        self.season = Season.objects.create(
            fifa_id=33,
            competition=self.competition,
            name="Temporada 33",
            year=2026,
            start_date="2026-06-01",
            end_date="2026-07-30",
        )
        self.pool = Pool.objects.create(
            name="Pool Prize",
            slug="pool-prize",
            season=self.season,
            created_by=self.owner,
        )

    def test_refresh_prize_distribution_ignores_inactive_participant_payments(self):
        PoolParticipant.objects.create(pool=self.pool, user=self.user_active, is_active=True)
        PoolParticipant.objects.create(pool=self.pool, user=self.user_inactive, is_active=False)

        Payment.objects.create(
            user=self.user_active,
            pool=self.pool,
            amount=100,
            amount_received=120,
            status="approved",
            payment_method="pix",
        )
        Payment.objects.create(
            user=self.user_inactive,
            pool=self.pool,
            amount=999,
            amount_received=999,
            status="approved",
            payment_method="pix",
        )

        self.pool.refresh_prize_distribution(save=True)

        self.pool.refresh_from_db()
        self.assertEqual(str(self.pool.total_prize_amount), "114.00")

    def test_refresh_prize_distribution_uses_gross_percentage_split(self):
        PoolParticipant.objects.create(pool=self.pool, user=self.user_active, is_active=True)

        Payment.objects.create(
            user=self.user_active,
            pool=self.pool,
            amount=100,
            amount_received=100,
            status="approved",
            payment_method="pix",
        )

        self.pool.refresh_prize_distribution(save=True)
        self.pool.refresh_from_db()

        self.assertEqual(str(self.pool.admin_fee_amount), "5.00")
        self.assertEqual(str(self.pool.first_place_amount), "65.00")
        self.assertEqual(str(self.pool.second_place_amount), "20.00")
        self.assertEqual(str(self.pool.third_place_amount), "10.00")
        self.assertEqual(str(self.pool.total_prize_amount), "95.00")

    def test_refresh_prize_distribution_rejects_percentage_sum_different_from_100(self):
        PoolParticipant.objects.create(pool=self.pool, user=self.user_active, is_active=True)
        self.pool.first_place_percentage = 70

        with self.assertRaises(ValidationError):
            self.pool.refresh_prize_distribution(save=False)


class ProjectedStandingsTieBreakerTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="tiebreak", email="tb@example.com", password="123456Aa!")
        self.competition = Competition.objects.create(fifa_id=3, name="Copa 3")
        self.season = Season.objects.create(
            fifa_id=3,
            competition=self.competition,
            name="Temporada 3",
            year=2026,
            start_date="2026-06-01",
            end_date="2026-07-30",
        )
        self.stage = Stage.objects.create(fifa_id="GROUP3", season=self.season, name="Group Stage", order=30)
        self.group = Group.objects.create(fifa_id="G-A", stage=self.stage, name="A")

        # Team A tem pior codigo, mas melhor world_ranking.
        self.team_a = Team.objects.create(
            fifa_id="TA",
            name="Team A",
            name_norm="team-a",
            code="ZZZ",
            world_ranking=5,
            group=self.group,
        )
        self.team_b = Team.objects.create(
            fifa_id="TB",
            name="Team B",
            name_norm="team-b",
            code="AAA",
            world_ranking=10,
            group=self.group,
        )
        self.team_c = Team.objects.create(
            fifa_id="TC",
            name="Team C",
            name_norm="team-c",
            code="CCC",
            world_ranking=20,
            group=self.group,
        )

        now = timezone.now()
        self.match_1 = Match.objects.create(
            fifa_id="M31",
            season=self.season,
            stage=self.stage,
            group=self.group,
            match_number=1,
            match_date_utc=now,
            match_date_local=now,
            match_date_brasilia=now,
            home_team=self.team_a,
            away_team=self.team_c,
        )
        self.match_2 = Match.objects.create(
            fifa_id="M32",
            season=self.season,
            stage=self.stage,
            group=self.group,
            match_number=2,
            match_date_utc=now + timezone.timedelta(hours=1),
            match_date_local=now + timezone.timedelta(hours=1),
            match_date_brasilia=now + timezone.timedelta(hours=1),
            home_team=self.team_b,
            away_team=self.team_c,
        )

        self.pool = Pool.objects.create(
            name="Pool Tie Break",
            slug="pool-tie-break",
            season=self.season,
            created_by=self.user,
            requires_payment=False,
        )
        self.participant = PoolParticipant.objects.create(pool=self.pool, user=self.user, is_active=True)

        PoolBet.objects.create(participant=self.participant, match=self.match_1, home_score_pred=1, away_score_pred=0)
        PoolBet.objects.create(participant=self.participant, match=self.match_2, home_score_pred=1, away_score_pred=0)

    def test_world_ranking_is_last_tiebreaker_after_points_goal_diff_and_goals_for(self):
        projected_groups = projected_group_standings(participant=self.participant, season=self.season)
        self.assertEqual(len(projected_groups), 1)

        standings = projected_groups[0]["standings"]
        self.assertGreaterEqual(len(standings), 2)

        self.assertEqual(standings[0].team.id, self.team_a.id)
        self.assertEqual(standings[1].team.id, self.team_b.id)


class PoolAutoBetLifecycleTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="autobet", email="autobet@example.com", password="123456Aa!")
        self.competition = Competition.objects.create(fifa_id=4, name="Copa 4")
        self.season = Season.objects.create(
            fifa_id=4,
            competition=self.competition,
            name="Temporada 4",
            year=2026,
            start_date="2026-06-01",
            end_date="2026-07-30",
        )
        self.stage_group = Stage.objects.create(fifa_id="GROUP4", season=self.season, name="Group Stage", order=40)
        self.stage_r16 = Stage.objects.create(fifa_id="R16-4", season=self.season, name="Round of 16", order=41)
        self.group_a = Group.objects.create(fifa_id="GA4", stage=self.stage_group, name="A")

        self.team_a = Team.objects.create(
            fifa_id="A4", name="Alpha 4", name_norm="alpha4", code="A4", group=self.group_a
        )
        self.team_b = Team.objects.create(
            fifa_id="B4", name="Beta 4", name_norm="beta4", code="B4", group=self.group_a
        )
        self.player_a = Player.objects.create(
            fifa_id="P4A",
            team=self.team_a,
            name="Artilheiro A",
            short_name="A. A",
            position="Forward",
        )

        now = timezone.now()
        self.group_match = Match.objects.create(
            fifa_id="GM4",
            season=self.season,
            stage=self.stage_group,
            group=self.group_a,
            match_number=70,
            match_date_utc=now,
            match_date_local=now,
            match_date_brasilia=now,
            home_team=self.team_a,
            away_team=self.team_b,
        )
        self.knockout_match = Match.objects.create(
            fifa_id="KM4",
            season=self.season,
            stage=self.stage_r16,
            match_number=71,
            match_date_utc=now + timezone.timedelta(hours=1),
            match_date_local=now + timezone.timedelta(hours=1),
            match_date_brasilia=now + timezone.timedelta(hours=1),
            home_placeholder="W70",
            away_placeholder="W70",
            home_team=self.team_a,
            away_team=self.team_b,
        )

        self.pool = Pool.objects.create(
            name="Pool Auto Bet",
            slug="pool-auto-bet",
            season=self.season,
            created_by=self.user,
            requires_payment=False,
        )
        self.participant = PoolParticipant.objects.create(pool=self.pool, user=self.user, is_active=True)

    def test_pool_detail_precreates_all_bets_as_inactive(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("pool:detail", kwargs={"slug": self.pool.slug}))
        self.assertEqual(response.status_code, 200)

        bets = PoolBet.objects.filter(participant=self.participant).order_by("match__match_number")
        self.assertEqual(bets.count(), 2)
        self.assertFalse(any(bet.is_active for bet in bets))

    def test_winner_placeholder_uses_predicted_winner(self):
        PoolBet.objects.create(
            participant=self.participant,
            match=self.group_match,
            home_score_pred=2,
            away_score_pred=1,
            winner_pred=self.team_a,
        )

        self.client.force_login(self.user)
        response = self.client.get(reverse("pool:detail", kwargs={"slug": self.pool.slug}))
        self.assertEqual(response.status_code, 200)

        knockout_rows = response.context["knockout_rows"]
        self.assertEqual(len(knockout_rows), 1)
        self.assertIsNotNone(knockout_rows[0]["home_team"])
        self.assertEqual(knockout_rows[0]["home_team"].id, self.team_a.id)

    def test_bets_tab_orders_matches_by_match_date(self):
        # Plan 02-01 changed sort to match_date_brasilia ascending.
        # Give match_number=10 an earlier date so it sorts before self.group_match (match_number=70, date=now).
        earlier = timezone.now() - timezone.timedelta(days=10)
        early_date_match = Match.objects.create(
            fifa_id="GM4-ORDER",
            season=self.season,
            stage=self.stage_group,
            group=self.group_a,
            match_number=10,
            match_date_utc=earlier,
            match_date_local=earlier,
            match_date_brasilia=earlier,
            home_team=self.team_a,
            away_team=self.team_b,
        )

        self.client.force_login(self.user)
        response = self.client.get(reverse("pool:detail", kwargs={"slug": self.pool.slug}))
        self.assertEqual(response.status_code, 200)

        dates = [row["match"].match_date_brasilia for row in response.context["group_rows"]]
        self.assertEqual(dates, sorted(dates))
        match_numbers = [row["match"].match_number for row in response.context["group_rows"]]
        self.assertIn(early_date_match.match_number, match_numbers)

    def test_bets_tab_shows_phase_with_group_and_stage_name(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("pool:detail", kwargs={"slug": self.pool.slug}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Fase de Grupos - Grupo A")
        self.assertContains(response, "Mata-mata - Round of 16")

    def test_winner_placeholder_uses_score_when_winner_not_selected(self):
        PoolBet.objects.create(
            participant=self.participant,
            match=self.group_match,
            home_score_pred=3,
            away_score_pred=1,
            winner_pred=None,
        )

        self.client.force_login(self.user)
        response = self.client.get(reverse("pool:detail", kwargs={"slug": self.pool.slug}))
        self.assertEqual(response.status_code, 200)

        knockout_rows = response.context["knockout_rows"]
        self.assertEqual(len(knockout_rows), 1)
        self.assertIsNotNone(knockout_rows[0]["home_team"])
        self.assertEqual(knockout_rows[0]["home_team"].id, self.team_a.id)

    def test_loser_placeholder_ru_uses_losing_team(self):
        now = timezone.now()
        ru_match = Match.objects.create(
            fifa_id="KM4-RU",
            season=self.season,
            stage=self.stage_r16,
            match_number=72,
            match_date_utc=now + timezone.timedelta(hours=2),
            match_date_local=now + timezone.timedelta(hours=2),
            match_date_brasilia=now + timezone.timedelta(hours=2),
            home_placeholder="RU70",
            away_placeholder="W70",
        )

        PoolBet.objects.create(
            participant=self.participant,
            match=self.group_match,
            home_score_pred=3,
            away_score_pred=1,
            winner_pred=None,
        )

        self.client.force_login(self.user)
        response = self.client.get(reverse("pool:detail", kwargs={"slug": self.pool.slug}))
        self.assertEqual(response.status_code, 200)

        knockout_rows = response.context["knockout_rows"]
        ru_row = next((row for row in knockout_rows if row["match"].id == ru_match.id), None)
        self.assertIsNotNone(ru_row)
        self.assertIsNotNone(ru_row["home_team"])
        self.assertEqual(ru_row["home_team"].id, self.team_b.id)

    def test_bulk_save_enqueues_group_projection_recalc(self):
        future = timezone.now() + timezone.timedelta(days=1)
        self.group_match.match_date_utc = future
        self.group_match.match_date_local = future
        self.group_match.match_date_brasilia = future
        self.group_match.save(update_fields=["match_date_utc", "match_date_local", "match_date_brasilia"])

        self.client.force_login(self.user)

        response = self.client.post(
            reverse("pool:save-bets-bulk", kwargs={"slug": self.pool.slug}),
            data={
                f"match_{self.group_match.id}_home_score_pred": "2",
                f"match_{self.group_match.id}_away_score_pred": "1",
                f"match_{self.group_match.id}_winner_pred": "",
            },
        )
        self.assertEqual(response.status_code, 302)

        job = self.participant.projection_recalc
        self.assertEqual(job.status, PoolProjectionRecalc.STATUS_PENDING)

    def test_bulk_save_skips_knockout_match_without_teams(self):
        future = timezone.now() + timezone.timedelta(days=1)
        match_no_teams = Match.objects.create(
            fifa_id="KM4NT",
            season=self.season,
            stage=self.stage_r16,
            match_number=99,
            match_date_utc=future,
            match_date_local=future,
            match_date_brasilia=future,
            home_placeholder="W69",
            away_placeholder="W70",
        )
        self.group_match.match_date_utc = future
        self.group_match.match_date_local = future
        self.group_match.match_date_brasilia = future
        self.group_match.save(update_fields=["match_date_utc", "match_date_local", "match_date_brasilia"])

        self.client.force_login(self.user)
        response = self.client.post(
            reverse("pool:save-bets-bulk", kwargs={"slug": self.pool.slug}),
            data={
                f"match_{self.group_match.id}_home_score_pred": "2",
                f"match_{self.group_match.id}_away_score_pred": "1",
                f"match_{match_no_teams.id}_home_score_pred": "1",
                f"match_{match_no_teams.id}_away_score_pred": "0",
            },
        )
        self.assertEqual(response.status_code, 302)

        # Group match bet must be saved despite the invalid knockout bet
        group_bet = PoolBet.objects.get(participant=self.participant, match=self.group_match)
        self.assertEqual(group_bet.home_score_pred, 2)
        self.assertEqual(group_bet.away_score_pred, 1)

        # Knockout match without teams must not be saved
        knockout_bet = PoolBet.objects.get(participant=self.participant, match=match_no_teams)
        self.assertIsNone(knockout_bet.home_score_pred)
        self.assertIsNone(knockout_bet.away_score_pred)

    def test_knockout_draw_without_winner_is_saved_inactive(self):
        future = timezone.now() + timezone.timedelta(days=1)
        self.group_match.match_date_utc = future
        self.group_match.match_date_local = future
        self.group_match.match_date_brasilia = future
        self.group_match.save(update_fields=["match_date_utc", "match_date_local", "match_date_brasilia"])

        bet = PoolBet(
            participant=self.participant,
            match=self.knockout_match,
            home_score_pred=1,
            away_score_pred=1,
            winner_pred=None,
        )
        bet.full_clean()
        bet.save()

        bet.refresh_from_db()
        self.assertFalse(bet.is_active)

    def test_bulk_save_updates_top_scorer_prediction(self):
        future = timezone.now() + timezone.timedelta(days=1)
        self.group_match.match_date_utc = future
        self.group_match.match_date_local = future
        self.group_match.match_date_brasilia = future
        self.group_match.save(update_fields=["match_date_utc", "match_date_local", "match_date_brasilia"])

        self.client.force_login(self.user)
        response = self.client.post(
            reverse("pool:save-bets-bulk", kwargs={"slug": self.pool.slug}),
            data={"top_scorer_pred": str(self.player_a.id)},
        )
        self.assertEqual(response.status_code, 302)

        self.participant.refresh_from_db()
        self.assertEqual(self.participant.top_scorer_pred_id, self.player_a.id)

    def test_group_draw_updates_classification_immediately_after_save(self):
        future = timezone.now() + timezone.timedelta(days=1)
        self.group_match.match_date_utc = future
        self.group_match.match_date_local = future
        self.group_match.match_date_brasilia = future
        self.group_match.save(update_fields=["match_date_utc", "match_date_local", "match_date_brasilia"])

        self.client.force_login(self.user)
        response = self.client.post(
            reverse("pool:save-bets-bulk", kwargs={"slug": self.pool.slug}),
            data={
                f"match_{self.group_match.id}_home_score_pred": "2",
                f"match_{self.group_match.id}_away_score_pred": "2",
                f"match_{self.group_match.id}_winner_pred": "",
            },
        )
        self.assertEqual(response.status_code, 302)

        detail_response = self.client.get(reverse("pool:detail", kwargs={"slug": self.pool.slug}))
        self.assertEqual(detail_response.status_code, 200)

        projected_groups = detail_response.context["projected_groups"]
        self.assertEqual(len(projected_groups), 1)
        standings = projected_groups[0]["standings"]
        self.assertEqual(len(standings), 2)
        self.assertEqual(standings[0].points, 1)
        self.assertEqual(standings[1].points, 1)

    def test_bulk_save_skips_invalid_bets_and_saves_valid_ones(self):
        future = timezone.now() + timezone.timedelta(days=1)
        self.group_match.match_date_utc = future
        self.group_match.match_date_local = future
        self.group_match.match_date_brasilia = future
        self.group_match.save(update_fields=["match_date_utc", "match_date_local", "match_date_brasilia"])

        self.knockout_match.match_date_utc = future
        self.knockout_match.match_date_local = future
        self.knockout_match.match_date_brasilia = future
        self.knockout_match.save(update_fields=["match_date_utc", "match_date_local", "match_date_brasilia"])

        self.client.force_login(self.user)
        response = self.client.post(
            reverse("pool:save-bets-bulk", kwargs={"slug": self.pool.slug}),
            data={
                "top_scorer_pred": str(self.player_a.id),
                f"match_{self.group_match.id}_home_score_pred": "2",
                f"match_{self.group_match.id}_away_score_pred": "1",
                f"match_{self.group_match.id}_winner_pred": "",
                f"match_{self.knockout_match.id}_home_score_pred": "abc",
                f"match_{self.knockout_match.id}_away_score_pred": "1",
                f"match_{self.knockout_match.id}_winner_pred": "",
            },
        )
        self.assertEqual(response.status_code, 302)

        # Valid bets must be saved despite the invalid knockout bet
        self.participant.refresh_from_db()
        self.assertEqual(self.participant.top_scorer_pred_id, self.player_a.id)

        group_bet = PoolBet.objects.get(participant=self.participant, match=self.group_match)
        self.assertEqual(group_bet.home_score_pred, 2)
        self.assertEqual(group_bet.away_score_pred, 1)
        self.assertTrue(group_bet.is_active)

        # Invalid knockout bet must not be saved
        knockout_bet = PoolBet.objects.get(participant=self.participant, match=self.knockout_match)
        self.assertIsNone(knockout_bet.home_score_pred)
        self.assertIsNone(knockout_bet.away_score_pred)
        self.assertFalse(knockout_bet.is_active)

    def test_bulk_save_updates_updated_at_on_changed_bets(self):
        future = timezone.now() + timezone.timedelta(days=1)
        self.group_match.match_date_utc = future
        self.group_match.match_date_local = future
        self.group_match.match_date_brasilia = future
        self.group_match.save(update_fields=["match_date_utc", "match_date_local", "match_date_brasilia"])

        self.client.force_login(self.user)

        timestamp_before = timezone.now()
        self.client.post(
            reverse("pool:save-bets-bulk", kwargs={"slug": self.pool.slug}),
            data={
                f"match_{self.group_match.id}_home_score_pred": "2",
                f"match_{self.group_match.id}_away_score_pred": "1",
            },
        )

        bet = PoolBet.objects.get(participant=self.participant, match=self.group_match)
        self.assertGreater(bet.updated_at, timestamp_before)


class AssignThirdPlaceholderNormalizationTest(TestCase):
    def test_assign_third_accepts_hyphenated_placeholder(self):
        competition = Competition.objects.create(fifa_id=10, name="Copa 10")
        season = Season.objects.create(
            fifa_id=10,
            competition=competition,
            name="Temporada 10",
            year=2026,
            start_date="2026-06-01",
            end_date="2026-07-30",
        )

        team = Team.objects.create(fifa_id="T10", name="Team 10", name_norm="team10", code="T10")
        AssignThird.objects.create(
            season=season,
            groups_key="A,C,D,F,H,I,K,L",
            placeholder="3-CEFHI",
            third_group="C",
        )

        assign_map = load_assign_third_map(season=season, qualified_groups=["A", "C", "D", "F", "H", "I", "K", "L"])
        resolved = resolve_knockout_placeholder_team(
            placeholder="3CEFHI",
            projected_slots={"C3": team},
            assign_third_map=assign_map,
        )

        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.id, team.id)


class ProjectionQueueRetryLimitTest(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="owner-queue", email="ownerq@example.com", password="123456Aa!")
        self.user = User.objects.create_user(username="user-queue", email="userq@example.com", password="123456Aa!")
        competition = Competition.objects.create(fifa_id=401, name="Copa Queue")
        season = Season.objects.create(
            fifa_id=401,
            competition=competition,
            name="Temporada Queue",
            year=2026,
            start_date="2026-06-01",
            end_date="2026-07-30",
        )
        pool = Pool.objects.create(name="Pool Queue", slug="pool-queue", season=season, created_by=self.owner)
        self.participant = PoolParticipant.objects.create(pool=pool, user=self.user, is_active=True)

    def test_enqueue_revives_failed_job_and_resets_attempts(self):
        """Nova edição do usuário deve rearmar job FAILED@max e zerar tentativas."""
        job = PoolProjectionRecalc.objects.create(
            participant=self.participant,
            status=PoolProjectionRecalc.STATUS_FAILED,
            attempts=MAX_ATTEMPTS,
            last_error="boom",
        )

        enqueue_projection_recalc(self.participant)

        job.refresh_from_db()
        self.assertEqual(job.status, PoolProjectionRecalc.STATUS_PENDING)
        self.assertEqual(job.attempts, 0)
        self.assertEqual(job.last_error, "")

    @patch("src.pool.services.projection_queue.sync_persisted_group_standings", side_effect=RuntimeError("boom"))
    def test_job_becomes_failed_when_reaching_max_attempts(self, _sync_mock):
        job = PoolProjectionRecalc.objects.create(
            participant=self.participant,
            status=PoolProjectionRecalc.STATUS_PENDING,
            attempts=MAX_ATTEMPTS - 1,
        )

        processed = process_next_projection_recalc_job()

        self.assertIsNotNone(processed)
        job.refresh_from_db()
        self.assertEqual(job.attempts, MAX_ATTEMPTS)
        self.assertEqual(job.status, PoolProjectionRecalc.STATUS_FAILED)
        self.assertEqual(job.last_error, "boom")

    @patch("src.pool.services.projection_queue.sync_persisted_group_standings", side_effect=RuntimeError("boom"))
    def test_job_is_requeued_while_attempts_below_max(self, _sync_mock):
        job = PoolProjectionRecalc.objects.create(
            participant=self.participant,
            status=PoolProjectionRecalc.STATUS_PENDING,
            attempts=0,
        )

        processed = process_next_projection_recalc_job()

        self.assertIsNotNone(processed)
        job.refresh_from_db()
        self.assertEqual(job.attempts, 1)
        self.assertEqual(job.status, PoolProjectionRecalc.STATUS_PENDING)
        self.assertEqual(job.last_error, "boom")

    def test_concurrent_requeue_does_not_consume_attempt(self):
        """Re-enqueue concorrente (CAS miss) devolve a tentativa; não marca FAILED."""
        job = PoolProjectionRecalc.objects.create(
            participant=self.participant,
            status=PoolProjectionRecalc.STATUS_PENDING,
            attempts=0,
        )

        def fake_sync(participant):
            # Simula render de página concorrente re-enfileirando o job enquanto
            # o cálculo corre: volta o status para PENDING, derrubando o CAS final.
            PoolProjectionRecalc.objects.filter(id=job.id).update(
                status=PoolProjectionRecalc.STATUS_PENDING,
            )
            return {}

        with (
            patch(
                "src.pool.services.projection_queue.sync_persisted_group_standings",
                side_effect=fake_sync,
            ),
            patch("src.pool.services.projection_queue.sync_persisted_third_places"),
        ):
            process_next_projection_recalc_job()

        job.refresh_from_db()
        self.assertEqual(job.status, PoolProjectionRecalc.STATUS_PENDING)
        self.assertEqual(job.attempts, 0, "tentativa não deveria ser consumida em re-enqueue concorrente")

    def test_pending_job_above_limit_is_marked_failed_and_skipped(self):
        job = PoolProjectionRecalc.objects.create(
            participant=self.participant,
            status=PoolProjectionRecalc.STATUS_PENDING,
            attempts=MAX_ATTEMPTS,
        )

        processed = process_next_projection_recalc_job()

        self.assertIsNone(processed)
        job.refresh_from_db()
        self.assertEqual(job.status, PoolProjectionRecalc.STATUS_FAILED)
        self.assertIn("Max retries reached", job.last_error)

    def test_successful_recalc_resets_attempts(self):
        """Recalc bem-sucedido deve zerar attempts. Sem isso, recalcs repetidos
        disparados pelo sync de fundo (que não zera attempts) acumulam tentativas
        ao longo de execuções que SEMPRE deram certo, e o job saudável vira FAILED
        ao bater MAX_ATTEMPTS na limpeza."""
        job = PoolProjectionRecalc.objects.create(
            participant=self.participant,
            status=PoolProjectionRecalc.STATUS_PENDING,
            attempts=MAX_ATTEMPTS - 1,
        )

        with (
            patch("src.pool.services.projection_queue.sync_persisted_group_standings", return_value={}),
            patch("src.pool.services.projection_queue.sync_persisted_third_places"),
        ):
            process_next_projection_recalc_job()

        job.refresh_from_db()
        self.assertEqual(job.status, PoolProjectionRecalc.STATUS_IDLE)
        self.assertEqual(job.attempts, 0, "sucesso deve refazer o orçamento de tentativas")


class SaveBetAjaxErrorMessageTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="ajaxerr", email="ajaxerr@example.com", password="123456Aa!")
        self.owner = User.objects.create_user(
            username="ajaxowner",
            email="ajaxowner@example.com",
            password="123456Aa!",
        )
        competition = Competition.objects.create(fifa_id=701, name="Copa Ajax")
        season = Season.objects.create(
            fifa_id=701,
            competition=competition,
            name="Temporada Ajax",
            year=2026,
            start_date="2026-06-01",
            end_date="2026-07-30",
        )
        stage = Stage.objects.create(fifa_id="GROUP701", season=season, name="Group Stage", order=1)
        group = Group.objects.create(fifa_id="GA701", stage=stage, name="A")
        self.home = Team.objects.create(fifa_id="H701", name="Home 701", name_norm="home701", code="H70", group=group)
        self.away = Team.objects.create(fifa_id="A701", name="Away 701", name_norm="away701", code="A70", group=group)

        future = timezone.now() + timezone.timedelta(days=1)
        self.match = Match.objects.create(
            fifa_id="M701",
            season=season,
            stage=stage,
            group=group,
            match_number=1,
            match_date_utc=future,
            match_date_local=future,
            match_date_brasilia=future,
            home_team=self.home,
            away_team=self.away,
        )

        self.pool = Pool.objects.create(
            name="Pool Ajax",
            slug="pool-ajax",
            season=season,
            created_by=self.owner,
            requires_payment=False,
        )
        PoolParticipant.objects.create(pool=self.pool, user=self.user, is_active=True)
        self.client.force_login(self.user)
        self.url = reverse("pool:save-bet", kwargs={"slug": self.pool.slug, "match_id": self.match.id})
        self.payload = {
            "home_score_pred": "1",
            "away_score_pred": "0",
            "winner_pred": "",
        }
        self.ajax_headers = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}

    @patch("src.pool.views.PoolBet.save", side_effect=ValidationError("Janela de palpites desta fase esta fechada."))
    def test_ajax_returns_specific_message_for_locked_phase(self, _mock_save):
        response = self.client.post(self.url, data=self.payload, **self.ajax_headers)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json().get("error"), "Fase de palpites fechada.")

    @patch("src.pool.views.PoolBet.save", side_effect=ValidationError("Informe o placar completo da partida."))
    def test_ajax_returns_specific_message_for_missing_scores(self, _mock_save):
        response = self.client.post(self.url, data=self.payload, **self.ajax_headers)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json().get("error"), "Preencha todos os campos.")

    @patch("src.pool.views.PoolBet.save", side_effect=RuntimeError("db timeout"))
    def test_ajax_returns_generic_message_for_unexpected_error(self, _mock_save):
        response = self.client.post(self.url, data=self.payload, **self.ajax_headers)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json().get("error"), "Erro interno, tente novamente.")


class ProjectedGroupStandingsH2HTest(TestCase):
    """
    Validates that head-to-head (H2H) tiebreaker is applied before FIFA world ranking.

    Scenario:
      - A vs B: 1-0 → A wins
      - A vs C: 1-2 → C wins
      - B vs C: 1-0 → B wins

    Global totals:
      A: 3pts, GD=0, GF=2   (tied with C)
      B: 3pts, GD=0, GF=1   (3rd — fewer goals)
      C: 3pts, GD=0, GF=2   (tied with A)

    H2H between {A, C}: C beat A 2-1 → C ranked above A.
    Without H2H, A (ranking=10) would incorrectly beat C (ranking=20).
    """

    def setUp(self):
        self.user = User.objects.create_user(username="h2huser", email="h2h@example.com", password="123456Aa!")
        self.competition = Competition.objects.create(fifa_id=500, name="Copa H2H")
        self.season = Season.objects.create(
            fifa_id=500,
            competition=self.competition,
            name="Temporada H2H",
            year=2026,
            start_date="2026-06-01",
            end_date="2026-07-30",
        )
        self.stage = Stage.objects.create(fifa_id="GROUP500", season=self.season, name="Group Stage", order=500)
        self.group = Group.objects.create(fifa_id="G-H2H", stage=self.stage, name="H")

        self.team_a = Team.objects.create(
            fifa_id="H2H-A", name="H2H Alpha", name_norm="h2h-alpha", code="H2A", world_ranking=10, group=self.group
        )
        self.team_b = Team.objects.create(
            fifa_id="H2H-B", name="H2H Beta", name_norm="h2h-beta", code="H2B", world_ranking=5, group=self.group
        )
        self.team_c = Team.objects.create(
            fifa_id="H2H-C", name="H2H Gamma", name_norm="h2h-gamma", code="H2C", world_ranking=20, group=self.group
        )

        now = timezone.now()
        self.match_ab = Match.objects.create(
            fifa_id="M-H2H-AB",
            season=self.season,
            stage=self.stage,
            group=self.group,
            match_number=501,
            match_date_utc=now,
            match_date_local=now,
            match_date_brasilia=now,
            home_team=self.team_a,
            away_team=self.team_b,
        )
        self.match_ac = Match.objects.create(
            fifa_id="M-H2H-AC",
            season=self.season,
            stage=self.stage,
            group=self.group,
            match_number=502,
            match_date_utc=now + timezone.timedelta(hours=1),
            match_date_local=now + timezone.timedelta(hours=1),
            match_date_brasilia=now + timezone.timedelta(hours=1),
            home_team=self.team_a,
            away_team=self.team_c,
        )
        self.match_bc = Match.objects.create(
            fifa_id="M-H2H-BC",
            season=self.season,
            stage=self.stage,
            group=self.group,
            match_number=503,
            match_date_utc=now + timezone.timedelta(hours=2),
            match_date_local=now + timezone.timedelta(hours=2),
            match_date_brasilia=now + timezone.timedelta(hours=2),
            home_team=self.team_b,
            away_team=self.team_c,
        )

        self.pool = Pool.objects.create(
            name="Pool H2H",
            slug="pool-h2h",
            season=self.season,
            created_by=self.user,
            requires_payment=False,
        )
        self.participant = PoolParticipant.objects.create(pool=self.pool, user=self.user, is_active=True)

        PoolBet.objects.create(participant=self.participant, match=self.match_ab, home_score_pred=1, away_score_pred=0)
        PoolBet.objects.create(participant=self.participant, match=self.match_ac, home_score_pred=1, away_score_pred=2)
        PoolBet.objects.create(participant=self.participant, match=self.match_bc, home_score_pred=1, away_score_pred=0)

    def test_h2h_resolves_globally_tied_teams(self):
        projected_groups = projected_group_standings(participant=self.participant, season=self.season)
        standings = projected_groups[0]["standings"]

        self.assertEqual(standings[0].team.id, self.team_c.id, "C beat A in H2H → C should be 1st")
        self.assertEqual(standings[1].team.id, self.team_a.id, "A lost to C in H2H → A should be 2nd")
        self.assertEqual(standings[2].team.id, self.team_b.id, "B has fewer global GF → B should be 3rd")

    def test_h2h_takes_priority_over_world_ranking(self):
        # A (ranking=10) has a better FIFA rank than C (ranking=20),
        # but C won H2H against A, so C must rank above A.
        projected_groups = projected_group_standings(participant=self.participant, season=self.season)
        standings = projected_groups[0]["standings"]

        pos_a = next(i for i, s in enumerate(standings) if s.team.id == self.team_a.id)
        pos_c = next(i for i, s in enumerate(standings) if s.team.id == self.team_c.id)
        self.assertLess(pos_c, pos_a, "C (worse ranking) should beat A (better ranking) because C won H2H")


class ProjectedGroupStandingsH2HCircularTest(TestCase):
    """
    Validates circular H2H fallback to world ranking.

    Scenario (circular): A beats B 2-1, B beats C 2-1, C beats A 2-1.
    All three teams end up with identical global stats AND identical H2H stats.
    Tiebreaker must fall back to FIFA world ranking: B(5) → A(10) → C(20).
    """

    def setUp(self):
        self.user = User.objects.create_user(username="circuser", email="circ@example.com", password="123456Aa!")
        self.competition = Competition.objects.create(fifa_id=600, name="Copa Circular")
        self.season = Season.objects.create(
            fifa_id=600,
            competition=self.competition,
            name="Temporada Circular",
            year=2026,
            start_date="2026-06-01",
            end_date="2026-07-30",
        )
        self.stage = Stage.objects.create(fifa_id="GROUP600", season=self.season, name="Group Stage", order=600)
        self.group = Group.objects.create(fifa_id="G-CIRC", stage=self.stage, name="Z")

        self.team_a = Team.objects.create(
            fifa_id="CR-A", name="Circ Alpha", name_norm="circ-alpha", code="CRA", world_ranking=10, group=self.group
        )
        self.team_b = Team.objects.create(
            fifa_id="CR-B", name="Circ Beta", name_norm="circ-beta", code="CRB", world_ranking=5, group=self.group
        )
        self.team_c = Team.objects.create(
            fifa_id="CR-C", name="Circ Gamma", name_norm="circ-gamma", code="CRC", world_ranking=20, group=self.group
        )

        now = timezone.now()
        self.match_ab = Match.objects.create(
            fifa_id="CM-AB",
            season=self.season,
            stage=self.stage,
            group=self.group,
            match_number=601,
            match_date_utc=now,
            match_date_local=now,
            match_date_brasilia=now,
            home_team=self.team_a,
            away_team=self.team_b,
        )
        self.match_bc = Match.objects.create(
            fifa_id="CM-BC",
            season=self.season,
            stage=self.stage,
            group=self.group,
            match_number=602,
            match_date_utc=now + timezone.timedelta(hours=1),
            match_date_local=now + timezone.timedelta(hours=1),
            match_date_brasilia=now + timezone.timedelta(hours=1),
            home_team=self.team_b,
            away_team=self.team_c,
        )
        self.match_ca = Match.objects.create(
            fifa_id="CM-CA",
            season=self.season,
            stage=self.stage,
            group=self.group,
            match_number=603,
            match_date_utc=now + timezone.timedelta(hours=2),
            match_date_local=now + timezone.timedelta(hours=2),
            match_date_brasilia=now + timezone.timedelta(hours=2),
            home_team=self.team_c,
            away_team=self.team_a,
        )

        self.pool = Pool.objects.create(
            name="Pool Circular",
            slug="pool-circular",
            season=self.season,
            created_by=self.user,
            requires_payment=False,
        )
        self.participant = PoolParticipant.objects.create(pool=self.pool, user=self.user, is_active=True)

        PoolBet.objects.create(participant=self.participant, match=self.match_ab, home_score_pred=2, away_score_pred=1)
        PoolBet.objects.create(participant=self.participant, match=self.match_bc, home_score_pred=2, away_score_pred=1)
        PoolBet.objects.create(participant=self.participant, match=self.match_ca, home_score_pred=2, away_score_pred=1)

    def test_circular_h2h_falls_back_to_world_ranking(self):
        projected_groups = projected_group_standings(participant=self.participant, season=self.season)
        standings = projected_groups[0]["standings"]

        self.assertEqual(standings[0].team.id, self.team_b.id, "B has best FIFA ranking (5) → 1st")
        self.assertEqual(standings[1].team.id, self.team_a.id, "A has mid FIFA ranking (10) → 2nd")
        self.assertEqual(standings[2].team.id, self.team_c.id, "C has worst FIFA ranking (20) → 3rd")


# Pure-function tests for scoring.py and rules.py (no database required)


class ScoringWinnerFromScoreTest(SimpleTestCase):
    """Unit tests for _winner_from_score pure function."""

    def test_home_wins(self):
        """Home score > away score returns 'HOME'."""
        self.assertEqual(_winner_from_score(2, 1), "HOME")

    def test_away_wins(self):
        """Away score > home score returns 'AWAY'."""
        self.assertEqual(_winner_from_score(0, 1), "AWAY")

    def test_draw(self):
        """Equal scores return 'DRAW'."""
        self.assertEqual(_winner_from_score(1, 1), "DRAW")

    def test_draw_zero_zero(self):
        """0-0 draw returns 'DRAW'."""
        self.assertEqual(_winner_from_score(0, 0), "DRAW")


class ScoringCalculateBetPointsTest(SimpleTestCase):
    def _make_scoring_config(self, **overrides):
        defaults = dict(
            group_exact_score=25,
            group_winner_and_winner_goals=18,
            group_winner_and_diff=15,
            group_winner_and_loser_goals=12,
            group_winner_only=10,
            knockout_exact_and_advancing=35,
            knockout_advancing_and_winner_goals=25,
            knockout_advancing_and_diff=20,
            knockout_advancing_and_loser_goals=17,
            knockout_advancing_only=15,
            knockout_exact_wrong_advancing=10,
            knockout_draw_prediction_points=20,
        )
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    def _make_group_bet(self, home_pred, away_pred, home_real, away_real, is_active=True):
        stage = SimpleNamespace(name="Group Stage")
        match = SimpleNamespace(
            stage=stage,
            home_score=home_real,
            away_score=away_real,
            winner_id=None,
        )
        return SimpleNamespace(
            is_active=is_active,
            home_score_pred=home_pred,
            away_score_pred=away_pred,
            winner_pred_id=None,
            match=match,
        )

    def _make_knockout_bet(
        self,
        home_pred,
        away_pred,
        home_real,
        away_real,
        winner_real_id=None,
        winner_pred_id=None,
        is_active=True,
        home_team_id=1,
    ):
        stage = SimpleNamespace(name="Semi-Final")
        match = SimpleNamespace(
            stage=stage,
            home_score=home_real,
            away_score=away_real,
            winner_id=winner_real_id,
            home_team_id=home_team_id,
            away_team_id=2,
        )
        return SimpleNamespace(
            is_active=is_active,
            home_score_pred=home_pred,
            away_score_pred=away_pred,
            winner_pred_id=winner_pred_id,
            match=match,
        )

    def test_inactive_bet(self):
        bet = self._make_group_bet(2, 1, 2, 1, is_active=False)
        result = calculate_bet_points(bet, self._make_scoring_config())
        self.assertEqual(result["points"], 0)
        self.assertFalse(result["exact_score"])
        self.assertFalse(result["advancing_correct"])

    def test_no_home_pred(self):
        bet = self._make_group_bet(None, 1, 2, 1)
        result = calculate_bet_points(bet, self._make_scoring_config())
        self.assertEqual(result["points"], 0)
        self.assertFalse(result["exact_score"])
        self.assertFalse(result["advancing_correct"])

    def test_no_match_score(self):
        bet = self._make_group_bet(2, 1, None, 1)
        result = calculate_bet_points(bet, self._make_scoring_config())
        self.assertEqual(result["points"], 0)
        self.assertFalse(result["exact_score"])
        self.assertFalse(result["advancing_correct"])

    def test_group_exact_score(self):
        bet = self._make_group_bet(2, 1, 2, 1)
        result = calculate_bet_points(bet, self._make_scoring_config())
        self.assertEqual(result["points"], 25)
        self.assertTrue(result["exact_score"])
        self.assertTrue(result["advancing_correct"])

    def test_group_winner_and_winner_goals(self):
        bet = self._make_group_bet(2, 0, 2, 1)
        result = calculate_bet_points(bet, self._make_scoring_config())
        self.assertEqual(result["points"], 18)
        self.assertFalse(result["exact_score"])
        self.assertTrue(result["advancing_correct"])
        self.assertTrue(result["advancing_goals_correct"])
        self.assertFalse(result["diff_correct"])

    def test_group_winner_and_diff(self):
        bet = self._make_group_bet(3, 2, 2, 1)
        result = calculate_bet_points(bet, self._make_scoring_config())
        self.assertEqual(result["points"], 15)
        self.assertTrue(result["advancing_correct"])
        self.assertTrue(result["diff_correct"])
        self.assertFalse(result["advancing_goals_correct"])

    def test_group_winner_and_loser_goals(self):
        bet = self._make_group_bet(3, 1, 2, 1)
        result = calculate_bet_points(bet, self._make_scoring_config())
        self.assertEqual(result["points"], 12)
        self.assertTrue(result["advancing_correct"])
        self.assertTrue(result["eliminated_goals_correct"])

    def test_group_winner_only(self):
        bet = self._make_group_bet(1, 0, 2, 1)
        result = calculate_bet_points(bet, self._make_scoring_config())
        self.assertEqual(result["points"], 15)
        self.assertTrue(result["advancing_correct"])
        self.assertTrue(result["diff_correct"])

    def test_group_wrong_winner(self):
        bet = self._make_group_bet(0, 2, 2, 1)
        result = calculate_bet_points(bet, self._make_scoring_config())
        self.assertEqual(result["points"], 0)
        self.assertFalse(result["advancing_correct"])

    def test_group_draw_wrong_winner(self):
        bet = self._make_group_bet(1, 1, 2, 1)
        result = calculate_bet_points(bet, self._make_scoring_config())
        self.assertEqual(result["points"], 0)
        self.assertFalse(result["advancing_correct"])

    def test_group_draw_exact(self):
        bet = self._make_group_bet(1, 1, 1, 1)
        result = calculate_bet_points(bet, self._make_scoring_config())
        self.assertEqual(result["points"], 25)
        self.assertTrue(result["exact_score"])
        self.assertTrue(result["advancing_correct"])

    def test_group_draw_diff_correct(self):
        bet = self._make_group_bet(2, 2, 1, 1)
        result = calculate_bet_points(bet, self._make_scoring_config())
        self.assertEqual(result["points"], 15)
        self.assertTrue(result["advancing_correct"])
        self.assertTrue(result["diff_correct"])

    def test_group_draw_winner_only_impossible(self):
        bet = self._make_group_bet(2, 1, 1, 1)
        result = calculate_bet_points(bet, self._make_scoring_config())
        self.assertEqual(result["points"], 0)
        self.assertFalse(result["advancing_correct"])

    def test_knockout_exact_and_advancing(self):
        bet = self._make_knockout_bet(2, 1, 2, 1, winner_real_id=1, winner_pred_id=1)
        result = calculate_bet_points(bet, self._make_scoring_config())
        self.assertEqual(result["points"], 35)
        self.assertTrue(result["exact_score"])
        self.assertTrue(result["advancing_correct"])

    def test_knockout_advancing_and_winner_goals(self):
        bet = self._make_knockout_bet(2, 0, 2, 1, winner_real_id=1, winner_pred_id=1)
        result = calculate_bet_points(bet, self._make_scoring_config())
        self.assertEqual(result["points"], 25)
        self.assertTrue(result["advancing_correct"])
        self.assertTrue(result["advancing_goals_correct"])

    def test_knockout_advancing_and_diff(self):
        bet = self._make_knockout_bet(3, 2, 2, 1, winner_real_id=1, winner_pred_id=1)
        result = calculate_bet_points(bet, self._make_scoring_config())
        self.assertEqual(result["points"], 20)
        self.assertTrue(result["advancing_correct"])
        self.assertTrue(result["diff_correct"])

    def test_knockout_advancing_and_loser_goals(self):
        bet = self._make_knockout_bet(3, 1, 2, 1, winner_real_id=1, winner_pred_id=1)
        result = calculate_bet_points(bet, self._make_scoring_config())
        self.assertEqual(result["points"], 17)
        self.assertTrue(result["advancing_correct"])
        self.assertTrue(result["eliminated_goals_correct"])

    def test_knockout_advancing_only(self):
        bet = self._make_knockout_bet(1, 0, 3, 1, winner_real_id=1, winner_pred_id=1)
        result = calculate_bet_points(bet, self._make_scoring_config())
        self.assertEqual(result["points"], 15)
        self.assertTrue(result["advancing_correct"])
        self.assertFalse(result["advancing_goals_correct"])
        self.assertFalse(result["diff_correct"])
        self.assertFalse(result["eliminated_goals_correct"])

    def test_knockout_wrong_advancing(self):
        bet = self._make_knockout_bet(0, 2, 2, 1, winner_real_id=1, winner_pred_id=2)
        result = calculate_bet_points(bet, self._make_scoring_config())
        self.assertEqual(result["points"], 0)
        self.assertFalse(result["advancing_correct"])

    def test_tipo2_knockout_exact_score_wrong_advancing_zero(self):
        # Tipo 2: placar exato não salva se o classificado estiver errado.
        bet = self._make_knockout_bet(2, 1, 2, 1, winner_real_id=1, winner_pred_id=2)
        result = calculate_bet_points(
            bet, self._make_scoring_config(), pool_type=POOL_TYPE_2, predicted_advancing_id=2
        )
        self.assertEqual(result["points"], 0)
        self.assertTrue(result["exact_score"])
        self.assertFalse(result["advancing_correct"])

    def test_knockout_draw_exact_score_correct_advancing(self):
        # Palpite 1-1, jogo real 1-1 com HOME avancando por penaltis, winner_pred = HOME.
        # Placar exato + avanço correto = knockout_exact_and_advancing (35).
        bet = self._make_knockout_bet(1, 1, 1, 1, winner_real_id=1, winner_pred_id=1)
        result = calculate_bet_points(bet, self._make_scoring_config())
        self.assertEqual(result["points"], 35)
        self.assertTrue(result["exact_score"])
        self.assertTrue(result["advancing_correct"])

    def test_knockout_draw_exact_score_wrong_advancing(self):
        # Palpite 1-1, jogo real 1-1 HOME via penaltis, winner_pred = AWAY.
        # Placar exato sempre vale knockout_exact_and_advancing (35), independente dos penaltis.
        bet = self._make_knockout_bet(1, 1, 1, 1, winner_real_id=1, winner_pred_id=2)
        result = calculate_bet_points(bet, self._make_scoring_config())
        self.assertEqual(result["points"], 35)
        self.assertTrue(result["exact_score"])
        self.assertFalse(result["advancing_correct"])

    def test_knockout_draw_non_exact_correct_advancing(self):
        # Palpite 0-0, jogo real 1-1 com HOME avancando por penaltis, winner_pred = HOME.
        # Acertou empate mas placar errado = knockout_draw_prediction_points (20).
        bet = self._make_knockout_bet(0, 0, 1, 1, winner_real_id=1, winner_pred_id=1)
        result = calculate_bet_points(bet, self._make_scoring_config())
        self.assertEqual(result["points"], 20)
        self.assertFalse(result["exact_score"])
        self.assertTrue(result["advancing_correct"])

    def test_knockout_draw_prediction_real_non_draw_zero(self):
        # Palpite empate (2-2), jogo real HOME ganha 1-0 (sem empate no regulamentar):
        # 0 pts. Pontos fixos do palpite-empate so pagam quando o real tambem empata.
        bet = self._make_knockout_bet(2, 2, 1, 0, winner_real_id=1, winner_pred_id=1)
        result = calculate_bet_points(bet, self._make_scoring_config())
        self.assertEqual(result["points"], 0)
        self.assertFalse(result["exact_score"])

    def test_knockout_non_draw_bet_real_draw_pen_decided(self):
        # Palpite 2-1 (HOME wins), jogo real 1-1 com HOME avancando por penaltis.
        # Scoring considera o placar do tempo regulamentar: real eh empate,
        # palpite nao-empate erra o resultado -> 0 pts.
        bet = self._make_knockout_bet(2, 1, 1, 1, winner_real_id=1, winner_pred_id=1)
        result = calculate_bet_points(bet, self._make_scoring_config())
        self.assertEqual(result["points"], 0)
        self.assertFalse(result["advancing_correct"])
        self.assertFalse(result["advancing_goals_correct"])
        self.assertFalse(result["diff_correct"])
        self.assertFalse(result["eliminated_goals_correct"])

    def test_knockout_non_draw_wrong_winner_real_draw(self):
        # Palpite 0-2 (AWAY wins por posicao), jogo real 1-1 HOME por penaltis.
        # Vencedor posicional errado -> 0 pts.
        bet = self._make_knockout_bet(0, 2, 1, 1, winner_real_id=1, winner_pred_id=2)
        result = calculate_bet_points(bet, self._make_scoring_config())
        self.assertEqual(result["points"], 0)
        self.assertFalse(result["advancing_correct"])

    def test_tipo1_penalty_decision_non_draw_pick_real_draw_zero(self):
        # Real match 1-1 in regulation, HOME advances on penalties. User predicts
        # 2-1 (HOME wins). Regulation result is a draw, so non-draw guess -> 0 pts.
        # Team-advancement bonus (Tipo 1) is awarded separately, not here.
        bet = self._make_knockout_bet(2, 1, 1, 1, winner_real_id=1, winner_pred_id=1)
        result = calculate_bet_points(bet, self._make_scoring_config())
        self.assertEqual(result["points"], 0)
        self.assertFalse(result["advancing_correct"])

    def test_winner_pred_ignored_tipo1_but_gates_tipo2(self):
        # Tipo 1 (posicional): winner_pred irrelevante -> 25.
        bet_t1 = self._make_knockout_bet(2, 0, 2, 1, winner_real_id=1, winner_pred_id=2)
        r1 = calculate_bet_points(bet_t1, self._make_scoring_config())
        self.assertEqual(r1["points"], 25)
        self.assertTrue(r1["advancing_correct"])
        # Tipo 2: classificado projetado (2) != real (1) -> 0.
        bet_t2 = self._make_knockout_bet(2, 0, 2, 1, winner_real_id=1, winner_pred_id=2)
        r2 = calculate_bet_points(bet_t2, self._make_scoring_config(), pool_type=POOL_TYPE_2, predicted_advancing_id=2)
        self.assertEqual(r2["points"], 0)
        self.assertFalse(r2["advancing_correct"])

    # --- Tipo 2 mata-mata: gate por classificado ---

    def test_tipo2_ex1_advancing_loser_goals(self):
        # real Brasil(1) 2x1 Holanda(2), palpite 3x1, classificado certo (1).
        bet = self._make_knockout_bet(3, 1, 2, 1, winner_real_id=1, winner_pred_id=1)
        result = calculate_bet_points(
            bet, self._make_scoring_config(), pool_type=POOL_TYPE_2, predicted_advancing_id=1
        )
        self.assertEqual(result["points"], 17)
        self.assertTrue(result["advancing_correct"])
        self.assertTrue(result["eliminated_goals_correct"])
        self.assertFalse(result["exact_score"])

    def test_tipo2_ex2_wrong_advancing_zero(self):
        # palpite 0x1 -> classificado palpitado = Holanda(2); real = Brasil(1).
        bet = self._make_knockout_bet(0, 1, 2, 1, winner_real_id=1, winner_pred_id=2)
        result = calculate_bet_points(
            bet, self._make_scoring_config(), pool_type=POOL_TYPE_2, predicted_advancing_id=2
        )
        self.assertEqual(result["points"], 0)
        self.assertFalse(result["advancing_correct"])

    def test_tipo2_ex3_exact_score_with_different_loser(self):
        # palpite 2x1 (eliminado projetado != real), classificado certo (1).
        bet = self._make_knockout_bet(2, 1, 2, 1, winner_real_id=1, winner_pred_id=1)
        result = calculate_bet_points(
            bet, self._make_scoring_config(), pool_type=POOL_TYPE_2, predicted_advancing_id=1
        )
        self.assertEqual(result["points"], 35)
        self.assertTrue(result["exact_score"])
        self.assertTrue(result["advancing_correct"])

    def test_tipo2_ex4_exact_score_wrong_advancing_zero(self):
        # placar exato 2x1, mas classificado palpitado = Marrocos(3) != real Brasil(1).
        bet = self._make_knockout_bet(2, 1, 2, 1, winner_real_id=1, winner_pred_id=3)
        result = calculate_bet_points(
            bet, self._make_scoring_config(), pool_type=POOL_TYPE_2, predicted_advancing_id=3
        )
        self.assertEqual(result["points"], 0)
        self.assertFalse(result["advancing_correct"])

    def test_tipo2_ex5_draw_pred_correct_advancing_only(self):
        # palpite 0x0 + Brasil(1) classifica; real 2x1 Brasil. Só classificado.
        bet = self._make_knockout_bet(0, 0, 2, 1, winner_real_id=1, winner_pred_id=1)
        result = calculate_bet_points(
            bet, self._make_scoring_config(), pool_type=POOL_TYPE_2, predicted_advancing_id=1
        )
        self.assertEqual(result["points"], 15)
        self.assertTrue(result["advancing_correct"])
        self.assertFalse(result["exact_score"])

    def test_tipo2_draw_pred_real_non_draw_winner_goals_coincide_only_classified(self):
        # Real África(1) 0 x 1 Canadá(2): away vence. Palpite 1x1 (EMPATE),
        # classificado Canadá(2) certo. O gol do visitante coincide (1 == 1),
        # mas o palpite foi empate e o jogo não foi empate -> errou o placar.
        # Deve pagar só classificado (advancing_only=15), nunca gols do vencedor.
        bet = self._make_knockout_bet(1, 1, 0, 1, winner_real_id=2, winner_pred_id=2)
        result = calculate_bet_points(
            bet, self._make_scoring_config(), pool_type=POOL_TYPE_2, predicted_advancing_id=2
        )
        self.assertEqual(result["points"], 15)
        self.assertTrue(result["advancing_correct"])
        self.assertFalse(result["advancing_goals_correct"])
        self.assertFalse(result["exact_score"])

    def test_tipo2_wrong_winner_pred_real_non_draw_only_classified(self):
        # Real 0 x 1 (away vence), palpite 2x1 (palpitou MANDANTE vencendo),
        # classificado (away, id=2) certo. Direção do palpite errada, mas gol do
        # visitante coincide (1 == 1). Deve pagar só classificado (15).
        bet = self._make_knockout_bet(2, 1, 0, 1, winner_real_id=2, winner_pred_id=2)
        result = calculate_bet_points(
            bet, self._make_scoring_config(), pool_type=POOL_TYPE_2, predicted_advancing_id=2
        )
        self.assertEqual(result["points"], 15)
        self.assertTrue(result["advancing_correct"])
        self.assertFalse(result["advancing_goals_correct"])

    def test_tipo2_real_draw_exact(self):
        # real 1x1 (pênaltis, Brasil avança), palpite 1x1, classificado certo.
        bet = self._make_knockout_bet(1, 1, 1, 1, winner_real_id=1, winner_pred_id=1)
        result = calculate_bet_points(
            bet, self._make_scoring_config(), pool_type=POOL_TYPE_2, predicted_advancing_id=1
        )
        self.assertEqual(result["points"], 35)
        self.assertTrue(result["exact_score"])

    def test_tipo2_real_draw_same_diff(self):
        # real 1x1, palpite 0x0 (mesma diferença 0), classificado certo.
        bet = self._make_knockout_bet(0, 0, 1, 1, winner_real_id=1, winner_pred_id=1)
        result = calculate_bet_points(
            bet, self._make_scoring_config(), pool_type=POOL_TYPE_2, predicted_advancing_id=1
        )
        self.assertEqual(result["points"], 20)
        self.assertTrue(result["diff_correct"])

    def test_tipo2_real_draw_non_draw_pred_advancing_only(self):
        # real 1x1 (pênaltis), palpite 2x1 (não-empate), classificado certo.
        bet = self._make_knockout_bet(2, 1, 1, 1, winner_real_id=1, winner_pred_id=1)
        result = calculate_bet_points(
            bet, self._make_scoring_config(), pool_type=POOL_TYPE_2, predicted_advancing_id=1
        )
        self.assertEqual(result["points"], 15)
        self.assertTrue(result["advancing_correct"])

    def test_tipo2_no_winner_yet_zero(self):
        # match.winner_id ausente (jogo não decidido) -> 0.
        bet = self._make_knockout_bet(2, 1, 2, 1, winner_real_id=None, winner_pred_id=1)
        result = calculate_bet_points(
            bet, self._make_scoring_config(), pool_type=POOL_TYPE_2, predicted_advancing_id=1
        )
        self.assertEqual(result["points"], 0)
        self.assertFalse(result["advancing_correct"])

    def _make_phase_scoring(self):
        from src.pool.models import KNOCKOUT_PHASE_DEFAULTS

        return {key: SimpleNamespace(**values) for key, values in KNOCKOUT_PHASE_DEFAULTS.items()}

    def _make_knockout_bet_phase(
        self,
        home_pred,
        away_pred,
        home_real,
        away_real,
        *,
        stage_name,
        winner_real_id=None,
        winner_pred_id=None,
        home_team_id=1,
    ):
        stage = SimpleNamespace(name=stage_name)
        match = SimpleNamespace(
            stage=stage,
            home_score=home_real,
            away_score=away_real,
            winner_id=winner_real_id,
            home_team_id=home_team_id,
            away_team_id=2,
        )
        return SimpleNamespace(
            is_active=True,
            home_score_pred=home_pred,
            away_score_pred=away_pred,
            winner_pred_id=winner_pred_id,
            match=match,
        )

    def test_tipo2_final_exact_uses_final_tier(self):
        from src.pool.services.rules import POOL_TYPE_2

        bet = self._make_knockout_bet_phase(2, 1, 2, 1, stage_name="Final", winner_real_id=1)
        result = calculate_bet_points(
            bet,
            self._make_scoring_config(),
            pool_type=POOL_TYPE_2,
            predicted_advancing_id=1,
            knockout_phase_scoring=self._make_phase_scoring(),
        )
        self.assertEqual(result["points"], 95)
        self.assertTrue(result["exact_score"])
        self.assertTrue(result["advancing_correct"])

    def test_tipo2_r32_exact_uses_r32_tier(self):
        from src.pool.services.rules import POOL_TYPE_2

        bet = self._make_knockout_bet_phase(2, 1, 2, 1, stage_name="R32", winner_real_id=1)
        result = calculate_bet_points(
            bet,
            self._make_scoring_config(),
            pool_type=POOL_TYPE_2,
            predicted_advancing_id=1,
            knockout_phase_scoring=self._make_phase_scoring(),
        )
        self.assertEqual(result["points"], 40)

    def test_tipo2_final_scores_more_than_r32_same_guess(self):
        from src.pool.services.rules import POOL_TYPE_2

        # Pred: home wins 2-0. Real: home wins 2-1 (same winner, winner goals match, not exact).
        # → advancing_goals tier applies.
        phases = self._make_phase_scoring()
        cfg = self._make_scoring_config()
        final_bet = self._make_knockout_bet_phase(2, 0, 2, 1, stage_name="Final", winner_real_id=1)
        r32_bet = self._make_knockout_bet_phase(2, 0, 2, 1, stage_name="R32", winner_real_id=1)
        final_pts = calculate_bet_points(
            final_bet,
            cfg,
            pool_type=POOL_TYPE_2,
            predicted_advancing_id=1,
            knockout_phase_scoring=phases,
        )["points"]
        r32_pts = calculate_bet_points(
            r32_bet,
            cfg,
            pool_type=POOL_TYPE_2,
            predicted_advancing_id=1,
            knockout_phase_scoring=phases,
        )["points"]
        self.assertEqual(final_pts, 72)  # FINAL advancing_goals
        self.assertEqual(r32_pts, 30)  # R32 advancing_goals
        self.assertGreater(final_pts, r32_pts)

    def test_tipo2_wrong_classified_zero_even_in_final(self):
        from src.pool.services.rules import POOL_TYPE_2

        bet = self._make_knockout_bet_phase(2, 1, 2, 1, stage_name="Final", winner_real_id=2)
        result = calculate_bet_points(
            bet,
            self._make_scoring_config(),
            pool_type=POOL_TYPE_2,
            predicted_advancing_id=1,
            knockout_phase_scoring=self._make_phase_scoring(),
        )
        self.assertEqual(result["points"], 0)
        self.assertFalse(result["advancing_correct"])

    def test_tipo2_example_wrong_opponent_right_classified_scores_full(self):
        # Real Marrocos(1) x Holanda(2): away advances. Palpite Brasil x Holanda 1x2.
        # Classificado (away, id=2) == real winner (id=2) → exato da fase (QF).
        from src.pool.services.rules import POOL_TYPE_2

        bet = self._make_knockout_bet_phase(
            1,
            2,
            1,
            2,
            stage_name="Quartas",
            winner_real_id=2,
        )
        result = calculate_bet_points(
            bet,
            self._make_scoring_config(),
            pool_type=POOL_TYPE_2,
            predicted_advancing_id=2,
            knockout_phase_scoring=self._make_phase_scoring(),
        )
        self.assertEqual(result["points"], 62)  # QF exact
        self.assertTrue(result["exact_score"])

    def test_tipo2_fallback_to_flat_when_no_phase_map(self):
        # Sem knockout_phase_scoring → usa campos flat (retrocompatível).
        from src.pool.services.rules import POOL_TYPE_2

        bet = self._make_knockout_bet_phase(2, 1, 2, 1, stage_name="Final", winner_real_id=1)
        result = calculate_bet_points(
            bet,
            self._make_scoring_config(),
            pool_type=POOL_TYPE_2,
            predicted_advancing_id=1,
        )
        self.assertEqual(result["points"], 35)  # knockout_exact_and_advancing flat

    def test_tipo2_third_place_exact_uses_third_tier(self):
        # "Terceiro Lugar" → normalize_stage_key → "THIRD" (via "TERCE" + "LUGAR").
        # THIRD é não-monotônico: exact=55, entre R16(50) e QF(62).
        from src.pool.services.rules import POOL_TYPE_2

        bet = self._make_knockout_bet_phase(2, 1, 2, 1, stage_name="Terceiro Lugar", winner_real_id=1)
        result = calculate_bet_points(
            bet,
            self._make_scoring_config(),
            pool_type=POOL_TYPE_2,
            predicted_advancing_id=1,
            knockout_phase_scoring=self._make_phase_scoring(),
        )
        self.assertEqual(result["points"], 55)  # THIRD exact
        self.assertTrue(result["exact_score"])


class NormalizeStageKeyTest(SimpleTestCase):
    """Unit tests for normalize_stage_key pure function."""

    def _stage(self, name):
        """Helper to create a stage object."""
        return SimpleNamespace(name=name)

    def test_stage_none(self):
        """None stage returns empty string."""
        self.assertEqual(normalize_stage_key(None), "")

    def test_stage_name_none(self):
        """Stage with name=None returns empty string."""
        self.assertEqual(normalize_stage_key(self._stage(None)), "")

    def test_stage_name_empty(self):
        """Stage with empty name returns empty string."""
        self.assertEqual(normalize_stage_key(self._stage("")), "")

    def test_group_stage_en(self):
        """'Group Stage' (English) returns 'GROUP'."""
        self.assertEqual(normalize_stage_key(self._stage("Group Stage")), "GROUP")

    def test_grupo_pt(self):
        """'Grupo A' (Portuguese) returns 'GROUP'."""
        self.assertEqual(normalize_stage_key(self._stage("Grupo A")), "GROUP")

    def test_primeira_fase_pt(self):
        """'Primeira Fase' (Portuguese) returns 'GROUP'."""
        self.assertEqual(normalize_stage_key(self._stage("Primeira Fase")), "GROUP")

    def test_r16_en(self):
        """'Round of 16' (English) returns 'R16'."""
        self.assertEqual(normalize_stage_key(self._stage("Round of 16")), "R16")

    def test_r16_pt(self):
        """'Oitavas de Final' (Portuguese) returns 'R16'."""
        self.assertEqual(normalize_stage_key(self._stage("Oitavas de Final")), "R16")

    def test_qf_en(self):
        """'Quarter-Final' (English) returns 'QF'."""
        self.assertEqual(normalize_stage_key(self._stage("Quarter-Final")), "QF")

    def test_qf_pt(self):
        """'Quartas de Final' (Portuguese) returns 'QF'."""
        self.assertEqual(normalize_stage_key(self._stage("Quartas de Final")), "QF")

    def test_sf_en(self):
        """'Semi-Final' (English) returns 'SF'."""
        self.assertEqual(normalize_stage_key(self._stage("Semi-Final")), "SF")

    def test_sf_pt(self):
        """'Semifinal' (Portuguese) returns 'SF'."""
        self.assertEqual(normalize_stage_key(self._stage("Semifinal")), "SF")

    def test_third_decisao(self):
        """'Decisão 3o Lugar' (Portuguese) returns 'THIRD'."""
        self.assertEqual(normalize_stage_key(self._stage("Decisão 3o Lugar")), "THIRD")

    def test_third_terceiro(self):
        """'Terceiro Lugar' (Portuguese) returns 'THIRD'."""
        self.assertEqual(normalize_stage_key(self._stage("Terceiro Lugar")), "THIRD")

    def test_final_exact(self):
        """'Final' (exact match) returns 'FINAL'."""
        self.assertEqual(normalize_stage_key(self._stage("Final")), "FINAL")

    def test_final_grand(self):
        """'Grand Final' returns 'FINAL'."""
        self.assertEqual(normalize_stage_key(self._stage("Grand Final")), "FINAL")

    def test_unknown(self):
        """Unknown stage returns empty string."""
        self.assertEqual(normalize_stage_key(self._stage("Mystery Stage")), "")


class PhaseForMatchTest(SimpleTestCase):
    """Unit tests for phase_for_match pure function."""

    def _match(self, stage_name):
        """Helper to create a match object."""
        stage = SimpleNamespace(name=stage_name) if stage_name is not None else None
        return SimpleNamespace(stage=stage)

    def test_group_stage(self):
        """Match with Group Stage returns PHASE_GROUP."""
        match = self._match("Group Stage")
        self.assertEqual(phase_for_match(match), PHASE_GROUP)

    def test_knockout_stage(self):
        """Match with Semi-Final returns PHASE_KNOCKOUT."""
        match = self._match("Semi-Final")
        self.assertEqual(phase_for_match(match), PHASE_KNOCKOUT)

    def test_none_stage(self):
        """Match with None stage returns PHASE_KNOCKOUT (fallback)."""
        match = self._match(None)
        self.assertEqual(phase_for_match(match), PHASE_KNOCKOUT)


class ContextBuilderPureHelpersTest(SimpleTestCase):
    """Unit tests for pure helper functions from context_builder.py."""

    def _team(self, id):
        """Create a mock team with given ID."""
        return SimpleNamespace(id=id)

    def _bet(
        self,
        *,
        is_active=True,
        winner_pred_id=None,
        winner_pred=None,
        home_score_pred=None,
        away_score_pred=None,
        updated_at=None,
        match=None,
    ):
        """Create a mock bet."""
        return SimpleNamespace(
            is_active=is_active,
            winner_pred_id=winner_pred_id,
            winner_pred=winner_pred,
            home_score_pred=home_score_pred,
            away_score_pred=away_score_pred,
            updated_at=updated_at,
            match=match,
        )

    def _match_obj(
        self,
        *,
        id=1,
        match_number=1,
        winner_id=None,
        winner=None,
        home_team=None,
        away_team=None,
        home_team_id=None,
        away_team_id=None,
        group_id=None,
    ):
        """Create a mock match."""
        return SimpleNamespace(
            id=id,
            match_number=match_number,
            winner_id=winner_id,
            winner=winner,
            home_team=home_team,
            away_team=away_team,
            home_team_id=home_team_id,
            away_team_id=away_team_id,
            group_id=group_id,
        )

    # _make_pairs tests
    def test_make_pairs_empty(self):
        """Empty list returns empty list."""
        self.assertEqual(_make_pairs([]), [])

    def test_make_pairs_one(self):
        """List with 1 element returns [[element]]."""
        self.assertEqual(_make_pairs([1]), [[1]])

    def test_make_pairs_two(self):
        """List with 2 elements returns [[a, b]]."""
        self.assertEqual(_make_pairs([1, 2]), [[1, 2]])

    def test_make_pairs_three(self):
        """List with 3 elements returns [[a, b], [c]]."""
        self.assertEqual(_make_pairs([1, 2, 3]), [[1, 2], [3]])

    def test_make_pairs_four(self):
        """List with 4 elements returns [[a, b], [c, d]]."""
        self.assertEqual(_make_pairs([1, 2, 3, 4]), [[1, 2], [3, 4]])

    # _cb_normalize_stage_key tests (local version in context_builder)
    def test_cb_normalize_none(self):
        """stage=None returns empty string."""
        self.assertEqual(_cb_normalize_stage_key(None), "")

    def test_cb_normalize_sf_en(self):
        """'Semi-Final' returns 'SF'."""
        stage = SimpleNamespace(name="Semi-Final")
        self.assertEqual(_cb_normalize_stage_key(stage), "SF")

    def test_cb_normalize_sf_pt(self):
        """'Semifinal' returns 'SF'."""
        stage = SimpleNamespace(name="Semifinal")
        self.assertEqual(_cb_normalize_stage_key(stage), "SF")

    def test_cb_normalize_qf_en(self):
        """'Quarter-Final' returns 'QF'."""
        stage = SimpleNamespace(name="Quarter-Final")
        self.assertEqual(_cb_normalize_stage_key(stage), "QF")

    def test_cb_normalize_r16_en(self):
        """'Round of 16' returns 'R16'."""
        stage = SimpleNamespace(name="Round of 16")
        self.assertEqual(_cb_normalize_stage_key(stage), "R16")

    def test_cb_normalize_r16_pt(self):
        """'Oitavas de Final' returns 'R16'."""
        stage = SimpleNamespace(name="Oitavas de Final")
        self.assertEqual(_cb_normalize_stage_key(stage), "R16")

    def test_cb_normalize_final(self):
        """'Final' returns 'FINAL'."""
        stage = SimpleNamespace(name="Final")
        self.assertEqual(_cb_normalize_stage_key(stage), "FINAL")

    def test_cb_normalize_third(self):
        """'Decisão 3o Lugar' returns 'THIRD'."""
        stage = SimpleNamespace(name="Decisão 3o Lugar")
        self.assertEqual(_cb_normalize_stage_key(stage), "THIRD")

    def test_cb_normalize_unknown(self):
        """'Mystery Stage' returns empty string."""
        stage = SimpleNamespace(name="Mystery Stage")
        self.assertEqual(_cb_normalize_stage_key(stage), "")

    # _infer_advancing_team tests
    def test_infer_match_has_winner_no_bet(self):
        """match.winner_id set but no bet → None (real winner not used; scoring is separate)."""
        winner_team = self._team(100)
        match = self._match_obj(winner_id=100, winner=winner_team)
        result = _infer_advancing_team(match, None, None, None)
        self.assertIsNone(result)

    def test_infer_no_bet(self):
        """match.winner_id None, bet=None → None."""
        match = self._match_obj(winner_id=None)
        result = _infer_advancing_team(match, None, None, None)
        self.assertIsNone(result)

    def test_infer_inactive_bet(self):
        """match.winner_id None, bet.is_active=False → None."""
        match = self._match_obj(winner_id=None)
        bet = self._bet(is_active=False)
        result = _infer_advancing_team(match, bet, None, None)
        self.assertIsNone(result)

    def test_infer_bet_has_winner_pred(self):
        """match.winner_id None, bet.is_active=True, bet.winner_pred_id set → returns bet.winner_pred."""
        winner_pred = self._team(50)
        match = self._match_obj(winner_id=None)
        bet = self._bet(is_active=True, winner_pred_id=50, winner_pred=winner_pred)
        result = _infer_advancing_team(match, bet, None, None)
        self.assertIs(result, winner_pred)

    def test_infer_bet_home_pred_higher(self):
        """match.winner_id None, bet active, home_pred=2 > away_pred=1 → returns home_team."""
        home_team = self._team(1)
        away_team = self._team(2)
        match = self._match_obj(winner_id=None)
        bet = self._bet(is_active=True, home_score_pred=2, away_score_pred=1)
        result = _infer_advancing_team(match, bet, home_team, away_team)
        self.assertIs(result, home_team)

    def test_infer_bet_away_pred_higher(self):
        """match.winner_id None, bet active, away_pred=2 > home_pred=1 → returns away_team."""
        home_team = self._team(1)
        away_team = self._team(2)
        match = self._match_obj(winner_id=None)
        bet = self._bet(is_active=True, home_score_pred=1, away_score_pred=2)
        result = _infer_advancing_team(match, bet, home_team, away_team)
        self.assertIs(result, away_team)

    def test_infer_draw_pred(self):
        """match.winner_id None, bet active, equal scores → None."""
        home_team = self._team(1)
        away_team = self._team(2)
        match = self._match_obj(winner_id=None)
        bet = self._bet(is_active=True, home_score_pred=1, away_score_pred=1)
        result = _infer_advancing_team(match, bet, home_team, away_team)
        self.assertIsNone(result)

    # _infer_losing_team tests
    def test_losing_winner_none(self):
        """winner_team=None → None."""
        result = _infer_losing_team(None, self._team(1), self._team(2))
        self.assertIsNone(result)

    def test_losing_home_none(self):
        """home_team=None → None."""
        result = _infer_losing_team(self._team(1), None, self._team(2))
        self.assertIsNone(result)

    def test_losing_away_none(self):
        """away_team=None → None."""
        result = _infer_losing_team(self._team(1), self._team(1), None)
        self.assertIsNone(result)

    def test_losing_winner_is_home(self):
        """winner.id == home.id → returns away_team."""
        winner = self._team(1)
        home = self._team(1)
        away = self._team(2)
        result = _infer_losing_team(winner, home, away)
        self.assertIs(result, away)

    def test_losing_winner_is_away(self):
        """winner.id == away.id → returns home_team."""
        winner = self._team(2)
        home = self._team(1)
        away = self._team(2)
        result = _infer_losing_team(winner, home, away)
        self.assertIs(result, home)

    # _build_winners_map tests
    def test_winners_map_bet_with_winner_pred(self):
        """bet active with winner_pred_id → uses winner_pred."""
        winner_pred = self._team(100)
        match = self._match_obj(match_number=1, id=1)
        bet = self._bet(is_active=True, winner_pred_id=100, winner_pred=winner_pred)
        bets_by_match_id = {1: bet}

        result = _build_winners_map([match], bets_by_match_id)
        self.assertEqual(result[1], winner_pred)

    def test_winners_map_bet_scores_home_wins(self):
        """group match, bet active, home_score_pred > away_score_pred → uses match.home_team."""
        home_team = self._team(1)
        away_team = self._team(2)
        match = self._match_obj(
            match_number=1,
            id=1,
            home_team=home_team,
            away_team=away_team,
            home_team_id=1,
            away_team_id=2,
            group_id=1,
        )
        bet = self._bet(is_active=True, home_score_pred=2, away_score_pred=1)
        bets_by_match_id = {1: bet}

        result = _build_winners_map([match], bets_by_match_id)
        self.assertEqual(result[1], home_team)

    def test_winners_map_bet_scores_away_wins(self):
        """group match, bet active, away_score_pred > home_score_pred → uses match.away_team."""
        home_team = self._team(1)
        away_team = self._team(2)
        match = self._match_obj(
            match_number=1,
            id=1,
            home_team=home_team,
            away_team=away_team,
            home_team_id=1,
            away_team_id=2,
            group_id=1,
        )
        bet = self._bet(is_active=True, home_score_pred=1, away_score_pred=2)
        bets_by_match_id = {1: bet}

        result = _build_winners_map([match], bets_by_match_id)
        self.assertEqual(result[1], away_team)

    def test_winners_map_no_bet_match_has_winner(self):
        """group match, no bet, match has winner_id → uses match.winner."""
        winner = self._team(100)
        match = self._match_obj(match_number=1, id=1, winner_id=100, winner=winner, group_id=1)
        bets_by_match_id = {}

        result = _build_winners_map([match], bets_by_match_id)
        self.assertEqual(result[1], winner)

    def test_winners_map_no_bet_no_winner(self):
        """no bet, no match.winner_id → not in map."""
        match = self._match_obj(match_number=1, id=1, winner_id=None)
        bets_by_match_id = {}

        result = _build_winners_map([match], bets_by_match_id)
        self.assertNotIn(1, result)

    def test_winners_map_knockout_real_winner_not_used(self):
        """knockout match (group_id=None), no bet, real winner set → NOT in map (preserves user projection)."""
        winner = self._team(100)
        match = self._match_obj(match_number=49, id=49, winner_id=100, winner=winner, group_id=None)
        bets_by_match_id = {}

        result = _build_winners_map([match], bets_by_match_id)
        self.assertNotIn(49, result)

    def test_winners_map_knockout_score_inference_not_used(self):
        """knockout match, bet with scores but no winner_pred → NOT in map (score inference skipped for knockout)."""
        home_team = self._team(1)
        away_team = self._team(2)
        match = self._match_obj(
            match_number=49,
            id=49,
            home_team=home_team,
            away_team=away_team,
            home_team_id=1,
            away_team_id=2,
            group_id=None,
        )
        bet = self._bet(is_active=True, home_score_pred=2, away_score_pred=1)
        bets_by_match_id = {49: bet}

        result = _build_winners_map([match], bets_by_match_id)
        self.assertNotIn(49, result)

    # _projection_is_stale_from_prefetched tests
    def test_stale_no_active_group_bets(self):
        """no active bets with group → False."""
        # All bets inactive
        match = self._match_obj(group_id=1)
        bet = self._bet(is_active=False, match=match, updated_at=tz_now())
        bets = [bet]
        projected_standings = [SimpleNamespace(updated_at=tz_now())]
        projected_third_places = [SimpleNamespace(updated_at=tz_now())]

        result = _projection_is_stale_from_prefetched(bets, projected_standings, projected_third_places)
        self.assertFalse(result)

    def test_stale_no_group_id_in_bets(self):
        """bets active but no group_id (knockout bets) → False."""
        match = self._match_obj(group_id=None)  # knockout match
        bet = self._bet(is_active=True, match=match, updated_at=tz_now())
        bets = [bet]
        projected_standings = [SimpleNamespace(updated_at=tz_now())]
        projected_third_places = [SimpleNamespace(updated_at=tz_now())]

        result = _projection_is_stale_from_prefetched(bets, projected_standings, projected_third_places)
        self.assertFalse(result)

    def test_stale_empty_standings(self):
        """bets active + standings empty → True."""
        match = self._match_obj(group_id=1)
        bet_time = tz_now()
        bet = self._bet(is_active=True, match=match, updated_at=bet_time)
        bets = [bet]
        projected_standings = []
        projected_third_places = [SimpleNamespace(updated_at=bet_time)]

        result = _projection_is_stale_from_prefetched(bets, projected_standings, projected_third_places)
        self.assertTrue(result)

    def test_stale_empty_third_places(self):
        """bets active + third_places empty → True."""
        match = self._match_obj(group_id=1)
        bet_time = tz_now()
        bet = self._bet(is_active=True, match=match, updated_at=bet_time)
        bets = [bet]
        projected_standings = [SimpleNamespace(updated_at=bet_time)]
        projected_third_places = []

        result = _projection_is_stale_from_prefetched(bets, projected_standings, projected_third_places)
        self.assertTrue(result)

    def test_stale_standings_older_than_bets(self):
        """standings.updated_at < bet.updated_at → True."""
        match = self._match_obj(group_id=1)
        bet_time = tz_now()
        old_time = bet_time - datetime.timedelta(minutes=10)
        bet = self._bet(is_active=True, match=match, updated_at=bet_time)
        bets = [bet]
        projected_standings = [SimpleNamespace(updated_at=old_time)]
        projected_third_places = [SimpleNamespace(updated_at=bet_time)]

        result = _projection_is_stale_from_prefetched(bets, projected_standings, projected_third_places)
        self.assertTrue(result)

    def test_stale_third_older_than_bets(self):
        """third_places.updated_at < bet.updated_at → True."""
        match = self._match_obj(group_id=1)
        bet_time = tz_now()
        old_time = bet_time - datetime.timedelta(minutes=10)
        bet = self._bet(is_active=True, match=match, updated_at=bet_time)
        bets = [bet]
        projected_standings = [SimpleNamespace(updated_at=bet_time)]
        projected_third_places = [SimpleNamespace(updated_at=old_time)]

        result = _projection_is_stale_from_prefetched(bets, projected_standings, projected_third_places)
        self.assertTrue(result)

    def test_not_stale_standings_current(self):
        """standings and third_places current → False."""
        match = self._match_obj(group_id=1)
        bet_time = tz_now()
        new_time = bet_time + datetime.timedelta(minutes=10)
        bet = self._bet(is_active=True, match=match, updated_at=bet_time)
        bets = [bet]
        projected_standings = [SimpleNamespace(updated_at=new_time)]
        projected_third_places = [SimpleNamespace(updated_at=new_time)]

        result = _projection_is_stale_from_prefetched(bets, projected_standings, projected_third_places)
        self.assertFalse(result)

    # _build_projected_groups_from_rows tests
    def test_build_groups_empty(self):
        """Empty list → []."""
        result = _build_projected_groups_from_rows([])
        self.assertEqual(result, [])

    def test_build_groups_same_group(self):
        """2 rows same group → [{"group": group, "standings": [row1, row2]}]."""
        group = SimpleNamespace(name="A")
        row1 = SimpleNamespace(group=group, position=1)
        row2 = SimpleNamespace(group=group, position=2)

        result = _build_projected_groups_from_rows([row1, row2])
        self.assertEqual(len(result), 1)
        self.assertIs(result[0]["group"], group)
        self.assertEqual(result[0]["standings"], [row1, row2])

    def test_build_groups_two_groups(self):
        """2 rows different groups → 2 dicts (must be sorted)."""
        group_a = SimpleNamespace(name="A")
        group_b = SimpleNamespace(name="B")
        row1 = SimpleNamespace(group=group_a, position=1)
        row2 = SimpleNamespace(group=group_b, position=1)

        # itertools.groupby requires sorted input for proper grouping
        result = _build_projected_groups_from_rows([row1, row2])
        self.assertEqual(len(result), 2)
        self.assertIs(result[0]["group"], group_a)
        self.assertIs(result[1]["group"], group_b)

    # _build_third_rows_from_rows tests
    def test_build_third_empty(self):
        """Empty list → []."""
        result = _build_third_rows_from_rows([])
        self.assertEqual(result, [])

    def test_build_third_one_row(self):
        """1 row → dict with group, line, score, position_global, is_qualified."""
        group = SimpleNamespace(name="A")
        row = SimpleNamespace(
            group=group,
            score=5,
            position_global=1,
            is_qualified=True,
        )

        result = _build_third_rows_from_rows([row])
        self.assertEqual(len(result), 1)
        self.assertIs(result[0]["group"], group)
        self.assertIs(result[0]["line"], row)
        self.assertEqual(result[0]["score"], 5)
        self.assertEqual(result[0]["position_global"], 1)


class ResolveKnockoutAdvancingTest(TestCase):
    def _build_tipo2_knockout_fixture(self):
        user = User.objects.create_user(username="adv_user", email="adv@example.com", password="123456Aa!")
        competition = Competition.objects.create(fifa_id=8001, name="Copa Adv")
        season = Season.objects.create(
            fifa_id=8001,
            competition=competition,
            name="Temp Adv",
            year=2026,
            start_date="2026-06-01",
            end_date="2026-07-30",
        )
        stage_r32 = Stage.objects.create(fifa_id="R32-ADV", season=season, name="R32", order=50)
        home_team = Team.objects.create(fifa_id="ADV_HOME", name="Home Team Adv", name_norm="hometeamadv", code="HTA")
        away_team = Team.objects.create(fifa_id="ADV_AWAY", name="Away Team Adv", name_norm="awayteamadv", code="ATA")
        future = timezone.now() + timezone.timedelta(days=2)
        r32_match = Match.objects.create(
            fifa_id="ADV-R32-1",
            season=season,
            stage=stage_r32,
            match_number=80,
            match_date_utc=future,
            match_date_local=future,
            match_date_brasilia=future,
            home_team=home_team,
            away_team=away_team,
        )
        pool = Pool.objects.create(
            name="Pool Adv",
            slug="pool-adv",
            season=season,
            created_by=user,
            requires_payment=False,
            pool_type=POOL_TYPE_2,
        )
        participant = PoolParticipant.objects.create(pool=pool, user=user, is_active=True)
        # home wins 2x1 → advancing = home_team
        PoolBet.objects.create(
            participant=participant,
            match=r32_match,
            home_score_pred=2,
            away_score_pred=1,
            winner_pred=home_team,
            is_active=True,
        )
        knockout_matches = list(
            Match.objects.filter(season=season)
            .select_related("stage", "home_team", "away_team")
            .order_by("match_number")
        )
        bets_by_match_id = {
            b.match_id: b for b in PoolBet.objects.filter(participant=participant).select_related("winner_pred")
        }
        return {
            "participant": participant,
            "season": season,
            "r32_match": r32_match,
            "knockout_matches": knockout_matches,
            "bets_by_match_id": bets_by_match_id,
            "expected_advancing_team_id": home_team.id,
        }

    def test_advancing_map_uses_winner_pred_for_r32(self):
        from src.pool.services.context_builder import resolve_knockout_advancing_by_match

        ctx = self._build_tipo2_knockout_fixture()
        advancing = resolve_knockout_advancing_by_match(
            participant=ctx["participant"],
            matches=ctx["knockout_matches"],
            season=ctx["season"],
            bets_by_match_id=ctx["bets_by_match_id"],
        )
        self.assertEqual(advancing[ctx["r32_match"].id], ctx["expected_advancing_team_id"])


class ContextBuilderBetScoreRowTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="u_bs", email="u_bs@example.com", password="123456Aa!")
        self.competition = Competition.objects.create(fifa_id=99, name="Copa BS")
        self.season = Season.objects.create(
            fifa_id=99,
            competition=self.competition,
            name="Temporada BS",
            year=2026,
            start_date="2026-06-01",
            end_date="2026-07-30",
        )
        self.stage = Stage.objects.create(fifa_id="GROUP_BS", season=self.season, name="Group Stage", order=1)
        self.group = Group.objects.create(fifa_id="G_BS", stage=self.stage, name="A")
        self.home = Team.objects.create(fifa_id="HBS", name="HomeBS", name_norm="homebs", code="HBS")
        self.away = Team.objects.create(fifa_id="ABS", name="AwayBS", name_norm="awaybs", code="ABS")
        past = timezone.now() - datetime.timedelta(hours=3)
        self.match = Match.objects.create(
            fifa_id="MBS1",
            season=self.season,
            stage=self.stage,
            home_team=self.home,
            away_team=self.away,
            match_date_utc=past,
            match_date_local=past,
            match_date_brasilia=past,
            match_number=1,
            home_score=2,
            away_score=1,
            status=Match.STATUS_FINISHED,
        )
        self.pool = Pool.objects.create(
            name="Pool BS",
            slug="pool-bs",
            season=self.season,
            created_by=self.user,
        )
        self.participant = PoolParticipant.objects.create(pool=self.pool, user=self.user, is_active=True)
        self.bet = PoolBet.objects.create(
            participant=self.participant,
            match=self.match,
            home_score_pred=2,
            away_score_pred=1,
        )

    def test_bet_score_in_row_when_score_exists(self):
        from src.pool.models import PoolBetScore
        from src.pool.services.context_builder import build_pool_participant_view_context

        score = PoolBetScore.objects.create(bet=self.bet, points=3, exact_score=True)
        ctx = build_pool_participant_view_context(pool=self.pool, participant=self.participant, ensure_bets=False)
        group_rows = ctx["group_rows"]
        self.assertEqual(len(group_rows), 1)
        row = group_rows[0]
        self.assertIn("bet_score", row)
        self.assertEqual(row["bet_score"].points, 3)
        self.assertEqual(row["bet_score"].pk, score.pk)

    def test_bet_score_none_when_no_score_exists(self):
        from src.pool.services.context_builder import build_pool_participant_view_context

        ctx = build_pool_participant_view_context(pool=self.pool, participant=self.participant, ensure_bets=False)
        group_rows = ctx["group_rows"]
        row = group_rows[0]
        self.assertIn("bet_score", row)
        self.assertIsNone(row["bet_score"])


class SyncPersistedStandingsUpsertTest(TestCase):
    """Regression: sync_persisted_group_standings deve usar UPSERT, não DELETE+INSERT.

    DELETE-tudo abria janela onde dois escritores concorrentes (worker + request web)
    inseriam as mesmas linhas e o segundo batia em UniqueViolation. Os testes abaixo
    fixam o comportamento correto para impedir regressão silenciosa.
    """

    def setUp(self):
        self.user = User.objects.create_user(username="upsert_user", email="upsert@example.com", password="123456Aa!")
        self.competition = Competition.objects.create(fifa_id=99, name="Copa Upsert")
        self.season = Season.objects.create(
            fifa_id=99,
            competition=self.competition,
            name="Temporada Upsert",
            year=2026,
            start_date="2026-06-01",
            end_date="2026-07-30",
        )
        self.stage = Stage.objects.create(fifa_id="GUPSERT", season=self.season, name="Group Stage", order=1)
        self.group = Group.objects.create(fifa_id="G-UP", stage=self.stage, name="U")
        now = timezone.now()
        self.team_x = Team.objects.create(
            fifa_id="TX", name="Team X", name_norm="team-x", code="XXX", world_ranking=1, group=self.group
        )
        self.team_y = Team.objects.create(
            fifa_id="TY", name="Team Y", name_norm="team-y", code="YYY", world_ranking=2, group=self.group
        )
        self.match = Match.objects.create(
            fifa_id="MUP1",
            season=self.season,
            stage=self.stage,
            group=self.group,
            match_number=1,
            match_date_utc=now,
            match_date_local=now,
            match_date_brasilia=now,
            home_team=self.team_x,
            away_team=self.team_y,
        )
        self.pool = Pool.objects.create(
            name="Pool Upsert",
            slug="pool-upsert",
            season=self.season,
            created_by=self.user,
            requires_payment=False,
        )
        self.participant = PoolParticipant.objects.create(pool=self.pool, user=self.user, is_active=True)
        self.bet = PoolBet.objects.create(
            participant=self.participant, match=self.match, home_score_pred=1, away_score_pred=0
        )

    def test_rerun_updates_rows_in_place_no_duplicate(self):
        sync_persisted_group_standings(participant=self.participant)
        ids_first = set(
            PoolParticipantStanding.objects.filter(participant=self.participant).values_list("id", flat=True)
        )
        points_x_first = PoolParticipantStanding.objects.get(participant=self.participant, team=self.team_x).points
        self.assertEqual(len(ids_first), 2)
        self.assertEqual(points_x_first, 3)  # team_x ganhou 1x0

        # Muda palpite: empate → team_x deve ter 1 ponto agora
        self.bet.away_score_pred = 1
        self.bet.save()

        sync_persisted_group_standings(participant=self.participant)
        ids_second = set(
            PoolParticipantStanding.objects.filter(participant=self.participant).values_list("id", flat=True)
        )
        points_x_second = PoolParticipantStanding.objects.get(participant=self.participant, team=self.team_x).points

        # Sem novas linhas criadas (IDs idênticos = UPSERT, não DELETE+INSERT)
        self.assertEqual(ids_first, ids_second, "UPSERT deve atualizar as mesmas linhas, não criar novas")
        # Valor realmente atualizado
        self.assertEqual(points_x_second, 1, "Pontuação deve refletir o novo palpite após segundo sync")

    def test_stale_rows_deleted_on_sync(self):
        # Insere manualmente uma linha "fantasma" para um time que não participa de nenhuma partida.
        # Simula dado obsoleto que deveria ser removido pelo sync.
        team_phantom = Team.objects.create(
            fifa_id="TPH", name="Phantom", name_norm="phantom", code="PHT", world_ranking=99, group=self.group
        )
        PoolParticipantStanding.objects.create(
            participant=self.participant,
            group=self.group,
            team=team_phantom,
            position=99,
            points=0,
        )
        self.assertEqual(PoolParticipantStanding.objects.filter(participant=self.participant).count(), 1)

        sync_persisted_group_standings(participant=self.participant)

        self.assertFalse(
            PoolParticipantStanding.objects.filter(participant=self.participant, team=team_phantom).exists(),
            "Linha fantasma (time fora da projeção) deve ser removida no sync",
        )
        # Linhas reais dos times da partida devem estar presentes
        self.assertEqual(PoolParticipantStanding.objects.filter(participant=self.participant).count(), 2)


class BetSaveAjaxFlowTest(TestCase):
    """Save bulk via AJAX + endpoints de status/partial do mata-mata."""

    ajax_headers = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}

    def setUp(self):
        self.user = User.objects.create_user(username="ajaxsave", email="ajaxsave@example.com", password="123456Aa!")
        self.competition = Competition.objects.create(fifa_id=9, name="Copa 9")
        self.season = Season.objects.create(
            fifa_id=9,
            competition=self.competition,
            name="Temporada 9",
            year=2026,
            start_date="2026-06-01",
            end_date="2026-07-30",
        )
        self.stage_group = Stage.objects.create(fifa_id="GROUP9", season=self.season, name="Group Stage", order=90)
        self.stage_r16 = Stage.objects.create(fifa_id="R16-9", season=self.season, name="Round of 16", order=91)
        self.group_a = Group.objects.create(fifa_id="GA9", stage=self.stage_group, name="A")

        self.team_a = Team.objects.create(
            fifa_id="A9", name="Alpha 9", name_norm="alpha9", code="A9", group=self.group_a
        )
        self.team_b = Team.objects.create(
            fifa_id="B9", name="Beta 9", name_norm="beta9", code="B9", group=self.group_a
        )

        group_date = timezone.now() + timezone.timedelta(days=2)
        knockout_date = timezone.now() + timezone.timedelta(days=3)
        self.group_match = Match.objects.create(
            fifa_id="GM9",
            season=self.season,
            stage=self.stage_group,
            group=self.group_a,
            match_number=90,
            match_date_utc=group_date,
            match_date_local=group_date,
            match_date_brasilia=group_date,
            home_team=self.team_a,
            away_team=self.team_b,
        )
        self.knockout_match = Match.objects.create(
            fifa_id="KM9",
            season=self.season,
            stage=self.stage_r16,
            match_number=91,
            match_date_utc=knockout_date,
            match_date_local=knockout_date,
            match_date_brasilia=knockout_date,
            home_placeholder="W90",
            away_placeholder="W90",
            home_team=self.team_a,
            away_team=self.team_b,
        )

        self.pool = Pool.objects.create(
            name="Pool Ajax Save",
            slug="pool-ajax-save",
            season=self.season,
            created_by=self.user,
            requires_payment=False,
        )
        self.participant = PoolParticipant.objects.create(pool=self.pool, user=self.user, is_active=True)
        self.client.force_login(self.user)

    def _group_payload(self, home=2, away=1):
        return {
            f"match_{self.group_match.id}_home_score_pred": str(home),
            f"match_{self.group_match.id}_away_score_pred": str(away),
            f"match_{self.group_match.id}_winner_pred": "",
        }

    def test_bulk_save_ajax_returns_json(self):
        response = self.client.post(
            reverse("pool:save-bets-bulk", kwargs={"slug": self.pool.slug}),
            data=self._group_payload(),
            **self.ajax_headers,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertGreaterEqual(data["saved_group_count"], 1)
        self.assertTrue(data["knockout_review"])
        self.assertTrue(data["projection_pending"])

    def test_bulk_save_non_ajax_still_redirects(self):
        response = self.client.post(
            reverse("pool:save-bets-bulk", kwargs={"slug": self.pool.slug}),
            data=self._group_payload(),
        )
        self.assertRedirects(response, reverse("pool:detail", kwargs={"slug": self.pool.slug}))

    def test_projection_status_ready_when_no_pending_job(self):
        response = self.client.get(reverse("pool:projection-status", kwargs={"slug": self.pool.slug}))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertFalse(data["pending"])
        self.assertTrue(data["ready"])

    def test_projection_status_pending_after_enqueue(self):
        PoolBet.objects.create(
            participant=self.participant,
            match=self.group_match,
            home_score_pred=2,
            away_score_pred=1,
        )
        enqueue_projection_recalc(self.participant)

        response = self.client.get(reverse("pool:projection-status", kwargs={"slug": self.pool.slug}))
        data = response.json()
        self.assertTrue(data["pending"])
        self.assertTrue(has_pending_projection_recalc(self.participant))
        self.assertFalse(data["ready"])

    def test_projection_status_not_pending_after_worker_processes(self):
        PoolBet.objects.create(
            participant=self.participant,
            match=self.group_match,
            home_score_pred=2,
            away_score_pred=1,
            winner_pred=self.team_a,
        )
        enqueue_projection_recalc(self.participant)
        process_next_projection_recalc_job()

        # Re-fetch para descartar o cache da relation OneToOne (status agora IDLE no banco).
        fresh = PoolParticipant.objects.get(pk=self.participant.pk)
        self.assertFalse(has_pending_projection_recalc(fresh))

        response = self.client.get(reverse("pool:projection-status", kwargs={"slug": self.pool.slug}))
        self.assertFalse(response.json()["pending"])

    def test_knockout_cards_partial_renders_projected_team(self):
        PoolBet.objects.create(
            participant=self.participant,
            match=self.group_match,
            home_score_pred=2,
            away_score_pred=1,
            winner_pred=self.team_a,
        )

        response = self.client.get(reverse("pool:knockout-cards", kwargs={"slug": self.pool.slug}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.team_a.name)
        self.assertContains(response, f"match_{self.knockout_match.id}_home_score_pred")

    # --- save_bets_bulk AJAX: ramos adicionais -------------------------------

    def test_bulk_save_ajax_knockout_only_no_projection(self):
        response = self.client.post(
            reverse("pool:save-bets-bulk", kwargs={"slug": self.pool.slug}),
            data={
                f"match_{self.knockout_match.id}_home_score_pred": "2",
                f"match_{self.knockout_match.id}_away_score_pred": "0",
                f"match_{self.knockout_match.id}_winner_pred": "",
            },
            **self.ajax_headers,
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertGreaterEqual(data["saved_count"], 1)
        self.assertEqual(data["saved_group_count"], 0)
        self.assertGreaterEqual(data["saved_knockout_count"], 1)
        self.assertFalse(data["knockout_review"])
        self.assertFalse(data["projection_pending"])
        self.assertFalse(PoolProjectionRecalc.objects.filter(participant=self.participant).exists())

    def test_bulk_save_ajax_no_changes_reports_zero(self):
        payload = self._group_payload()
        self.client.post(
            reverse("pool:save-bets-bulk", kwargs={"slug": self.pool.slug}),
            data=payload,
            **self.ajax_headers,
        )
        # Reenvia valores idênticos -> nada muda.
        response = self.client.post(
            reverse("pool:save-bets-bulk", kwargs={"slug": self.pool.slug}),
            data=payload,
            **self.ajax_headers,
        )
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["saved_count"], 0)
        self.assertEqual(data["saved_group_count"], 0)
        self.assertEqual(data["validation_errors"], [])

    def test_bulk_save_ajax_validation_error_listed(self):
        response = self.client.post(
            reverse("pool:save-bets-bulk", kwargs={"slug": self.pool.slug}),
            data={
                f"match_{self.group_match.id}_home_score_pred": "2",
                f"match_{self.group_match.id}_away_score_pred": "",
                f"match_{self.group_match.id}_winner_pred": "",
            },
            **self.ajax_headers,
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["saved_count"], 0)
        self.assertTrue(data["validation_errors"])

    def test_bulk_save_ajax_enqueues_job_on_group_change(self):
        self.client.post(
            reverse("pool:save-bets-bulk", kwargs={"slug": self.pool.slug}),
            data=self._group_payload(),
            **self.ajax_headers,
        )
        job = PoolProjectionRecalc.objects.get(participant=self.participant)
        self.assertEqual(job.status, PoolProjectionRecalc.STATUS_PENDING)

    def test_bulk_save_ajax_top_scorer_change(self):
        player = Player.objects.create(
            fifa_id="P9A",
            team=self.team_a,
            name="Artilheiro 9",
            short_name="A9",
            position="Forward",
        )
        response = self.client.post(
            reverse("pool:save-bets-bulk", kwargs={"slug": self.pool.slug}),
            data={"top_scorer_pred": str(player.id)},
            **self.ajax_headers,
        )
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertTrue(data["top_scorer_changed"])
        self.participant.refresh_from_db()
        self.assertEqual(self.participant.top_scorer_pred_id, player.id)

    def test_bulk_save_ajax_forbidden_when_cannot_bet(self):
        self.pool.requires_payment = True
        self.pool.save(update_fields=["requires_payment"])

        response = self.client.post(
            reverse("pool:save-bets-bulk", kwargs={"slug": self.pool.slug}),
            data=self._group_payload(),
            **self.ajax_headers,
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(response.json()["ok"])

    def test_bulk_save_ajax_skips_locked_group_match(self):
        past = timezone.now() - timezone.timedelta(days=1)
        locked_match = Match.objects.create(
            fifa_id="GM9-LOCK",
            season=self.season,
            stage=self.stage_group,
            group=self.group_a,
            match_number=89,
            match_date_utc=past,
            match_date_local=past,
            match_date_brasilia=past,
            home_team=self.team_a,
            away_team=self.team_b,
        )
        # Com um jogo de grupo no passado, a fase de grupos fica travada (lock = 1º jogo).
        response = self.client.post(
            reverse("pool:save-bets-bulk", kwargs={"slug": self.pool.slug}),
            data={
                f"match_{locked_match.id}_home_score_pred": "1",
                f"match_{locked_match.id}_away_score_pred": "0",
                f"match_{locked_match.id}_winner_pred": "",
            },
            **self.ajax_headers,
        )
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["saved_group_count"], 0)
        self.assertFalse(
            PoolBet.objects.filter(participant=self.participant, match=locked_match, is_active=True).exists()
        )

    # --- auth / escopo dos novos endpoints -----------------------------------

    def test_projection_status_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("pool:projection-status", kwargs={"slug": self.pool.slug}))
        self.assertEqual(response.status_code, 302)

    def test_knockout_cards_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("pool:knockout-cards", kwargs={"slug": self.pool.slug}))
        self.assertEqual(response.status_code, 302)

    def test_projection_status_404_for_non_participant(self):
        other = User.objects.create_user(username="outsider9", email="out9@example.com", password="123456Aa!")
        self.client.force_login(other)
        response = self.client.get(reverse("pool:projection-status", kwargs={"slug": self.pool.slug}))
        self.assertEqual(response.status_code, 404)

    def test_knockout_cards_404_for_non_participant(self):
        other = User.objects.create_user(username="outsider9b", email="out9b@example.com", password="123456Aa!")
        self.client.force_login(other)
        response = self.client.get(reverse("pool:knockout-cards", kwargs={"slug": self.pool.slug}))
        self.assertEqual(response.status_code, 404)

    # --- knockout_cards_partial: comportamento -------------------------------

    def test_knockout_cards_partial_does_not_create_bets(self):
        self.assertEqual(PoolBet.objects.filter(participant=self.participant).count(), 0)
        self.client.get(reverse("pool:knockout-cards", kwargs={"slug": self.pool.slug}))
        # ensure_bets=False -> não materializa palpites.
        self.assertEqual(PoolBet.objects.filter(participant=self.participant).count(), 0)

    def test_knockout_cards_partial_reflects_projection_change(self):
        bet = PoolBet.objects.create(
            participant=self.participant,
            match=self.group_match,
            home_score_pred=2,
            away_score_pred=1,
            winner_pred=self.team_a,
        )
        response = self.client.get(reverse("pool:knockout-cards", kwargs={"slug": self.pool.slug}))
        rows = response.context["knockout_rows"]
        self.assertEqual(rows[0]["home_team"].id, self.team_a.id)

        # Inverte o vencedor projetado.
        bet.home_score_pred = 0
        bet.away_score_pred = 3
        bet.winner_pred = self.team_b
        bet.save()

        response = self.client.get(reverse("pool:knockout-cards", kwargs={"slug": self.pool.slug}))
        rows = response.context["knockout_rows"]
        self.assertEqual(rows[0]["home_team"].id, self.team_b.id)

    def test_knockout_cards_partial_shows_saved_bet_value(self):
        PoolBet.objects.create(
            participant=self.participant,
            match=self.knockout_match,
            home_score_pred=4,
            away_score_pred=2,
            winner_pred=self.team_a,
            is_active=True,
        )
        response = self.client.get(reverse("pool:knockout-cards", kwargs={"slug": self.pool.slug}))
        self.assertContains(response, 'value="4"')


class Tipo2KnockoutOpenTestCase(TestCase):
    """Tipo 2: R32 abre por times reais; R16+ abre por projeção dos palpites; trava global no 1o jogo de mata-mata."""

    def setUp(self):
        self.user = User.objects.create_user(username="t2user", password="x")
        self.competition = Competition.objects.create(fifa_id=9001, name="Copa T2")
        self.season = Season.objects.create(
            fifa_id=9002,
            competition=self.competition,
            name="Temp T2",
            year=2026,
            start_date="2026-06-01",
            end_date="2026-07-30",
        )
        self.stage_r32 = Stage.objects.create(fifa_id="R32-T2", season=self.season, name="R32", order=50)
        self.stage_r16 = Stage.objects.create(fifa_id="R16-T2", season=self.season, name="Round of 16", order=51)

        def mk_team(code):
            return Team.objects.create(fifa_id=f"T2{code}", name=f"Team {code}", name_norm=f"team{code}", code=code)

        self.a, self.b, self.c, self.d = mk_team("A"), mk_team("B"), mk_team("C"), mk_team("D")

        self.future = timezone.now() + timezone.timedelta(days=2)
        self.r32_1 = self._mk_match("R32-1", self.stage_r32, 80, self.future, home=self.a, away=self.b)
        self.r32_2 = self._mk_match("R32-2", self.stage_r32, 82, self.future, home=self.c, away=self.d)
        self.r16 = self._mk_match(
            "R16-1",
            self.stage_r16,
            90,
            self.future + timezone.timedelta(hours=3),
            home_ph="W80",
            away_ph="W82",
        )

        self.pool = Pool.objects.create(
            name="Pool T2",
            slug="pool-t2",
            season=self.season,
            created_by=self.user,
            requires_payment=False,
            pool_type=POOL_TYPE_2,
        )
        self.participant = PoolParticipant.objects.create(pool=self.pool, user=self.user, is_active=True)

    def _mk_match(self, fid, stage, number, date, home=None, away=None, home_ph="", away_ph=""):
        return Match.objects.create(
            fifa_id=fid,
            season=self.season,
            stage=stage,
            match_number=number,
            match_date_utc=date,
            match_date_local=date,
            match_date_brasilia=date,
            home_team=home,
            away_team=away,
            home_placeholder=home_ph,
            away_placeholder=away_ph,
        )

    def _rows(self):
        ctx = build_pool_participant_view_context(pool=self.pool, participant=self.participant, ensure_bets=False)
        return {r["match"].id: r for r in ctx["knockout_rows"]}

    def test_r32_open_with_real_teams_before_global_lock(self):
        rows = self._rows()
        self.assertEqual(rows[self.r32_1.id]["bet_status"], "open")
        self.assertFalse(rows[self.r32_1.id]["locked"])

    def test_r16_awaiting_until_user_bets_feeders(self):
        rows = self._rows()
        self.assertEqual(rows[self.r16.id]["bet_status"], "awaiting_teams")
        self.assertTrue(rows[self.r16.id]["locked"])

    def _bet_r32_feeders(self):
        PoolBet.objects.create(
            participant=self.participant,
            match=self.r32_1,
            home_score_pred=2,
            away_score_pred=0,
            winner_pred=self.a,
        )
        PoolBet.objects.create(
            participant=self.participant,
            match=self.r32_2,
            home_score_pred=2,
            away_score_pred=0,
            winner_pred=self.c,
        )

    def test_r16_opens_after_user_bets_both_feeders(self):
        self._bet_r32_feeders()
        rows = self._rows()
        row = rows[self.r16.id]
        self.assertEqual(row["home_team"].id, self.a.id)
        self.assertEqual(row["away_team"].id, self.c.id)
        self.assertEqual(row["bet_status"], "open")
        self.assertFalse(row["locked"])

    def test_global_lock_at_first_knockout_kickoff(self):
        past = timezone.now() - timezone.timedelta(hours=1)
        Match.objects.filter(id=self.r32_1.id).update(match_date_brasilia=past)
        self._bet_r32_feeders()
        rows = self._rows()
        self.assertEqual(rows[self.r32_2.id]["bet_status"], "locked")
        self.assertTrue(rows[self.r32_2.id]["locked"])
        self.assertEqual(rows[self.r16.id]["bet_status"], "locked")

    def test_save_r16_rejected_until_feeders_bet(self):
        bet = PoolBet(participant=self.participant, match=self.r16, home_score_pred=1, away_score_pred=0)
        with self.assertRaises(ValidationError):
            bet.full_clean()

    def test_save_r16_allowed_after_feeders_bet(self):
        self._bet_r32_feeders()
        bet = PoolBet(participant=self.participant, match=self.r16, home_score_pred=1, away_score_pred=0)
        bet.full_clean()  # não deve levantar
        bet.save()
        self.assertTrue(PoolBet.objects.get(participant=self.participant, match=self.r16).is_active)


class ComputeAsOfStandingsBetsTest(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="asof_owner", email="asof_owner@example.com", password="123456Aa!"
        )
        self.user = User.objects.create_user(username="asof_user", email="asof_user@example.com", password="123456Aa!")

        self.competition = Competition.objects.create(fifa_id=9001, name="Copa AsOf")
        self.season = Season.objects.create(
            fifa_id=9001,
            competition=self.competition,
            name="Temporada AsOf",
            year=2026,
            start_date="2026-06-01",
            end_date="2026-07-30",
        )
        # Stage name "Group Stage" → normalize_stage_key → "GROUP" → PHASE_GROUP
        self.group_stage = Stage.objects.create(
            fifa_id="GROUP9001", season=self.season, name="Group Stage", order=9001
        )

        self.home_team = Team.objects.create(fifa_id="AH1", name="AsOf Home", name_norm="asof-home", code="ASH")
        self.away_team = Team.objects.create(fifa_id="AH2", name="AsOf Away", name_norm="asof-away", code="ASA")

        now = timezone.now()
        past = now - timezone.timedelta(hours=2)

        self.match1 = Match.objects.create(
            fifa_id="ASOF-M1",
            season=self.season,
            stage=self.group_stage,
            match_number=9001,
            match_date_utc=past,
            match_date_local=past,
            match_date_brasilia=past,
            home_team=self.home_team,
            away_team=self.away_team,
            home_score=1,
            away_score=0,
            status=Match.STATUS_FINISHED,
        )
        self.match2 = Match.objects.create(
            fifa_id="ASOF-M2",
            season=self.season,
            stage=self.group_stage,
            match_number=9002,
            match_date_utc=past,
            match_date_local=past,
            match_date_brasilia=past,
            home_team=self.home_team,
            away_team=self.away_team,
            home_score=2,
            away_score=2,
            status=Match.STATUS_FINISHED,
        )

        self.pool = Pool.objects.create(
            name="Pool AsOf",
            slug="pool-asof",
            season=self.season,
            created_by=self.owner,
            requires_payment=False,
            pool_type=2,
        )
        self.participant = PoolParticipant.objects.create(pool=self.pool, user=self.user, is_active=True)

        # Exact bet on match1 (1-0) and wrong bet on match2
        PoolBet.objects.create(
            participant=self.participant,
            match=self.match1,
            home_score_pred=1,
            away_score_pred=0,
        )
        PoolBet.objects.create(
            participant=self.participant,
            match=self.match2,
            home_score_pred=0,
            away_score_pred=0,
        )

        self.scoring_config = self.pool.get_scoring_config()
        self.official_result = self.pool.get_official_results()

    def test_only_allowed_matches_count(self):
        rows = compute_asof_standings(
            self.pool,
            allowed_match_ids={self.match1.id},
            scoring_config=self.scoring_config,
            official_result=self.official_result,
        )
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertIsInstance(row, AsOfStanding)
        self.assertEqual(row.participant.id, self.participant.id)
        # Só o match1 (placar exato) conta; match2 fora do conjunto é ignorado.
        self.assertEqual(row.total_points, self.scoring_config.group_exact_score)
        self.assertEqual(row.group_points, self.scoring_config.group_exact_score)
        self.assertEqual(row.knockout_points, 0)
        self.assertEqual(row.exact_score_hits, 1)
        # Bônus ainda não implementado nesta task.
        self.assertFalse(row.champion_hit)
        self.assertFalse(row.top_scorer_hit)

    def _build_tipo2_decided_knockout_asof(self):
        """Fixture: pool Tipo 2 com jogo de mata-mata decidido.

        Retorna ctx com pool, allowed_match_ids, scoring_config, official_result,
        correct_participant (acertou classificado) e wrong_participant (errou).
        Espelha _build_tipo2_decided_knockout de RecalculateTipo2KnockoutTest,
        mas empacota os extras que compute_asof_standings exige.
        """
        user_correct = User.objects.create_user(
            username="asof_t2_correct", email="asof_t2_correct@example.com", password="pass"
        )
        user_wrong = User.objects.create_user(
            username="asof_t2_wrong", email="asof_t2_wrong@example.com", password="pass"
        )

        competition = Competition.objects.create(fifa_id=8100, name="Copa AsOf T2")
        season = Season.objects.create(
            fifa_id=8100,
            competition=competition,
            name="AsOf T2 Season",
            year=2026,
            start_date="2026-06-01",
            end_date="2026-07-30",
        )
        stage_r32 = Stage.objects.create(fifa_id="R32-ASOF", season=season, name="R32", order=50)

        team_a = Team.objects.create(fifa_id="ASOF-A", name="AsOf Alpha", name_norm="asof-alpha", code="AOA")
        team_b = Team.objects.create(fifa_id="ASOF-B", name="AsOf Beta", name_norm="asof-beta", code="AOB")

        past = timezone.now() - timezone.timedelta(hours=2)
        match = Match.objects.create(
            fifa_id="ASOF-R32-1",
            season=season,
            stage=stage_r32,
            match_number=810,
            match_date_utc=past,
            match_date_local=past,
            match_date_brasilia=past,
            home_team=team_a,
            away_team=team_b,
            home_score=2,
            away_score=1,
            winner=team_a,
            status=Match.STATUS_FINISHED,
        )

        pool = Pool.objects.create(
            name="Pool AsOf T2",
            slug="pool-asof-t2",
            season=season,
            created_by=user_correct,
            requires_payment=False,
            pool_type=POOL_TYPE_2,
        )
        correct_participant = PoolParticipant.objects.create(pool=pool, user=user_correct, is_active=True)
        wrong_participant = PoolParticipant.objects.create(pool=pool, user=user_wrong, is_active=True)

        # Correct: winner_pred == match.winner (team_a), score non-exact → advancing_only tier
        PoolBet.objects.create(
            participant=correct_participant,
            match=match,
            home_score_pred=1,
            away_score_pred=0,
            winner_pred=team_a,
            is_active=True,
        )
        # Wrong: winner_pred == loser (team_b) → gate blocks all knockout points
        PoolBet.objects.create(
            participant=wrong_participant,
            match=match,
            home_score_pred=0,
            away_score_pred=2,
            winner_pred=team_b,
            is_active=True,
        )

        return {
            "pool": pool,
            "allowed_match_ids": {match.id},
            "scoring_config": pool.get_scoring_config(),
            "official_result": pool.get_official_results(),
            "correct_participant": correct_participant,
            "wrong_participant": wrong_participant,
        }

    def test_asof_tipo2_knockout_wrong_advancing_zero(self):
        ctx = self._build_tipo2_decided_knockout_asof()
        rows = compute_asof_standings(
            ctx["pool"], ctx["allowed_match_ids"], ctx["scoring_config"], ctx["official_result"]
        )
        row = next(r for r in rows if r.participant.id == ctx["wrong_participant"].id)
        self.assertEqual(row.knockout_points, 0)

    def test_asof_tipo2_knockout_correct_advancing_scores(self):
        ctx = self._build_tipo2_decided_knockout_asof()
        rows = compute_asof_standings(
            ctx["pool"], ctx["allowed_match_ids"], ctx["scoring_config"], ctx["official_result"]
        )
        row = next(r for r in rows if r.participant.id == ctx["correct_participant"].id)
        self.assertGreater(row.knockout_points, 0)


class ComputeAsOfStandingsBonusTest(TestCase):
    """Group-qualifier bonus is gated: only fires when ALL group matches are in allowed_match_ids."""

    def setUp(self):
        self.owner = User.objects.create_user(
            username="bonus_owner", email="bonus_owner@example.com", password="123456Aa!"
        )
        self.user = User.objects.create_user(
            username="bonus_user", email="bonus_user@example.com", password="123456Aa!"
        )

        self.competition = Competition.objects.create(fifa_id=9002, name="Copa Bonus")
        self.season = Season.objects.create(
            fifa_id=9002,
            competition=self.competition,
            name="Temporada Bonus",
            year=2026,
            start_date="2026-06-01",
            end_date="2026-07-30",
        )
        # Stage name "Group Stage" → normalize_stage_key → "GROUP" → phase_for_match → PHASE_GROUP
        self.group_stage = Stage.objects.create(
            fifa_id="GROUP9002", season=self.season, name="Group Stage", order=9002
        )
        self.group = Group.objects.create(fifa_id="G-BONUS", stage=self.group_stage, name="B")

        # Two teams in the group
        self.team_a = Team.objects.create(
            fifa_id="BA1", name="Bonus Alpha", name_norm="bonus-alpha", code="BA1", group=self.group
        )
        self.team_b = Team.objects.create(
            fifa_id="BA2", name="Bonus Beta", name_norm="bonus-beta", code="BA2", group=self.group
        )

        now = timezone.now()
        past = now - timezone.timedelta(hours=2)

        # Create group matches — all finished so _group_match_ids finds them
        self.match1 = Match.objects.create(
            fifa_id="BONUS-M1",
            season=self.season,
            stage=self.group_stage,
            group=self.group,
            match_number=9010,
            match_date_utc=past,
            match_date_local=past,
            match_date_brasilia=past,
            home_team=self.team_a,
            away_team=self.team_b,
            home_score=1,
            away_score=0,
            status=Match.STATUS_FINISHED,
        )
        self.match2 = Match.objects.create(
            fifa_id="BONUS-M2",
            season=self.season,
            stage=self.group_stage,
            group=self.group,
            match_number=9011,
            match_date_utc=past,
            match_date_local=past,
            match_date_brasilia=past,
            home_team=self.team_b,
            away_team=self.team_a,
            home_score=0,
            away_score=2,
            status=Match.STATUS_FINISHED,
        )
        self.group_matches = [self.match1, self.match2]

        # Real Standing: team_a is position 1 in group B (real qualifier)
        Standing.objects.create(
            season=self.season,
            group=self.group,
            team=self.team_a,
            position=1,
            played=2,
            won=2,
            drawn=0,
            lost=0,
            goals_for=3,
            goals_against=0,
            goal_difference=3,
            points=6,
        )
        Standing.objects.create(
            season=self.season,
            group=self.group,
            team=self.team_b,
            position=2,
            played=2,
            won=0,
            drawn=0,
            lost=2,
            goals_for=0,
            goals_against=3,
            goal_difference=-3,
            points=0,
        )

        self.pool = Pool.objects.create(
            name="Pool Bonus",
            slug="pool-bonus",
            season=self.season,
            created_by=self.owner,
            requires_payment=False,
            pool_type=2,
        )
        self.participant = PoolParticipant.objects.create(pool=self.pool, user=self.user, is_active=True)

        # Participant's projected standing: team_a at position 1 (matches real Standing → qualifier hit)
        PoolParticipantStanding.objects.create(
            participant=self.participant,
            group=self.group,
            team=self.team_a,
            position=1,
        )
        PoolParticipantStanding.objects.create(
            participant=self.participant,
            group=self.group,
            team=self.team_b,
            position=2,
        )

        # No bets needed for the qualifier bonus tests (bonus is independent of bets)

        self.scoring_config = self.pool.get_scoring_config()
        self.official_result = self.pool.get_official_results()

    def test_group_bonus_zero_when_group_not_complete_in_set(self):
        # Only one match in allowed set → group stage NOT complete → no qualifier bonus
        partial = {self.match1.id}
        rows = compute_asof_standings(self.pool, partial, self.scoring_config, self.official_result)
        self.assertEqual(len(rows), 1)
        # With no bets, bet points are 0 whether partial or full
        # Qualifier bonus must NOT be applied when group stage incomplete in set
        self.assertEqual(rows[0].group_points, 0)

    def test_group_bonus_applied_when_group_complete_in_set(self):
        all_ids = {m.id for m in self.group_matches}
        rows = compute_asof_standings(self.pool, all_ids, self.scoring_config, self.official_result)
        self.assertEqual(len(rows), 1)
        # team_a at position 1 is a real qualifier at position 1 → group_qualifier_points + position_bonus
        self.assertGreaterEqual(
            rows[0].group_points,
            self.scoring_config.group_qualifier_points,
        )


class RecalculateTipo2KnockoutTest(TestCase):
    """recalculate_participant_scores aplica gate de classificado para pools Tipo 2."""

    def _build_tipo2_decided_knockout(self):
        user = User.objects.create_user(username="t2recalc", email="t2recalc@example.com", password="pass")
        competition = Competition.objects.create(fifa_id=8001, name="Copa T2 Recalc")
        season = Season.objects.create(
            fifa_id=8001,
            competition=competition,
            name="T2 Recalc Season",
            year=2026,
            start_date="2026-06-01",
            end_date="2026-07-30",
        )
        stage_r32 = Stage.objects.create(fifa_id="R32-RC", season=season, name="R32", order=50)

        team_a = Team.objects.create(fifa_id="RC-A", name="RC Alpha", name_norm="rc-alpha", code="RCA")
        team_b = Team.objects.create(fifa_id="RC-B", name="RC Beta", name_norm="rc-beta", code="RCB")

        past = timezone.now() - timezone.timedelta(hours=2)
        match = Match.objects.create(
            fifa_id="RC-R32-1",
            season=season,
            stage=stage_r32,
            match_number=80,
            match_date_utc=past,
            match_date_local=past,
            match_date_brasilia=past,
            home_team=team_a,
            away_team=team_b,
            home_score=2,
            away_score=1,
            winner=team_a,
            status=Match.STATUS_FINISHED,
        )

        pool = Pool.objects.create(
            name="Pool T2 Recalc",
            slug="pool-t2-recalc",
            season=season,
            created_by=user,
            requires_payment=False,
            pool_type=POOL_TYPE_2,
        )
        participant = PoolParticipant.objects.create(pool=pool, user=user, is_active=True)

        # Correct bet: winner_pred == match.winner (team_a), score non-exact → advancing_only tier
        correct_bet = PoolBet.objects.create(
            participant=participant,
            match=match,
            home_score_pred=1,
            away_score_pred=0,
            winner_pred=team_a,
            is_active=True,
        )

        # Second participant for the "wrong" bet (same pool, different user)
        user_wrong = User.objects.create_user(
            username="t2recalc_wrong", email="t2recalc_wrong@example.com", password="pass"
        )
        participant_wrong = PoolParticipant.objects.create(pool=pool, user=user_wrong, is_active=True)
        wrong_bet = PoolBet.objects.create(
            participant=participant_wrong,
            match=match,
            home_score_pred=0,
            away_score_pred=2,
            winner_pred=team_b,
            is_active=True,
        )

        return {
            "participant": participant,
            "participant_wrong": participant_wrong,
            "correct_bet": correct_bet,
            "wrong_bet": wrong_bet,
        }

    def test_recalculate_uses_advancing_gate(self):
        from src.pool.models import PoolBetScore

        ctx = self._build_tipo2_decided_knockout()

        recalculate_participant_scores(ctx["participant"])
        recalculate_participant_scores(ctx["participant_wrong"])

        score = PoolBetScore.objects.get(bet=ctx["correct_bet"])
        self.assertGreater(score.points, 0)
        self.assertTrue(score.advancing_correct)

        wrong = PoolBetScore.objects.get(bet=ctx["wrong_bet"])
        self.assertEqual(wrong.points, 0)
        self.assertFalse(wrong.advancing_correct)


class RecalculateTipo2KnockoutR16CascadeTest(TestCase):
    """Gate Tipo 2 para R16+: classificado resolvido via cascade de palpites do R32.

    Os jogos de R16 têm home_team/away_team = None (placeholders W<n>).
    O classificado projetado pelo participante para o jogo de R16 é derivado
    dos palpites do R32 que alimentam os slots, NÃO de um winner_pred direto
    no jogo de R16. Isso exercita _walk_knockout_bracket → winners_map cascade.

    Caso (a): participante acertou quem avança do R32 feeder → gate passa → pontos > 0.
    Caso (b): participante errou quem avança do R32 feeder → classificado projetado
              para o R16 é o time errado → gate bloqueia → 0 pontos.
    """

    def _build_r16_cascade_fixture(self):
        user_a = User.objects.create_user(username="r16c_correct", email="r16c_correct@example.com", password="pass")
        user_b = User.objects.create_user(username="r16c_wrong", email="r16c_wrong@example.com", password="pass")

        competition = Competition.objects.create(fifa_id=8200, name="Copa R16 Cascade")
        season = Season.objects.create(
            fifa_id=8200,
            competition=competition,
            name="R16 Cascade Season",
            year=2026,
            start_date="2026-06-01",
            end_date="2026-07-30",
        )
        stage_r32 = Stage.objects.create(fifa_id="R32-R16C", season=season, name="R32", order=50)
        stage_r16 = Stage.objects.create(fifa_id="R16-R16C", season=season, name="Round of 16", order=51)

        team_a = Team.objects.create(fifa_id="R16C-A", name="R16C Alpha", name_norm="r16c-alpha", code="R6A")
        team_b = Team.objects.create(fifa_id="R16C-B", name="R16C Beta", name_norm="r16c-beta", code="R6B")
        team_c = Team.objects.create(fifa_id="R16C-C", name="R16C Gamma", name_norm="r16c-gamma", code="R6C")
        team_d = Team.objects.create(fifa_id="R16C-D", name="R16C Delta", name_norm="r16c-delta", code="R6D")

        past = timezone.now() - timezone.timedelta(hours=3)

        # R32 match #180: team_a vs team_b — real result: team_a wins 2-1
        r32_1 = Match.objects.create(
            fifa_id="R16C-R32-1",
            season=season,
            stage=stage_r32,
            match_number=180,
            match_date_utc=past,
            match_date_local=past,
            match_date_brasilia=past,
            home_team=team_a,
            away_team=team_b,
            home_score=2,
            away_score=1,
            winner=team_a,
            status=Match.STATUS_FINISHED,
        )

        # R32 match #182: team_c vs team_d — real result: team_c wins 2-0
        r32_2 = Match.objects.create(
            fifa_id="R16C-R32-2",
            season=season,
            stage=stage_r32,
            match_number=182,
            match_date_utc=past,
            match_date_local=past,
            match_date_brasilia=past,
            home_team=team_c,
            away_team=team_d,
            home_score=2,
            away_score=0,
            winner=team_c,
            status=Match.STATUS_FINISHED,
        )

        # R16 match #190: placeholder slots W180 × W182.
        # home_team/away_team are intentionally None — the walk must resolve them
        # from the participant's R32 winner picks cascaded through winners_map.
        # Real result: team_a wins 2-1 (team_a was the real R32-1 winner).
        r16 = Match.objects.create(
            fifa_id="R16C-R16-1",
            season=season,
            stage=stage_r16,
            match_number=190,
            match_date_utc=past - timezone.timedelta(hours=1),
            match_date_local=past - timezone.timedelta(hours=1),
            match_date_brasilia=past - timezone.timedelta(hours=1),
            home_team=None,
            away_team=None,
            home_placeholder="W180",
            away_placeholder="W182",
            home_score=2,
            away_score=1,
            winner=team_a,
            status=Match.STATUS_FINISHED,
        )

        pool = Pool.objects.create(
            name="Pool R16 Cascade",
            slug="pool-r16-cascade",
            season=season,
            created_by=user_a,
            requires_payment=False,
            pool_type=POOL_TYPE_2,
        )

        # --- Participant "correct": bets team_a advances from R32 #180 ---
        # The walk will put team_a into the R16 home slot → predicted advancer = team_a.
        # Gate: team_a == match.winner (team_a) → passes → points > 0.
        participant_correct = PoolParticipant.objects.create(pool=pool, user=user_a, is_active=True)
        # R32 #180: predicts team_a (home) wins 2-1
        PoolBet.objects.create(
            participant=participant_correct,
            match=r32_1,
            home_score_pred=2,
            away_score_pred=1,
            winner_pred=team_a,
            is_active=True,
        )
        # R32 #182: predicts team_c (home) wins 2-0 — needed to resolve away slot of R16
        PoolBet.objects.create(
            participant=participant_correct,
            match=r32_2,
            home_score_pred=2,
            away_score_pred=0,
            winner_pred=team_c,
            is_active=True,
        )
        # R16 #190: no winner_pred — advancing team MUST come from walk cascade.
        # Predicts home wins 2-0; walk resolved home = team_a → advancing = team_a.
        r16_correct_bet = PoolBet.objects.create(
            participant=participant_correct,
            match=r16,
            home_score_pred=2,
            away_score_pred=0,
            winner_pred=None,
            is_active=True,
        )

        # --- Participant "wrong": bets team_b advances from R32 #180 ---
        # The walk will put team_b into the R16 home slot → predicted advancer = team_b.
        # Gate: team_b != match.winner (team_a) → blocked → 0 points.
        participant_wrong = PoolParticipant.objects.create(pool=pool, user=user_b, is_active=True)
        # R32 #180: predicts team_b (away) wins 0-2
        PoolBet.objects.create(
            participant=participant_wrong,
            match=r32_1,
            home_score_pred=0,
            away_score_pred=2,
            winner_pred=team_b,
            is_active=True,
        )
        # R32 #182: predicts team_c (home) wins 2-0
        PoolBet.objects.create(
            participant=participant_wrong,
            match=r32_2,
            home_score_pred=2,
            away_score_pred=0,
            winner_pred=team_c,
            is_active=True,
        )
        # R16 #190: no winner_pred. Walk resolved home = team_b → advancing = team_b ≠ team_a.
        r16_wrong_bet = PoolBet.objects.create(
            participant=participant_wrong,
            match=r16,
            home_score_pred=2,
            away_score_pred=0,
            winner_pred=None,
            is_active=True,
        )

        return {
            "participant_correct": participant_correct,
            "participant_wrong": participant_wrong,
            "r16": r16,
            "r16_correct_bet": r16_correct_bet,
            "r16_wrong_bet": r16_wrong_bet,
        }

    def test_r16_cascade_correct_projected_advancer_scores(self):
        """Participante cujo palpite de R32 projeta o time certo para o R16 → pontos > 0."""
        from src.pool.models import PoolBetScore

        ctx = self._build_r16_cascade_fixture()
        recalculate_participant_scores(ctx["participant_correct"])

        score = PoolBetScore.objects.get(bet=ctx["r16_correct_bet"])
        self.assertGreater(score.points, 0, "Gate must pass when projected advancer matches real winner")
        self.assertTrue(score.advancing_correct)

    def test_r16_cascade_wrong_projected_advancer_zero(self):
        """Participante cujo palpite de R32 projeta o time errado para o R16 → 0 pontos."""
        from src.pool.models import PoolBetScore

        ctx = self._build_r16_cascade_fixture()
        recalculate_participant_scores(ctx["participant_wrong"])

        score = PoolBetScore.objects.get(bet=ctx["r16_wrong_bet"])
        self.assertEqual(score.points, 0, "Gate must block when projected advancer does not match real winner")
        self.assertFalse(score.advancing_correct)


# ---------------------------------------------------------------------------
# New unit tests — added to ScoringCalculateBetPointsTest via a separate block
# to avoid editing the existing class definition above. These methods are
# logically identical to adding them inside the class; they live here as a
# distinct batch with a clear heading.
# ---------------------------------------------------------------------------


class ScoringTipo2ExhaustiveUnitTest(SimpleTestCase):
    """Exhaustive coverage of Tipo 2 mata-mata scoring edge-cases (unit, no DB)."""

    def _make_scoring_config(self, **overrides):
        defaults = dict(
            group_exact_score=25,
            group_winner_and_winner_goals=18,
            group_winner_and_diff=15,
            group_winner_and_loser_goals=12,
            group_winner_only=10,
            knockout_exact_and_advancing=35,
            knockout_advancing_and_winner_goals=25,
            knockout_advancing_and_diff=20,
            knockout_advancing_and_loser_goals=17,
            knockout_advancing_only=15,
            knockout_exact_wrong_advancing=10,
            knockout_draw_prediction_points=20,
        )
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    def _make_knockout_bet(
        self,
        home_pred,
        away_pred,
        home_real,
        away_real,
        winner_real_id=None,
        winner_pred_id=None,
        is_active=True,
        home_team_id=1,
    ):
        stage = SimpleNamespace(name="Semi-Final")
        match = SimpleNamespace(
            stage=stage,
            home_score=home_real,
            away_score=away_real,
            winner_id=winner_real_id,
            home_team_id=home_team_id,
            away_team_id=2,
        )
        return SimpleNamespace(
            is_active=is_active,
            home_score_pred=home_pred,
            away_score_pred=away_pred,
            winner_pred_id=winner_pred_id,
            match=match,
        )

    def _make_group_bet(self, home_pred, away_pred, home_real, away_real, is_active=True):
        stage = SimpleNamespace(name="Group Stage")
        match = SimpleNamespace(
            stage=stage,
            home_score=home_real,
            away_score=away_real,
            winner_id=None,
        )
        return SimpleNamespace(
            is_active=is_active,
            home_score_pred=home_pred,
            away_score_pred=away_pred,
            winner_pred_id=None,
            match=match,
        )

    # B5: real 1×1 penalties, winner=team1, predicted team2 → gate fails on draw branch.
    def test_tipo2_real_draw_wrong_advancing_zero(self):
        """B5: draw-decided game, wrong projected advancer → 0 points, advancing_correct False."""
        bet = self._make_knockout_bet(1, 1, 1, 1, winner_real_id=1)
        result = calculate_bet_points(
            bet, self._make_scoring_config(), pool_type=POOL_TYPE_2, predicted_advancing_id=2
        )
        self.assertEqual(result["points"], 0)
        self.assertFalse(result["advancing_correct"])

    # Regra: draw guess 2×2, real 2×0 HOME wins, classificado certo.
    # gate passa (predicted_advancing=1=winner). _knockout_points_by_score:
    # home=2, away=0, guess_home=2, guess_away=2. O palpite é EMPATE e o jogo não
    # foi empate → resultado errado. Mesmo com o gol do mandante coincidindo
    # (2==2), NÃO ganha gols do vencedor: paga só classificado (advancing_only=15).
    def test_tipo2_draw_pred_real_non_draw_only_classified(self):
        """Draw guess 2×2 vs real 2×0 HOME: errou o resultado → só classificado (15)."""
        bet = self._make_knockout_bet(2, 2, 2, 0, winner_real_id=1)
        result = calculate_bet_points(
            bet, self._make_scoring_config(), pool_type=POOL_TYPE_2, predicted_advancing_id=1
        )
        self.assertEqual(result["points"], 15)
        self.assertTrue(result["advancing_correct"])
        self.assertFalse(result["advancing_goals_correct"])

    # C4: inactive bet → always 0 regardless of pool_type.
    def test_tipo2_knockout_inactive_bet_zero(self):
        """C4: inactive bet in Tipo 2 knockout → 0 points."""
        bet = self._make_knockout_bet(2, 1, 2, 1, winner_real_id=1, is_active=False)
        result = calculate_bet_points(
            bet, self._make_scoring_config(), pool_type=POOL_TYPE_2, predicted_advancing_id=1
        )
        self.assertEqual(result["points"], 0)

    # C5: home_score_pred=None → early-exit 0.
    def test_tipo2_knockout_missing_pred_zero(self):
        """C5: None home prediction in Tipo 2 → 0 points."""
        bet = self._make_knockout_bet(None, 1, 2, 1, winner_real_id=1)
        result = calculate_bet_points(
            bet, self._make_scoring_config(), pool_type=POOL_TYPE_2, predicted_advancing_id=1
        )
        self.assertEqual(result["points"], 0)

    # C6: match not played yet (home_score=None) → early-exit 0.
    def test_tipo2_knockout_match_not_played_zero(self):
        """C6: match not yet played (None home_score) in Tipo 2 → 0 points."""
        bet = self._make_knockout_bet(2, 1, None, 1, winner_real_id=1)
        result = calculate_bet_points(
            bet, self._make_scoring_config(), pool_type=POOL_TYPE_2, predicted_advancing_id=1
        )
        self.assertEqual(result["points"], 0)

    # D2: group stage in a Tipo 2 pool is unaffected by Tipo 2 gate.
    # Bet 2×0 vs real 2×1: correct winner (HOME), winner goals (home 2==2) → 18.
    def test_group_bet_unaffected_by_pool_type_2(self):
        """D2: group bet scored with pool_type=POOL_TYPE_2 returns same result as default (18)."""
        bet = self._make_group_bet(2, 0, 2, 1)
        result = calculate_bet_points(bet, self._make_scoring_config(), pool_type=POOL_TYPE_2)
        self.assertEqual(result["points"], 18)
        self.assertTrue(result["advancing_correct"])

    # Sem predicted_team_ids a exceção não dispara: classificado errado → 0,
    # mesmo com placar exato (placar decisivo 2x1).
    def test_tipo2_knockout_exact_wrong_advancing_field_ignored(self):
        """Sem info dos times projetados, classificado errado → 0 (sem exceção)."""
        # Sem predicted_team_ids a exceção não dispara, mesmo com knockout_exact_wrong_advancing=10.
        bet = self._make_knockout_bet(2, 1, 2, 1, winner_real_id=1)
        result = calculate_bet_points(
            bet,
            self._make_scoring_config(knockout_exact_wrong_advancing=10),
            pool_type=POOL_TYPE_2,
            predicted_advancing_id=2,
        )
        self.assertEqual(result["points"], 0)
        self.assertFalse(result["advancing_correct"])

    # Exceção via fallback flat: classificado errado + placar exato + os dois
    # times do palpite são os reais → paga knockout_exact_wrong_advancing (10).
    def test_tipo2_exact_wrong_advancing_flat(self):
        bet = self._make_knockout_bet(1, 1, 1, 1, winner_real_id=1)
        result = calculate_bet_points(
            bet,
            self._make_scoring_config(),
            pool_type=POOL_TYPE_2,
            predicted_advancing_id=2,
            predicted_team_ids=(1, 2),
        )
        self.assertEqual(result["points"], 10)
        self.assertTrue(result["exact_score"])
        self.assertFalse(result["advancing_correct"])

    # Exceção via faixa por fase: paga tier.exact_wrong_advancing (23), provando
    # que lê o campo configurado (exact=99, advancing_only=15 não influenciam).
    def test_tipo2_exact_wrong_advancing_per_phase(self):
        tier = SimpleNamespace(
            exact=99,
            advancing_goals=70,
            diff=60,
            loser_goals=50,
            advancing_only=15,
            exact_wrong_advancing=23,
        )
        bet = self._make_knockout_bet(1, 1, 1, 1, winner_real_id=1)
        result = calculate_bet_points(
            bet,
            self._make_scoring_config(),
            pool_type=POOL_TYPE_2,
            predicted_advancing_id=2,
            knockout_phase_scoring={"SF": tier},
            predicted_team_ids=(1, 2),
        )
        self.assertEqual(result["points"], 23)
        self.assertTrue(result["exact_score"])
        self.assertFalse(result["advancing_correct"])

    # R16+: par projetado difere do par real → exceção não dispara → 0.
    def test_tipo2_exact_wrong_advancing_projected_teams_differ(self):
        bet = self._make_knockout_bet(1, 1, 1, 1, winner_real_id=1)
        result = calculate_bet_points(
            bet,
            self._make_scoring_config(),
            pool_type=POOL_TYPE_2,
            predicted_advancing_id=2,
            predicted_team_ids=(1, 3),
        )
        self.assertEqual(result["points"], 0)
        self.assertFalse(result["advancing_correct"])

    # Retrocompat: sem predicted_team_ids → exceção não dispara → 0.
    def test_tipo2_exact_wrong_advancing_no_team_ids(self):
        bet = self._make_knockout_bet(1, 1, 1, 1, winner_real_id=1)
        result = calculate_bet_points(
            bet,
            self._make_scoring_config(),
            pool_type=POOL_TYPE_2,
            predicted_advancing_id=2,
        )
        self.assertEqual(result["points"], 0)


# ---------------------------------------------------------------------------
# Integration tests — Tipo 2 scoring coverage (require DB)
# ---------------------------------------------------------------------------


class Tipo2IntegrationExtraTest(TestCase):
    """E4 / E5: extra integration scenarios reusing RecalculateTipo2KnockoutTest fixture pattern."""

    def _build_fixture(self, *, fifa_id_base=8300, slug_suffix="e4"):
        """Build a minimal Tipo 2 pool with one decided knockout match and one group match.

        Returns a dict of all created objects for further use.
        """
        user = User.objects.create_user(
            username=f"t2extra_{slug_suffix}",
            email=f"t2extra_{slug_suffix}@example.com",
            password="pass",
        )
        competition = Competition.objects.create(fifa_id=fifa_id_base, name=f"Copa T2 Extra {slug_suffix}")
        season = Season.objects.create(
            fifa_id=fifa_id_base,
            competition=competition,
            name=f"T2 Extra {slug_suffix}",
            year=2026,
            start_date="2026-06-01",
            end_date="2026-07-30",
        )
        group_stage = Stage.objects.create(fifa_id=f"GRP-{slug_suffix}", season=season, name="Group Stage", order=10)
        ko_stage = Stage.objects.create(fifa_id=f"SF-{slug_suffix}", season=season, name="Semi-Final", order=50)

        code_a = f"A{slug_suffix[:3].upper()}"
        code_b = f"B{slug_suffix[:3].upper()}"
        team_a = Team.objects.create(
            fifa_id=f"{slug_suffix}-A", name=f"{slug_suffix} Alpha", name_norm=f"{slug_suffix}-alpha", code=code_a
        )
        team_b = Team.objects.create(
            fifa_id=f"{slug_suffix}-B", name=f"{slug_suffix} Beta", name_norm=f"{slug_suffix}-beta", code=code_b
        )

        past = timezone.now() - timezone.timedelta(hours=2)

        group_match = Match.objects.create(
            fifa_id=f"{slug_suffix}-GM1",
            season=season,
            stage=group_stage,
            match_number=fifa_id_base + 1,
            match_date_utc=past,
            match_date_local=past,
            match_date_brasilia=past,
            home_team=team_a,
            away_team=team_b,
            home_score=2,
            away_score=1,
            status=Match.STATUS_FINISHED,
        )
        ko_match = Match.objects.create(
            fifa_id=f"{slug_suffix}-KO1",
            season=season,
            stage=ko_stage,
            match_number=fifa_id_base + 2,
            match_date_utc=past - timezone.timedelta(hours=1),
            match_date_local=past - timezone.timedelta(hours=1),
            match_date_brasilia=past - timezone.timedelta(hours=1),
            home_team=team_a,
            away_team=team_b,
            home_score=2,
            away_score=1,
            winner=team_a,
            status=Match.STATUS_FINISHED,
        )

        pool = Pool.objects.create(
            name=f"Pool T2 Extra {slug_suffix}",
            slug=f"pool-t2-extra-{slug_suffix}",
            season=season,
            created_by=user,
            requires_payment=False,
            pool_type=POOL_TYPE_2,
        )

        return {
            "user": user,
            "season": season,
            "pool": pool,
            "team_a": team_a,
            "team_b": team_b,
            "group_match": group_match,
            "ko_match": ko_match,
        }

    def test_tipo2_no_team_advancement_bonus(self):
        """E4: Tipo 2 never sets team_advancement_bonus — that field is Tipo 1 only."""
        from src.pool.models import PoolBetScore

        ctx = self._build_fixture(fifa_id_base=8300, slug_suffix="e4")
        participant = PoolParticipant.objects.create(pool=ctx["pool"], user=ctx["user"], is_active=True)

        # Correct-advancer knockout bet (team_a wins → winner_pred=team_a)
        ko_bet = PoolBet.objects.create(
            participant=participant,
            match=ctx["ko_match"],
            home_score_pred=1,
            away_score_pred=0,
            winner_pred=ctx["team_a"],
            is_active=True,
        )

        recalculate_participant_scores(participant)

        score = PoolBetScore.objects.get(bet=ko_bet)
        self.assertGreater(score.points, 0, "Correct advancer should score > 0 (gate passed)")
        self.assertTrue(score.advancing_correct)
        self.assertFalse(score.team_advancement_bonus, "Tipo 2 must never award team_advancement_bonus")

    def test_tipo2_mixed_group_and_knockout(self):
        """E5: mixed group + knockout; group bet scores by tier, knockout gated by Tipo 2 identity."""
        from src.pool.models import PoolBetScore

        ctx = self._build_fixture(fifa_id_base=8400, slug_suffix="e5")
        pool = ctx["pool"]
        team_a = ctx["team_a"]
        team_b = ctx["team_b"]

        # Participant with correct advancer
        user_ok = User.objects.create_user(username="t2e5_ok", email="t2e5_ok@example.com", password="pass")
        participant_ok = PoolParticipant.objects.create(pool=pool, user=user_ok, is_active=True)

        # Group bet: guess 2×0, real 2×1 → winner correct + winner goals (home 2==2) → 18
        group_bet = PoolBet.objects.create(
            participant=participant_ok,
            match=ctx["group_match"],
            home_score_pred=2,
            away_score_pred=0,
            winner_pred=None,
            is_active=True,
        )
        # Knockout bet: correct advancer (team_a), guess 1×0 vs real 2×1 → advancing_only → 15
        ko_bet_ok = PoolBet.objects.create(
            participant=participant_ok,
            match=ctx["ko_match"],
            home_score_pred=1,
            away_score_pred=0,
            winner_pred=team_a,
            is_active=True,
        )

        # Participant with wrong advancer
        user_ko = User.objects.create_user(username="t2e5_ko", email="t2e5_ko@example.com", password="pass")
        participant_ko = PoolParticipant.objects.create(pool=pool, user=user_ko, is_active=True)
        ko_bet_ko = PoolBet.objects.create(
            participant=participant_ko,
            match=ctx["ko_match"],
            home_score_pred=2,
            away_score_pred=1,
            winner_pred=team_b,
            is_active=True,
        )

        recalculate_participant_scores(participant_ok)
        recalculate_participant_scores(participant_ko)

        group_score = PoolBetScore.objects.get(bet=group_bet)
        self.assertEqual(group_score.points, 18, "Group bet: winner + winner goals → 18")
        self.assertTrue(group_score.advancing_correct)

        ko_score_ok = PoolBetScore.objects.get(bet=ko_bet_ok)
        self.assertGreater(ko_score_ok.points, 0, "Correct-advancer knockout bet must score > 0")
        self.assertTrue(ko_score_ok.advancing_correct)

        ko_score_ko = PoolBetScore.objects.get(bet=ko_bet_ko)
        self.assertEqual(ko_score_ko.points, 0, "Wrong-advancer knockout bet must score 0")
        self.assertFalse(ko_score_ko.advancing_correct)


class Tipo2FullBracketEndToEndTest(TestCase):
    """H1: multi-round Tipo 2 bracket (QF → SF), 3 participants, per-match + aggregate assertions.

    Bracket layout
    ──────────────
    QF1 (#8501): Alpha(home) vs Beta(away)  — real 2-1, winner=Alpha
    QF2 (#8502): Gamma(home) vs Delta(away) — real 2-0, winner=Gamma
    SF  (#8503): W(QF1) vs W(QF2)           — NOT yet played (no score, winner=None)
    All QF/SF matches share one season/pool (Tipo 2).

    Participants
    ────────────
    P1 — QF1: Alpha 2-1 (exact), QF2: Gamma 2-0 (exact), SF: home 1-0 (not played → 0)
    P2 — QF1: Beta  0-2 (wrong advancer → 0), QF2: Gamma 2-0 (exact → 62), SF: not placed
    P3 — QF1: Alpha 3-1 (advancer ok, loser-goals → 35), QF2: Delta 0-1 (wrong → 0), SF: not placed

    Hand math (uses per-phase QF tier: exact=62, loser_goals=35)
    ─────────
    P1: QF1=62 (exact) + QF2=62 (exact) + SF=0 (no winner) = 124
    P2: QF1=0  (gate)  + QF2=62 (exact) + SF=0 (no bet)    = 62
    P3: QF1=35 (eliminated_goals) + QF2=0 (gate) + SF=0     = 35

    QF1 detail for P3: real 2-1 HOME, guess 3-1.
      Gate passes (predicted_advancing=Alpha=winner).
      is_exact=False; is_diff=(3-1)=2 vs (2-1)=1 → False; actual_dir=HOME.
      winner_goals: guess_home(3)==home(2)? No. loser_goals: guess_away(1)==away(1)? Yes → 35 (QF loser_goals tier).
    """

    def setUp(self):
        self.user_owner = User.objects.create_user(username="h1_owner", email="h1_owner@example.com", password="pass")
        self.user_p1 = User.objects.create_user(username="h1_p1", email="h1_p1@example.com", password="pass")
        self.user_p2 = User.objects.create_user(username="h1_p2", email="h1_p2@example.com", password="pass")
        self.user_p3 = User.objects.create_user(username="h1_p3", email="h1_p3@example.com", password="pass")

        competition = Competition.objects.create(fifa_id=8500, name="Copa H1 Bracket")
        self.season = Season.objects.create(
            fifa_id=8500,
            competition=competition,
            name="H1 Season",
            year=2026,
            start_date="2026-06-01",
            end_date="2026-07-30",
        )
        stage_qf = Stage.objects.create(fifa_id="QF-H1", season=self.season, name="Quarter-Final", order=50)
        stage_sf = Stage.objects.create(fifa_id="SF-H1", season=self.season, name="Semi-Final", order=51)

        self.alpha = Team.objects.create(fifa_id="H1-A", name="H1 Alpha", name_norm="h1-alpha", code="H1A")
        self.beta = Team.objects.create(fifa_id="H1-B", name="H1 Beta", name_norm="h1-beta", code="H1B")
        self.gamma = Team.objects.create(fifa_id="H1-C", name="H1 Gamma", name_norm="h1-gamma", code="H1C")
        self.delta = Team.objects.create(fifa_id="H1-D", name="H1 Delta", name_norm="h1-delta", code="H1D")

        past = timezone.now() - timezone.timedelta(hours=3)
        future = timezone.now() + timezone.timedelta(days=2)

        # QF1: Alpha 2-1 Beta → winner=Alpha (decided)
        self.qf1 = Match.objects.create(
            fifa_id="H1-QF1",
            season=self.season,
            stage=stage_qf,
            match_number=8501,
            match_date_utc=past,
            match_date_local=past,
            match_date_brasilia=past,
            home_team=self.alpha,
            away_team=self.beta,
            home_score=2,
            away_score=1,
            winner=self.alpha,
            status=Match.STATUS_FINISHED,
        )
        # QF2: Gamma 2-0 Delta → winner=Gamma (decided)
        self.qf2 = Match.objects.create(
            fifa_id="H1-QF2",
            season=self.season,
            stage=stage_qf,
            match_number=8502,
            match_date_utc=past,
            match_date_local=past,
            match_date_brasilia=past,
            home_team=self.gamma,
            away_team=self.delta,
            home_score=2,
            away_score=0,
            winner=self.gamma,
            status=Match.STATUS_FINISHED,
        )
        # SF: placeholder W8501 vs W8502 — NOT yet played
        self.sf = Match.objects.create(
            fifa_id="H1-SF1",
            season=self.season,
            stage=stage_sf,
            match_number=8503,
            match_date_utc=future,
            match_date_local=future,
            match_date_brasilia=future,
            home_team=None,
            away_team=None,
            home_placeholder="W8501",
            away_placeholder="W8502",
        )

        self.pool = Pool.objects.create(
            name="Pool H1",
            slug="pool-h1-bracket",
            season=self.season,
            created_by=self.user_owner,
            requires_payment=False,
            pool_type=POOL_TYPE_2,
        )

        # --- P1: exact on both QFs, SF not played ---
        self.p1 = PoolParticipant.objects.create(pool=self.pool, user=self.user_p1, is_active=True)
        self.p1_qf1_bet = PoolBet.objects.create(
            participant=self.p1,
            match=self.qf1,
            home_score_pred=2,
            away_score_pred=1,
            winner_pred=self.alpha,
            is_active=True,
        )
        self.p1_qf2_bet = PoolBet.objects.create(
            participant=self.p1,
            match=self.qf2,
            home_score_pred=2,
            away_score_pred=0,
            winner_pred=self.gamma,
            is_active=True,
        )
        # SF: predicts home wins 1-0; walk resolves home=Alpha (from QF1 winner_pred).
        # winner_pred=None forces the walk; projected advancer = Alpha.
        # SF not played → gate fails (winner_id=None) → 0.
        self.p1_sf_bet = PoolBet.objects.create(
            participant=self.p1,
            match=self.sf,
            home_score_pred=1,
            away_score_pred=0,
            winner_pred=None,
            is_active=True,
        )

        # --- P2: wrong on QF1, exact on QF2, no SF bet ---
        self.p2 = PoolParticipant.objects.create(pool=self.pool, user=self.user_p2, is_active=True)
        self.p2_qf1_bet = PoolBet.objects.create(
            participant=self.p2,
            match=self.qf1,
            home_score_pred=0,
            away_score_pred=2,
            winner_pred=self.beta,
            is_active=True,
        )
        self.p2_qf2_bet = PoolBet.objects.create(
            participant=self.p2,
            match=self.qf2,
            home_score_pred=2,
            away_score_pred=0,
            winner_pred=self.gamma,
            is_active=True,
        )

        # --- P3: correct advancer QF1 (eliminated_goals tier), wrong advancer QF2 ---
        self.p3 = PoolParticipant.objects.create(pool=self.pool, user=self.user_p3, is_active=True)
        self.p3_qf1_bet = PoolBet.objects.create(
            participant=self.p3,
            match=self.qf1,
            home_score_pred=3,
            away_score_pred=1,
            winner_pred=self.alpha,
            is_active=True,
        )
        self.p3_qf2_bet = PoolBet.objects.create(
            participant=self.p3,
            match=self.qf2,
            home_score_pred=0,
            away_score_pred=1,
            winner_pred=self.delta,
            is_active=True,
        )

    def test_h1_p1_per_match_and_total(self):
        """P1: QF1=62 (exact), QF2=62 (exact), SF=0 (not played) → total=124."""
        from src.pool.models import PoolBetScore

        recalculate_participant_scores(self.p1)
        self.p1.refresh_from_db()

        qf1_score = PoolBetScore.objects.get(bet=self.p1_qf1_bet)
        self.assertEqual(qf1_score.points, 62, "P1 QF1: exact score + correct advancer → 62 (QF exact tier)")
        self.assertTrue(qf1_score.exact_score)
        self.assertTrue(qf1_score.advancing_correct)

        qf2_score = PoolBetScore.objects.get(bet=self.p1_qf2_bet)
        self.assertEqual(qf2_score.points, 62, "P1 QF2: exact score + correct advancer → 62 (QF exact tier)")
        self.assertTrue(qf2_score.exact_score)

        sf_score = PoolBetScore.objects.get(bet=self.p1_sf_bet)
        self.assertEqual(sf_score.points, 0, "P1 SF: match not decided yet → 0")
        self.assertFalse(sf_score.advancing_correct)

        self.assertEqual(self.p1.knockout_points, 124, "P1 total knockout points: 62+62+0=124")

    def test_h1_p2_per_match_and_total(self):
        """P2: QF1=0 (wrong advancer), QF2=62 (exact), no SF → total=62."""
        from src.pool.models import PoolBetScore

        recalculate_participant_scores(self.p2)
        self.p2.refresh_from_db()

        qf1_score = PoolBetScore.objects.get(bet=self.p2_qf1_bet)
        self.assertEqual(qf1_score.points, 0, "P2 QF1: wrong advancer (Beta≠Alpha) → 0")
        self.assertFalse(qf1_score.advancing_correct)

        qf2_score = PoolBetScore.objects.get(bet=self.p2_qf2_bet)
        self.assertEqual(qf2_score.points, 62, "P2 QF2: exact 2-0 + correct advancer → 62 (QF exact tier)")
        self.assertTrue(qf2_score.advancing_correct)

        self.assertEqual(self.p2.knockout_points, 62, "P2 total knockout points: 0+62=62")

    def test_h1_p3_per_match_and_total(self):
        """P3: QF1=35 (eliminated_goals QF tier), QF2=0 (wrong advancer) → total=35."""
        from src.pool.models import PoolBetScore

        recalculate_participant_scores(self.p3)
        self.p3.refresh_from_db()

        # QF1: gate passes (Alpha). real 2-1, guess 3-1.
        # winner_goals: guess_home(3)!=home(2). loser_goals: guess_away(1)==away(1) → QF loser_goals=35.
        qf1_score = PoolBetScore.objects.get(bet=self.p3_qf1_bet)
        self.assertEqual(qf1_score.points, 35, "P3 QF1: correct advancer + loser goals → 35 (QF loser_goals tier)")
        self.assertTrue(qf1_score.advancing_correct)
        self.assertFalse(qf1_score.advancing_goals_correct)
        self.assertFalse(qf1_score.diff_correct)
        self.assertTrue(qf1_score.eliminated_goals_correct)

        # QF2: gate fails (Delta≠Gamma).
        qf2_score = PoolBetScore.objects.get(bet=self.p3_qf2_bet)
        self.assertEqual(qf2_score.points, 0, "P3 QF2: wrong advancer (Delta≠Gamma) → 0")
        self.assertFalse(qf2_score.advancing_correct)

        self.assertEqual(self.p3.knockout_points, 35, "P3 total knockout points: 35+0=35")

    def test_h1_ranking_order(self):
        """H1 combined: P1 (124) > P2 (62) > P3 (35) after all recalculations."""
        recalculate_participant_scores(self.p1)
        recalculate_participant_scores(self.p2)
        recalculate_participant_scores(self.p3)
        self.p1.refresh_from_db()
        self.p2.refresh_from_db()
        self.p3.refresh_from_db()
        self.assertGreater(self.p1.knockout_points, self.p2.knockout_points)
        self.assertGreater(self.p2.knockout_points, self.p3.knockout_points)


class KnockoutPhaseScoringSeedTest(TestCase):
    """get_scoring_config garante as 6 faixas de fase com os defaults oficiais."""

    def _make_minimal_pool(self):
        from src.football.models import Competition, Season
        from src.pool.models import POOL_TYPE_2, Pool

        user = User.objects.create_user(username="kps", email="kps@example.com", password="pass")
        competition = Competition.objects.create(fifa_id=9100, name="KPS Cup")
        season = Season.objects.create(
            fifa_id=9100,
            competition=competition,
            name="KPS Season",
            year=2026,
            start_date="2026-06-01",
            end_date="2026-07-30",
        )
        return Pool.objects.create(
            name="KPS Pool",
            slug="kps-pool",
            season=season,
            created_by=user,
            requires_payment=False,
            pool_type=POOL_TYPE_2,
        )

    def test_get_scoring_config_seeds_six_phase_rows(self):
        from src.pool.models import KNOCKOUT_PHASE_DEFAULTS

        pool = self._make_minimal_pool()
        config = pool.get_scoring_config()

        rows = {row.phase_key: row for row in config.knockout_phases.all()}
        self.assertEqual(set(rows), set(KNOCKOUT_PHASE_DEFAULTS))

        sf = rows["SF"]
        self.assertEqual(sf.exact, 78)
        self.assertEqual(sf.advancing_goals, 59)
        self.assertEqual(sf.diff, 50)
        self.assertEqual(sf.loser_goals, 44)
        self.assertEqual(sf.advancing_only, 40)
        self.assertEqual(sf.exact_wrong_advancing, 38)

        final = rows["FINAL"]
        self.assertEqual(final.exact, 95)
        self.assertEqual(final.advancing_only, 48)
        self.assertEqual(final.exact_wrong_advancing, 47)


class RecalculateTipo2PhaseTierTest(TestCase):
    """recalculate_participant_scores usa a faixa da fase (SF) no Tipo 2."""

    def _build_sf_pool(self):
        user = User.objects.create_user(username="t2sf", email="t2sf@example.com", password="pass")
        competition = Competition.objects.create(fifa_id=8201, name="Copa T2 SF")
        season = Season.objects.create(
            fifa_id=8201,
            competition=competition,
            name="T2 SF Season",
            year=2026,
            start_date="2026-06-01",
            end_date="2026-07-30",
        )
        stage_sf = Stage.objects.create(fifa_id="SF-PT", season=season, name="SF", order=80)
        team_a = Team.objects.create(fifa_id="PT-A", name="PT Alpha", name_norm="pt-alpha", code="PTA")
        team_b = Team.objects.create(fifa_id="PT-B", name="PT Beta", name_norm="pt-beta", code="PTB")

        past = timezone.now() - timezone.timedelta(hours=2)
        match = Match.objects.create(
            fifa_id="PT-SF-1",
            season=season,
            stage=stage_sf,
            match_number=100,
            match_date_utc=past,
            match_date_local=past,
            match_date_brasilia=past,
            home_team=team_a,
            away_team=team_b,
            home_score=2,
            away_score=0,
            winner=team_a,
            status=Match.STATUS_FINISHED,
        )
        pool = Pool.objects.create(
            name="Pool T2 SF",
            slug="pool-t2-sf",
            season=season,
            created_by=user,
            requires_payment=False,
            pool_type=POOL_TYPE_2,
        )
        participant = PoolParticipant.objects.create(pool=pool, user=user, is_active=True)
        # Placar exato 2x0, classificado team_a (== real winner) → SF exact = 78
        bet = PoolBet.objects.create(
            participant=participant,
            match=match,
            home_score_pred=2,
            away_score_pred=0,
            winner_pred=team_a,
            is_active=True,
        )
        return {"participant": participant, "bet": bet}

    def test_recalculate_uses_sf_exact_tier(self):
        from src.pool.models import PoolBetScore

        ctx = self._build_sf_pool()
        recalculate_participant_scores(ctx["participant"])
        score = PoolBetScore.objects.get(bet=ctx["bet"])
        self.assertEqual(score.points, 78)
        self.assertTrue(score.exact_score)
        self.assertTrue(score.advancing_correct)


class MatchMaxPointsPhaseTest(SimpleTestCase):
    def test_phase_map_overrides_flat_knockout_max(self):
        from src.rankings.services.dashboard import _match_max_points

        scoring_config = SimpleNamespace(group_exact_score=25, knockout_exact_and_advancing=35)
        final_stage = SimpleNamespace(name="Final")
        match = SimpleNamespace(stage=final_stage, group_id=None)
        phase_max_map = {"FINAL": 95, "R32": 40}

        self.assertEqual(_match_max_points(match, scoring_config, phase_max_map), 95)

    def test_no_phase_map_uses_flat(self):
        from src.rankings.services.dashboard import _match_max_points

        scoring_config = SimpleNamespace(group_exact_score=25, knockout_exact_and_advancing=35)
        final_stage = SimpleNamespace(name="Final")
        match = SimpleNamespace(stage=final_stage, group_id=None)

        self.assertEqual(_match_max_points(match, scoring_config), 35)


class Tipo1TeamAdvancementBonusWithoutWinnerPredTest(TestCase):
    """Tipo 1: bônus de classificado deve sair mesmo com winner_pred=None.

    Mata-mata é palpitado projetado (sem times reais), então clean() não deriva
    winner_pred para palpites decisivos — o campo fica None. O bônus de avanço
    precisa resolver o classificado palpitado pelo placar/projeção, não pelo
    winner_pred cru.
    """

    def _build(self, *, slug_suffix, fifa_base):
        from src.pool.services.rules import POOL_TYPE_1

        user = User.objects.create_user(
            username=f"t1adv_{slug_suffix}", email=f"t1adv_{slug_suffix}@example.com", password="pass"
        )
        competition = Competition.objects.create(fifa_id=fifa_base, name=f"Copa T1 Adv {slug_suffix}")
        season = Season.objects.create(
            fifa_id=fifa_base,
            competition=competition,
            name=f"T1 Adv Season {slug_suffix}",
            year=2026,
            start_date="2026-06-01",
            end_date="2026-07-30",
        )
        stage_r32 = Stage.objects.create(fifa_id=f"R32-T1ADV-{slug_suffix}", season=season, name="R32", order=50)
        team_a = Team.objects.create(
            fifa_id=f"T1ADV-A-{slug_suffix}", name="T1 Alpha", name_norm=f"t1adv-alpha-{slug_suffix}", code="T1A"
        )
        team_b = Team.objects.create(
            fifa_id=f"T1ADV-B-{slug_suffix}", name="T1 Beta", name_norm=f"t1adv-beta-{slug_suffix}", code="T1B"
        )
        past = timezone.now() - timezone.timedelta(hours=2)
        match = Match.objects.create(
            fifa_id=f"T1ADV-R32-{slug_suffix}",
            season=season,
            stage=stage_r32,
            match_number=910,
            match_date_utc=past,
            match_date_local=past,
            match_date_brasilia=past,
            home_team=team_a,
            away_team=team_b,
            home_score=2,
            away_score=1,
            winner=team_a,
            status=Match.STATUS_FINISHED,
        )
        pool = Pool.objects.create(
            name=f"Pool T1 Adv {slug_suffix}",
            slug=f"pool-t1-adv-{slug_suffix}",
            season=season,
            created_by=user,
            requires_payment=False,
            pool_type=POOL_TYPE_1,
        )
        participant = PoolParticipant.objects.create(pool=pool, user=user, is_active=True)
        # Palpite decisivo 2-1 acertando o vencedor real (team_a), mas winner_pred=None
        # — exatamente o estado salvo quando o jogo foi palpitado projetado.
        bet = PoolBet.objects.create(
            participant=participant,
            match=match,
            home_score_pred=2,
            away_score_pred=1,
            winner_pred=None,
            is_active=True,
        )
        return SimpleNamespace(pool=pool, season=season, participant=participant, match=match, bet=bet, team_a=team_a)

    def test_recalc_awards_bonus_without_winner_pred(self):
        from src.pool.models import PoolBetScore

        ctx = self._build(slug_suffix="recalc", fifa_base=9100)
        bonus = ctx.pool.get_scoring_config().knockout_team_advancement_bonus

        recalculate_participant_scores(ctx.participant)
        ctx.participant.refresh_from_db()

        score = PoolBetScore.objects.get(bet=ctx.bet)
        self.assertTrue(
            score.team_advancement_bonus,
            "Palpite decisivo acertando o classificado deve dar bônus mesmo sem winner_pred",
        )
        self.assertEqual(ctx.participant.knockout_points, score.points + bonus)

    def test_asof_awards_bonus_without_winner_pred(self):
        from src.pool.services.rules import POOL_TYPE_1

        ctx = self._build(slug_suffix="asof", fifa_base=9200)
        scoring_config = ctx.pool.get_scoring_config()
        bonus = scoring_config.knockout_team_advancement_bonus
        positional = calculate_bet_points(ctx.bet, scoring_config=scoring_config, pool_type=POOL_TYPE_1)["points"]

        rows = compute_asof_standings(
            ctx.pool,
            {ctx.match.id},
            scoring_config,
            ctx.pool.get_official_results(),
        )
        row = next(r for r in rows if r.participant.id == ctx.participant.id)
        self.assertEqual(
            row.knockout_points,
            positional + bonus,
            "As-of standings devem incluir o bônus de avanço mesmo sem winner_pred",
        )


class ResolveKnockoutTeamsAndAdvancingTest(TestCase):
    """resolve_knockout_teams_and_advancing devolve times e classificado num walk."""

    def test_r32_real_teams_and_advancing(self):
        from src.pool.services.context_builder import resolve_knockout_teams_and_advancing

        user = User.objects.create_user(username="rkta", email="rkta@example.com", password="pass")
        competition = Competition.objects.create(fifa_id=8500, name="Copa RKTA")
        season = Season.objects.create(
            fifa_id=8500,
            competition=competition,
            name="RKTA",
            year=2026,
            start_date="2026-06-01",
            end_date="2026-07-30",
        )
        ko_stage = Stage.objects.create(fifa_id="R32-rkta", season=season, name="32 Avos", order=40)
        team_a = Team.objects.create(fifa_id="rkta-A", name="RKTA A", name_norm="rkta-a", code="RKA")
        team_b = Team.objects.create(fifa_id="rkta-B", name="RKTA B", name_norm="rkta-b", code="RKB")
        past = timezone.now() - timezone.timedelta(hours=2)
        ko_match = Match.objects.create(
            fifa_id="rkta-KO",
            season=season,
            stage=ko_stage,
            match_number=8501,
            match_date_utc=past,
            match_date_local=past,
            match_date_brasilia=past,
            home_team=team_a,
            away_team=team_b,
            home_score=1,
            away_score=1,
            winner=team_a,
            status=Match.STATUS_FINISHED,
        )
        pool = Pool.objects.create(
            name="Pool RKTA",
            slug="pool-rkta",
            season=season,
            created_by=user,
            requires_payment=False,
            pool_type=POOL_TYPE_2,
        )
        participant = PoolParticipant.objects.create(pool=pool, user=user, is_active=True)
        PoolBet.objects.create(
            participant=participant,
            match=ko_match,
            home_score_pred=1,
            away_score_pred=1,
            winner_pred=team_b,
            is_active=True,
        )

        teams_by_match, advancing_by_match = resolve_knockout_teams_and_advancing(
            participant=participant,
            matches=[ko_match],
            season=season,
        )

        self.assertEqual(teams_by_match[ko_match.id], (team_a, team_b))
        self.assertEqual(advancing_by_match[ko_match.id], team_b.id)

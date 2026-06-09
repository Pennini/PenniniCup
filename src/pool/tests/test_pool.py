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
from src.football.models import AssignThird, Competition, Group, Match, Player, Season, Stage, Team
from src.payments.models import Payment
from src.pool.models import Pool, PoolBet, PoolParticipant, PoolParticipantStanding, PoolProjectionRecalc
from src.pool.services.context_builder import (
    _build_projected_groups_from_rows,
    _build_third_rows_from_rows,
    _build_winners_map,
    _infer_advancing_team,
    _infer_losing_team,
    _make_pairs,
    _projection_is_stale_from_prefetched,
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
from src.pool.services.projection_queue import MAX_ATTEMPTS, process_next_projection_recalc_job
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

    def test_knockout_non_draw_exact_score(self):
        # Non-empate exact placar always implies winner correct in positional scoring.
        # Tipo 1 and Tipo 2 behave identically; winner_pred is ignored.
        bet = self._make_knockout_bet(2, 1, 2, 1, winner_real_id=1, winner_pred_id=2)
        result = calculate_bet_points(bet, self._make_scoring_config(), pool_type=POOL_TYPE_2)
        self.assertEqual(result["points"], 35)
        self.assertTrue(result["exact_score"])
        self.assertTrue(result["advancing_correct"])

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

    def test_knockout_winner_pred_ignored_in_non_draw(self):
        # Em palpite nao-empate, winner_pred eh irrelevante: so importa
        # placar + posicao. Pool type tambem nao afeta.
        bet_t1 = self._make_knockout_bet(2, 0, 2, 1, winner_real_id=1, winner_pred_id=2)
        bet_t2 = self._make_knockout_bet(2, 0, 2, 1, winner_real_id=1, winner_pred_id=2)
        r1 = calculate_bet_points(bet_t1, self._make_scoring_config())
        r2 = calculate_bet_points(bet_t2, self._make_scoring_config(), pool_type=POOL_TYPE_2)
        self.assertEqual(r1["points"], 25)
        self.assertEqual(r2["points"], 25)
        self.assertTrue(r1["advancing_correct"])
        self.assertTrue(r2["advancing_correct"])


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

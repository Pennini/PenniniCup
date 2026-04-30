from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from src.accounts.models import InviteToken
from src.football.models import AssignThird, Competition, Group, Match, Player, Season, Stage, Team
from src.payments.models import Payment
from src.pool.models import Pool, PoolBet, PoolParticipant, PoolProjectionRecalc
from src.pool.services.projection import (
    load_assign_third_map,
    projected_group_standings,
    resolve_knockout_placeholder_team,
)
from src.pool.services.projection_queue import MAX_ATTEMPTS, process_next_projection_recalc_job
from src.pool.services.ranking import recalculate_participant_scores

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
        self.pool.scoring_config.group_exact_score_points = 4
        self.pool.scoring_config.group_winner_or_draw_points = 6
        self.pool.scoring_config.group_one_team_score_points = 1
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

        # 10 pontos do placar exato em grupos (6 vencedor + 4 exato) + 9 + 7 + 5 de bonus
        self.assertEqual(self.participant.group_points, 10)
        self.assertEqual(self.participant.bonus_points, 21)
        self.assertEqual(self.participant.total_points, 31)
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

    def test_bets_tab_orders_matches_by_match_number(self):
        later = timezone.now() + timezone.timedelta(days=10)
        early_number_match = Match.objects.create(
            fifa_id="GM4-ORDER",
            season=self.season,
            stage=self.stage_group,
            group=self.group_a,
            match_number=10,
            match_date_utc=later,
            match_date_local=later,
            match_date_brasilia=later,
            home_team=self.team_a,
            away_team=self.team_b,
        )

        self.client.force_login(self.user)
        response = self.client.get(reverse("pool:detail", kwargs={"slug": self.pool.slug}))
        self.assertEqual(response.status_code, 200)

        group_numbers = [row["match"].match_number for row in response.context["group_rows"]]
        self.assertEqual(group_numbers, sorted(group_numbers))
        self.assertIn(early_number_match.match_number, group_numbers)

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

    def test_knockout_draw_without_winner_is_saved_inactive(self):
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

    def test_bulk_save_is_atomic_for_entire_batch(self):
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

        self.participant.refresh_from_db()
        self.assertIsNone(self.participant.top_scorer_pred_id)

        group_bet = PoolBet.objects.get(participant=self.participant, match=self.group_match)
        self.assertIsNone(group_bet.home_score_pred)
        self.assertIsNone(group_bet.away_score_pred)
        self.assertFalse(group_bet.is_active)


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

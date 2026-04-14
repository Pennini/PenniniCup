from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from src.accounts.models import InviteToken
from src.football.models import AssignThird, Competition, Group, Match, Player, Season, Stage, Team
from src.pool.models import Pool, PoolBet, PoolParticipant, PoolProjectionRecalc
from src.pool.services.projection import (
    load_assign_third_map,
    projected_group_standings,
    resolve_knockout_placeholder_team,
)
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

        detail_response = self.client.get(
            reverse(
                "pool:detail",
                kwargs={
                    "slug": self.pool.slug,
                },
            )
        )
        self.assertEqual(detail_response.status_code, 200)

        projected_groups = detail_response.context["projected_groups"]
        self.assertEqual(len(projected_groups), 1)
        standings = projected_groups[0]["standings"]
        self.assertEqual(len(standings), 2)
        self.assertEqual(standings[0].points, 1)
        self.assertEqual(standings[1].points, 1)


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

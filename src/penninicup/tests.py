import logging
from unittest.mock import patch
from uuid import UUID, uuid4

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from src.accounts.models import UserProfile
from src.common.logging_filters import RequestIdFilter
from src.common.utils.request_id import clear_request_id, set_request_id
from src.football.models import Competition, Group, Match, Season, Stage, Team
from src.payments.models import Payment
from src.pool.models import Pool, PoolBet, PoolParticipant

User = get_user_model()


class RulesPageTest(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="owner-rules",
            email="owner-rules@example.com",
            password="123456Aa!",
        )
        self.client.force_login(self.owner)
        competition = Competition.objects.create(fifa_id=991, name="Copa Rules")
        self.season = Season.objects.create(
            fifa_id=991,
            competition=competition,
            name="Temporada Rules",
            year=2026,
            start_date="2026-06-01",
            end_date="2026-07-30",
        )

        self.pool_a = Pool.objects.create(
            name="Pool Regras A",
            slug="pool-regras-a",
            season=self.season,
            created_by=self.owner,
            requires_payment=False,
        )
        self.pool_b = Pool.objects.create(
            name="Pool Regras B",
            slug="pool-regras-b",
            season=self.season,
            created_by=self.owner,
            requires_payment=False,
        )

        config_a = self.pool_a.get_scoring_config()
        config_a.group_exact_score_points = 13
        config_a.knockout_winner_advancing_points = 11
        config_a.knockout_exact_score_points = 7
        config_a.save()

    def test_rules_page_loads_and_uses_default_pool(self):
        response = self.client.get(reverse("penninicup:rules"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Pool Regras A")
        self.assertContains(response, "Acertar placar exato: +13")

    def test_rules_page_respects_selected_pool(self):
        config_b = self.pool_b.get_scoring_config()
        config_b.group_exact_score_points = 21
        config_b.save()

        response = self.client.get(reverse("penninicup:rules"), data={"pool": self.pool_b.slug})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Pool Regras B")
        self.assertContains(response, "21")

    def test_rules_page_shows_prize_amounts_and_total_collected(self):
        player_1 = User.objects.create_user(username="pay-user-1", email="pay1@example.com", password="123456Aa!")
        player_2 = User.objects.create_user(username="pay-user-2", email="pay2@example.com", password="123456Aa!")

        PoolParticipant.objects.create(pool=self.pool_a, user=player_1, is_active=True)
        PoolParticipant.objects.create(pool=self.pool_a, user=player_2, is_active=True)

        Payment.objects.create(user=player_1, pool=self.pool_a, status="approved", amount=100, amount_received=100)
        Payment.objects.create(user=player_2, pool=self.pool_a, status="approved", amount=50, amount_received=50)

        refresh_response = self.client.post(reverse("penninicup:rules"), data={"pool": self.pool_a.slug})
        self.assertEqual(refresh_response.status_code, 302)

        response = self.client.get(reverse("penninicup:rules"), data={"pool": self.pool_a.slug})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Total para premiação: R$ 142,50")
        self.assertContains(response, "Taxa do administrador: 5,00% (R$ 7,50)")
        self.assertContains(response, "R$ 99,75")
        self.assertContains(response, "R$ 28,50")
        self.assertContains(response, "R$ 14,25")

    @patch("src.penninicup.views.Pool.refresh_prize_distribution")
    def test_rules_get_does_not_recalculate_prize_distribution(self, refresh_mock):
        response = self.client.get(reverse("penninicup:rules"), data={"pool": self.pool_a.slug})
        self.assertEqual(response.status_code, 200)
        refresh_mock.assert_not_called()

    @patch("src.penninicup.views.Pool.refresh_prize_distribution")
    def test_rules_post_recalculates_prize_distribution(self, refresh_mock):
        response = self.client.post(reverse("penninicup:rules"), data={"pool": self.pool_a.slug})
        self.assertEqual(response.status_code, 302)
        refresh_mock.assert_called_once_with(save=True)


class ProfilePageTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="profile-user",
            email="profile-user@example.com",
            password="123456Aa!",
        )

        competition = Competition.objects.create(fifa_id=992, name="Copa Perfil")
        self.season = Season.objects.create(
            fifa_id=992,
            competition=competition,
            name="Temporada Perfil",
            year=2026,
            start_date="2026-01-01",
            end_date="2026-12-31",
        )
        stage = Stage.objects.create(fifa_id="STAGE-PROFILE", season=self.season, name="Fase de Grupos", order=999)
        group = Group.objects.create(fifa_id="GROUP-PROFILE", stage=stage, name="A")
        self.team = Team.objects.create(
            fifa_id="TEAM-PROFILE",
            name="Time Perfil",
            name_norm="time perfil",
            code="TPF",
            group=group,
        )

        self.pool = Pool.objects.create(
            name="Pool Perfil",
            slug="pool-perfil",
            season=self.season,
            created_by=self.user,
            requires_payment=False,
        )
        self.participant = PoolParticipant.objects.create(pool=self.pool, user=self.user, is_active=True)

        self.other_user = User.objects.create_user(
            username="other-user",
            email="other-user@example.com",
            password="123456Aa!",
        )
        self.other_participant = PoolParticipant.objects.create(pool=self.pool, user=self.other_user, is_active=True)

        self.match = Match.objects.create(
            fifa_id="MATCH-PROFILE-1",
            season=self.season,
            stage=stage,
            group=group,
            match_number=1,
            match_date_utc=timezone.now() + timezone.timedelta(days=3),
            match_date_local=timezone.now() + timezone.timedelta(days=3),
            match_date_brasilia=timezone.now() + timezone.timedelta(days=3),
            stadium=None,
            home_team=self.team,
            away_team=self.team,
        )
        PoolBet.objects.create(
            participant=self.other_participant,
            match=self.match,
            home_score_pred=1,
            away_score_pred=0,
            is_active=True,
        )

    def test_profile_requires_authentication(self):
        response = self.client.get(reverse("penninicup:profile"))
        self.assertEqual(response.status_code, 302)

    def test_profile_page_loads_for_authenticated_user(self):
        self.client.login(username="profile-user", password="123456Aa!")
        response = self.client.get(reverse("penninicup:profile"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Meu Perfil")
        self.assertContains(response, "Pool Perfil")

    def test_profile_post_updates_optional_fields(self):
        self.client.login(username="profile-user", password="123456Aa!")
        response = self.client.post(
            reverse("penninicup:profile"),
            data={
                "favorite_team": "Meu Time",
                "world_cup_team": str(self.team.id),
                "selected_pool": self.pool.slug,
                "active_tab": "bets",
            },
        )
        self.assertEqual(response.status_code, 302)

        profile = UserProfile.objects.get(user=self.user)
        self.assertEqual(profile.favorite_team, "Meu Time")
        self.assertEqual(profile.world_cup_team_id, self.team.id)

    def test_profile_post_updates_profile_image(self):
        self.client.login(username="profile-user", password="123456Aa!")
        uploaded = SimpleUploadedFile("avatar.png", b"fake-image-content", content_type="image/png")

        response = self.client.post(
            reverse("penninicup:profile"),
            data={
                "favorite_team": "",
                "world_cup_team": "",
                "selected_pool": self.pool.slug,
                "active_tab": "bets",
                "profile_image": uploaded,
            },
        )
        self.assertEqual(response.status_code, 302)

        profile = UserProfile.objects.get(user=self.user)
        self.assertTrue(profile.profile_image.name.startswith("profiles/"))

    def test_profile_invalid_pool_query_ignores_selection(self):
        self.client.login(username="profile-user", password="123456Aa!")
        response = self.client.get(reverse("penninicup:profile"), data={"pool": "nao-existe"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Pool Perfil")

    def test_profile_invalid_tab_redirects_to_bets(self):
        self.client.login(username="profile-user", password="123456Aa!")
        response = self.client.get(
            reverse("penninicup:profile"),
            data={"pool": self.pool.slug, "tab": "invalida"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("tab=bets", response.url)

    def test_other_profile_hides_predictions_before_first_match(self):
        self.client.login(username="profile-user", password="123456Aa!")
        response = self.client.get(
            reverse("penninicup:profile-user", kwargs={"username": self.other_user.username}),
            data={"pool": self.pool.slug},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ficarao visiveis apos o inicio da Copa")
        self.assertNotContains(response, "Palpite:")

    def test_other_profile_shows_predictions_after_first_match_starts(self):
        self.match.match_date_utc = timezone.now() - timezone.timedelta(days=1)
        self.match.match_date_local = timezone.now() - timezone.timedelta(days=1)
        self.match.match_date_brasilia = timezone.now() - timezone.timedelta(days=1)
        self.match.save(update_fields=["match_date_utc", "match_date_local", "match_date_brasilia"])

        self.client.login(username="profile-user", password="123456Aa!")
        response = self.client.get(
            reverse("penninicup:profile-user", kwargs={"username": self.other_user.username}),
            data={"pool": self.pool.slug},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Palpite:")


class RequestUUIDMiddlewareTest(SimpleTestCase):
    def test_response_has_x_request_uuid_header(self):
        response = self.client.get(reverse("penninicup:index"))

        request_id = response.headers.get("X-Request-UUID")
        self.assertIsNotNone(request_id)
        UUID(request_id)

    def test_valid_incoming_request_uuid_is_reused(self):
        incoming_id = str(uuid4())
        response = self.client.get(reverse("penninicup:index"), HTTP_X_REQUEST_UUID=incoming_id)

        self.assertEqual(response.headers.get("X-Request-UUID"), incoming_id)


class RequestIdFilterTest(SimpleTestCase):
    def tearDown(self):
        clear_request_id()

    def test_filter_injects_current_request_id(self):
        expected_id = str(uuid4())
        set_request_id(expected_id)
        record = self._build_log_record()

        result = RequestIdFilter().filter(record)

        self.assertTrue(result)
        self.assertEqual(record.request_id, expected_id)

    def test_filter_uses_fallback_when_request_context_missing(self):
        clear_request_id()
        record = self._build_log_record()

        result = RequestIdFilter().filter(record)

        self.assertTrue(result)
        self.assertEqual(record.request_id, "-")

    def _build_log_record(self):
        return logging.LogRecord(
            name="test",
            level=20,
            pathname=__file__,
            lineno=1,
            msg="log message",
            args=(),
            exc_info=None,
        )

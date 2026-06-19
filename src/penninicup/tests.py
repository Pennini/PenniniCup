import logging
from io import BytesIO
from unittest.mock import patch
from uuid import UUID, uuid4

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone
from PIL import Image

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

        PoolParticipant.objects.create(pool=self.pool_a, user=self.owner, is_active=True)
        PoolParticipant.objects.create(pool=self.pool_b, user=self.owner, is_active=True)

        config_a = self.pool_a.get_scoring_config()
        config_a.group_exact_score = 13
        config_a.save()

    def test_rules_page_loads_and_uses_default_pool(self):
        response = self.client.get(reverse("penninicup:rules"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Pool Regras A")
        self.assertContains(response, "13 pts")

    def test_rules_page_respects_selected_pool(self):
        config_b = self.pool_b.get_scoring_config()
        config_b.group_exact_score = 21
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
        self.assertContains(response, "Total: R$ 142,50")
        self.assertContains(response, "Taxa do administrador: 5,00% (R$ 7,50)")
        self.assertContains(response, "R$ 97,50")
        self.assertContains(response, "R$ 30,00")
        self.assertContains(response, "R$ 15,00")

    @patch("src.pool.models.Pool.refresh_prize_distribution")
    def test_rules_get_recalculates_prize_distribution(self, refresh_mock):
        response = self.client.get(reverse("penninicup:rules"), data={"pool": self.pool_a.slug})
        self.assertEqual(response.status_code, 200)
        refresh_mock.assert_called_once_with(save=True)

    @patch("src.pool.models.Pool.refresh_prize_distribution")
    def test_rules_post_recalculates_prize_distribution(self, refresh_mock):
        response = self.client.post(reverse("penninicup:rules"), data={"pool": self.pool_a.slug})
        self.assertEqual(response.status_code, 302)
        refresh_mock.assert_called_once_with(save=True)


class HomeShortcutsTest(TestCase):
    """Homepage shortcuts must target the slugless tab views (which wire the
    pool selector); otherwise the bolão selectbox never appears on arrival.
    """

    def setUp(self):
        self.user = User.objects.create_user(username="home-user", email="home@example.com", password="123456Aa!")
        self.client.force_login(self.user)
        competition = Competition.objects.create(fifa_id=992, name="Copa Home")
        season = Season.objects.create(
            fifa_id=992,
            competition=competition,
            name="Temporada Home",
            year=2026,
            start_date="2026-06-01",
            end_date="2026-07-30",
        )
        self.pool = Pool.objects.create(
            name="Pool Home",
            slug="pool-home",
            season=season,
            created_by=self.user,
            requires_payment=False,
        )
        PoolParticipant.objects.create(pool=self.pool, user=self.user, is_active=True)

    def test_shortcuts_point_to_slugless_tab_views(self):
        response = self.client.get(reverse("penninicup:index"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"{reverse('pool:ranking-tab')}?pool={self.pool.slug}")
        self.assertContains(response, f"{reverse('pool:bets-tab')}?pool={self.pool.slug}")


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
        buf = BytesIO()
        Image.new("RGB", (1, 1), color="red").save(buf, format="PNG")
        buf.seek(0)
        uploaded = SimpleUploadedFile("avatar.png", buf.read(), content_type="image/png")

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

    def _upload_image_as(self, username, *, filename="image.png", size=(10, 10)):
        self.client.login(username=username, password="123456Aa!")
        buf = BytesIO()
        Image.new("RGB", size, color="red").save(buf, format="PNG")
        buf.seek(0)
        uploaded = SimpleUploadedFile(filename, buf.read(), content_type="image/png")
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

    def test_profile_image_uses_unique_filename_per_upload(self):
        # Bug: nomes iguais no S3 (file_overwrite) sobrescreviam, vazando foto entre usuários.
        self._upload_image_as("profile-user", filename="image.png")
        self._upload_image_as("other-user", filename="image.png")

        mine = UserProfile.objects.get(user=self.user).profile_image.name
        theirs = UserProfile.objects.get(user=self.other_user).profile_image.name

        self.assertNotEqual(mine, theirs)
        self.assertNotIn("image.png", mine)
        self.assertNotIn("image.png", theirs)

    def test_profile_image_resized_and_converted_to_jpeg(self):
        # Fotos de celular são grandes; redimensiona e converte para JPEG no upload.
        self._upload_image_as("profile-user", filename="foto.png", size=(2000, 1500))

        profile = UserProfile.objects.get(user=self.user)
        self.assertTrue(profile.profile_image.name.endswith(".jpg"))
        with Image.open(profile.profile_image) as stored:
            self.assertLessEqual(max(stored.size), 1024)
            self.assertEqual(stored.format, "JPEG")

    def test_profile_image_accepts_heic_and_stores_jpeg(self):
        # Foto do iPhone (HEIC) deve ser aceita e convertida para JPEG.
        self.client.login(username="profile-user", password="123456Aa!")
        buf = BytesIO()
        Image.new("RGB", (60, 60), color="blue").save(buf, format="HEIF")
        buf.seek(0)
        uploaded = SimpleUploadedFile("foto.heic", buf.read(), content_type="image/heic")

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
        self.assertTrue(profile.profile_image.name.endswith(".jpg"))
        with Image.open(profile.profile_image) as stored:
            self.assertEqual(stored.format, "JPEG")

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
        self.assertContains(response, "ficarão visíveis após o travamento dos palpites")
        self.assertNotContains(response, "Meu palpite")

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
        self.assertContains(response, "Meu palpite")


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


class BuildGroupAuditTest(TestCase):
    """Integration test: builds a real season + participant and verifies the
    audit structure matches the qualifier bonus formula exactly."""

    def setUp(self):
        from src.football.models import Standing
        from src.pool.models import PoolParticipantStanding

        self.user = User.objects.create_user(username="ga-user", email="ga@example.com", password="123456Aa!")
        self.competition = Competition.objects.create(fifa_id=300, name="GA Cup")
        self.season = Season.objects.create(
            fifa_id=300,
            competition=self.competition,
            name="GA 2026",
            year=2026,
            start_date="2026-06-01",
            end_date="2026-07-30",
        )
        self.group_stage = Stage.objects.create(fifa_id="GA-GROUP", season=self.season, name="Group", order=1)
        self.r32_stage = Stage.objects.create(fifa_id="GA-R32", season=self.season, name="R32", order=2)

        self.group_a = Group.objects.create(stage=self.group_stage, name="A", fifa_id="GA-GA")
        self.teams = []
        for i in range(1, 5):
            t = Team.objects.create(fifa_id=f"GA-A{i}", name=f"GA A{i}", name_norm=f"ga a{i}", code=f"GA{i}")
            self.teams.append(t)

        # Group stage already over: a finished group match dated in the past.
        # The qualifier bonus is only awarded after the group stage ends, so the
        # R32-draw scenarios below require the group phase to be finished.
        past = timezone.now() - timezone.timedelta(days=2)
        Match.objects.create(
            fifa_id="GA-GROUP01",
            season=self.season,
            stage=self.group_stage,
            match_number=100,
            match_date_utc=past,
            match_date_local=past,
            match_date_brasilia=past,
            home_team=self.teams[0],
            away_team=self.teams[1],
        )

        self.pool = Pool.objects.create(name="GA Pool", slug="ga-pool", season=self.season, created_by=self.user)
        self.participant = PoolParticipant.objects.create(pool=self.pool, user=self.user, is_active=True)
        Payment.objects.create(
            user=self.user,
            pool=self.pool,
            amount=self.pool.entry_fee,
            amount_received=self.pool.entry_fee,
            payment_method="pix",
            status="approved",
        )

        # Real standings A1..A4 in positions 1..4
        for pos in (1, 2, 3, 4):
            Standing.objects.create(
                season=self.season,
                group=self.group_a,
                team=self.teams[pos - 1],
                position=pos,
                points=10 - pos,
            )

        # Projection: I predicted A1 in 1st, A3 in 2nd, A2 in 3rd
        PoolParticipantStanding.objects.create(
            participant=self.participant, group=self.group_a, team=self.teams[0], position=1
        )
        PoolParticipantStanding.objects.create(
            participant=self.participant, group=self.group_a, team=self.teams[2], position=2
        )
        PoolParticipantStanding.objects.create(
            participant=self.participant, group=self.group_a, team=self.teams[1], position=3
        )

    def test_audit_before_r32_draw_keeps_third_row_pending(self):
        from src.penninicup.views import _build_group_audit

        audit = _build_group_audit(self.participant, self.season, self.pool.get_scoring_config())

        self.assertEqual(len(audit), 1)
        entry = audit[0]
        rows = entry["rows"]
        self.assertEqual([r["position"] for r in rows], [1, 2, 3])

        # Row 1: predicted A1 (real 1st) → qualified+position_match
        self.assertTrue(rows[0]["settled"])
        self.assertTrue(rows[0]["qualified"])
        self.assertTrue(rows[0]["position_match"])

        # Row 2: predicted A3 (real 3rd) in 2nd → before R32 draw, 3rd is not
        # a qualifier yet, but A3 is also not in real top 2 → not qualified.
        self.assertTrue(rows[1]["settled"])
        self.assertFalse(rows[1]["qualified"])

        # Row 3: predicted A2 (real 2nd) in 3rd → r32_drawn=False → settled=False
        self.assertFalse(rows[2]["settled"])

    def test_audit_after_r32_draw_with_advancing_third(self):
        from src.penninicup.views import _build_group_audit

        # Draw R32 placing A3 in a match → A3 becomes a real qualifier.
        Match.objects.create(
            fifa_id="GA-R3201",
            season=self.season,
            stage=self.r32_stage,
            match_number=1,
            match_date_utc=timezone.now(),
            match_date_local=timezone.now(),
            match_date_brasilia=timezone.now() + timezone.timedelta(hours=2),
            home_team=self.teams[2],  # A3
            away_team=self.teams[0],  # A1
        )

        audit = _build_group_audit(self.participant, self.season, self.pool.get_scoring_config())
        entry = audit[0]
        rows = entry["rows"]
        scoring = self.pool.get_scoring_config()

        # Row 2: predicted A3 in 2nd → A3 advanced (qualifier) but position 2 != 3
        self.assertTrue(rows[1]["qualified"])
        self.assertFalse(rows[1]["position_match"])
        self.assertEqual(rows[1]["points"], scoring.group_qualifier_points)

        # Row 3: predicted A2 in 3rd, real 3rd is A3 → A2 IS a real qualifier
        # (it finished 2nd in real), so qualified=True, but position_match=False.
        self.assertTrue(rows[2]["settled"])
        self.assertTrue(rows[2]["qualified"])
        self.assertFalse(rows[2]["position_match"])
        self.assertEqual(rows[2]["points"], scoring.group_qualifier_points)
        # third_advanced reflects the REAL team at that slot (A3), not the predicted one.
        self.assertTrue(rows[2]["third_advanced"])

        # Group points sum equals what the scoring function computes.
        from src.pool.services.ranking import _calculate_group_qualifier_bonus

        expected = _calculate_group_qualifier_bonus(self.participant, scoring)
        self.assertEqual(entry["group_points"], expected)

    def test_audit_after_r32_draw_with_unlucky_third(self):
        from src.penninicup.views import _build_group_audit

        # R32 contains A1 and A2 only → A3 is NOT a real qualifier.
        Match.objects.create(
            fifa_id="GA-R3202",
            season=self.season,
            stage=self.r32_stage,
            match_number=2,
            match_date_utc=timezone.now(),
            match_date_local=timezone.now(),
            match_date_brasilia=timezone.now() + timezone.timedelta(hours=2),
            home_team=self.teams[0],
            away_team=self.teams[1],
        )

        audit = _build_group_audit(self.participant, self.season, self.pool.get_scoring_config())
        rows = audit[0]["rows"]

        # Row 3: real 3rd-place team A3 did NOT advance.
        self.assertTrue(rows[2]["settled"])
        self.assertFalse(rows[2]["third_advanced"])


class BuildKnockoutByPhasePredictedWinnersTest(SimpleTestCase):
    def test_predicted_includes_advanced_and_decided_flags(self):
        from types import SimpleNamespace

        from src.penninicup.views import _build_knockout_by_phase

        team_a = SimpleNamespace(id=1, name="A")
        team_b = SimpleNamespace(id=2, name="B")
        team_c = SimpleNamespace(id=3, name="C")
        stage = SimpleNamespace(fifa_id="R16", name="Oitavas de Final")

        match_decided = SimpleNamespace(
            stage=stage,
            match_number=1,
            winner=team_a,
        )
        match_pending = SimpleNamespace(
            stage=stage,
            match_number=2,
            winner=None,
        )
        bet_advanced = SimpleNamespace(winner_pred=team_a, winner_pred_id=1)
        bet_eliminated = SimpleNamespace(winner_pred=team_c, winner_pred_id=3)
        bet_pending = SimpleNamespace(winner_pred=team_b, winner_pred_id=2)

        rows = [
            {"match": match_decided, "bet": bet_advanced, "bet_score": None},
            {"match": match_pending, "bet": bet_eliminated, "bet_score": None},
            {"match": match_pending, "bet": bet_pending, "bet_score": None},
        ]

        scoring_config = SimpleNamespace(knockout_team_advancement_bonus=0)
        phases = _build_knockout_by_phase(rows, scoring_config)

        self.assertEqual(len(phases), 1)
        predicted = phases[0]["predicted_winners"]
        self.assertEqual(len(predicted), 3)
        items_by_team_id = {item["team"].id: item for item in predicted}

        # team_a: match decided → advanced=True, decided=True
        self.assertTrue(items_by_team_id[1]["advanced"])
        self.assertTrue(items_by_team_id[1]["decided"])

        # team_c: match still pending → decided=False regardless of other matches in phase
        self.assertFalse(items_by_team_id[3]["advanced"])
        self.assertFalse(items_by_team_id[3]["decided"])

        # team_b: match still pending → decided=False, advanced=False
        self.assertFalse(items_by_team_id[2]["decided"])
        self.assertFalse(items_by_team_id[2]["advanced"])


class HomeNextMatchesContextTest(TestCase):
    """_build_home_next_matches_context: jogos não finalizados dentro da janela
    live (2h após kickoff) continuam na lista e vêm marcados is_live."""

    def setUp(self):
        self.user = User.objects.create_user(username="nm-user", email="nm@example.com", password="123456Aa!")
        competition = Competition.objects.create(fifa_id=771, name="Copa NM")
        self.season = Season.objects.create(
            fifa_id=771,
            competition=competition,
            name="Temporada NM",
            year=2026,
            start_date="2026-06-01",
            end_date="2026-07-30",
        )
        self.stage = Stage.objects.create(fifa_id="NM-STAGE", season=self.season, name="Fase de Grupos", order=1)
        self.group = Group.objects.create(fifa_id="NM-GROUP", stage=self.stage, name="A")
        self.team = Team.objects.create(
            fifa_id="NM-TEAM", name="Time NM", name_norm="time nm", code="TNM", group=self.group
        )
        self.pool = Pool.objects.create(
            name="Pool NM", slug="pool-nm", season=self.season, created_by=self.user, requires_payment=False
        )
        self.participant = PoolParticipant.objects.create(pool=self.pool, user=self.user, is_active=True)

    def _make_match(self, *, fifa_id, number, kickoff, status=Match.STATUS_SCHEDULED):
        return Match.objects.create(
            fifa_id=fifa_id,
            season=self.season,
            stage=self.stage,
            group=self.group,
            match_number=number,
            match_date_utc=kickoff,
            match_date_local=kickoff,
            match_date_brasilia=kickoff,
            home_team=self.team,
            away_team=self.team,
            status=status,
        )

    def test_live_match_within_window_is_listed_and_flagged(self):
        from src.penninicup.views import _build_home_next_matches_context

        now = timezone.now()
        self._make_match(fifa_id="NM-LIVE", number=1, kickoff=now - timezone.timedelta(minutes=30))

        rows = _build_home_next_matches_context(participant=self.participant, pool=self.pool)

        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["is_live"])

    def test_match_past_live_window_is_excluded(self):
        from src.penninicup.views import _build_home_next_matches_context

        now = timezone.now()
        self._make_match(fifa_id="NM-OLD", number=2, kickoff=now - timezone.timedelta(hours=3))

        rows = _build_home_next_matches_context(participant=self.participant, pool=self.pool)

        self.assertEqual(rows, [])

    def test_finished_match_is_excluded(self):
        from src.penninicup.views import _build_home_next_matches_context

        now = timezone.now()
        self._make_match(
            fifa_id="NM-FIN", number=3, kickoff=now - timezone.timedelta(minutes=30), status=Match.STATUS_FINISHED
        )

        rows = _build_home_next_matches_context(participant=self.participant, pool=self.pool)

        self.assertEqual(rows, [])

    def test_future_match_listed_not_live(self):
        from src.penninicup.views import _build_home_next_matches_context

        now = timezone.now()
        self._make_match(fifa_id="NM-FUT", number=4, kickoff=now + timezone.timedelta(days=1))

        rows = _build_home_next_matches_context(participant=self.participant, pool=self.pool)

        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0]["is_live"])

    def test_respects_limit_and_order(self):
        from src.penninicup.views import _build_home_next_matches_context

        now = timezone.now()
        self._make_match(fifa_id="NM-A", number=10, kickoff=now + timezone.timedelta(days=3))
        self._make_match(fifa_id="NM-B", number=11, kickoff=now + timezone.timedelta(days=1))
        self._make_match(fifa_id="NM-C", number=12, kickoff=now + timezone.timedelta(days=2))
        self._make_match(fifa_id="NM-D", number=13, kickoff=now + timezone.timedelta(days=4))

        rows = _build_home_next_matches_context(participant=self.participant, pool=self.pool, limit=3)

        self.assertEqual(len(rows), 3)
        self.assertEqual([r["match"].fifa_id for r in rows], ["NM-B", "NM-C", "NM-A"])


class HomeLayoutTest(TestCase):
    """Painel da home: duas colunas (atalhos | próximos jogos), sem abas, e o
    jogo live aparece marcado AO VIVO."""

    def setUp(self):
        self.user = User.objects.create_user(username="layout-user", email="layout@example.com", password="123456Aa!")
        self.client.force_login(self.user)
        competition = Competition.objects.create(fifa_id=772, name="Copa Layout")
        self.season = Season.objects.create(
            fifa_id=772,
            competition=competition,
            name="Temporada Layout",
            year=2026,
            start_date="2026-06-01",
            end_date="2026-07-30",
        )
        self.stage = Stage.objects.create(fifa_id="LO-STAGE", season=self.season, name="Fase de Grupos", order=1)
        self.group = Group.objects.create(fifa_id="LO-GROUP", stage=self.stage, name="A")
        self.team = Team.objects.create(
            fifa_id="LO-TEAM", name="Time Layout", name_norm="time layout", code="TLO", group=self.group
        )
        self.pool = Pool.objects.create(
            name="Pool Layout", slug="pool-layout", season=self.season, created_by=self.user, requires_payment=False
        )
        PoolParticipant.objects.create(pool=self.pool, user=self.user, is_active=True)

    def test_tab_buttons_removed(self):
        response = self.client.get(reverse("penninicup:index"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "data-home-tab-trigger")
        self.assertNotContains(response, "data-home-tab-panel")

    def test_shows_shortcuts_and_next_games_together(self):
        response = self.client.get(reverse("penninicup:index"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Atalhos")
        self.assertContains(response, "Próximos jogos")

    def test_live_match_shows_ao_vivo_badge(self):
        now = timezone.now()
        Match.objects.create(
            fifa_id="LO-LIVE",
            season=self.season,
            stage=self.stage,
            group=self.group,
            match_number=1,
            match_date_utc=now - timezone.timedelta(minutes=15),
            match_date_local=now - timezone.timedelta(minutes=15),
            match_date_brasilia=now - timezone.timedelta(minutes=15),
            home_team=self.team,
            away_team=self.team,
        )

        response = self.client.get(reverse("penninicup:index"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Jogo 1")
        self.assertContains(response, "AO VIVO")

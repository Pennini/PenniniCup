import time
import uuid
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.db import IntegrityError
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from src.football.models import Competition, Season
from src.pool.models import Pool, PoolParticipant

from .forms import CustomPasswordResetForm, CustomUserCreationForm
from .models import InviteToken, UserProfile

User = get_user_model()


class CustomUserModelTest(TestCase):
    """Testes para o modelo CustomUser"""

    def setUp(self):
        self.user_data = {
            "username": "testuser",
            "email": "test@example.com",
            "password": "testpass123",
        }

    def test_create_user(self):
        """Teste criação de usuário com sucesso"""
        user = User.objects.create_user(**self.user_data)
        self.assertEqual(user.username, "testuser")
        self.assertEqual(user.email, "test@example.com")
        self.assertTrue(user.check_password("testpass123"))

    def test_email_unique(self):
        """Teste que email deve ser único"""
        User.objects.create_user(**self.user_data)
        with self.assertRaises(IntegrityError):
            User.objects.create_user(
                username="testuser2",
                email="test@example.com",
                password="testpass456",
            )

    def test_email_case_insensitive_unique(self):
        """Teste que email deve ser único (case-insensitive)"""
        User.objects.create_user(**self.user_data)
        with self.assertRaises(IntegrityError):
            User.objects.create_user(
                username="testuser2",
                email="TEST@EXAMPLE.COM",
                password="testpass456",
            )

    def test_user_str_representation(self):
        """Teste representação em string do usuário"""
        user = User.objects.create_user(**self.user_data)
        self.assertEqual(str(user), "testuser")


class UserProfileModelTest(TestCase):
    """Testes para o modelo UserProfile"""

    def setUp(self):
        self.user = User.objects.create_user(
            username="testuser",
            email="test@example.com",
            password="testpass123",
        )
        self.profile = UserProfile.objects.create(user=self.user)

    def test_profile_creation(self):
        """Teste criação de perfil"""
        self.assertIsNotNone(self.profile.verification_token)
        self.assertFalse(self.profile.email_verified)
        self.assertIsNotNone(self.profile.token_created_at)

    def test_is_token_valid_fresh_token(self):
        """Teste que token recém criado é válido"""
        self.assertTrue(self.profile.is_token_valid())

    def test_is_token_valid_expired_token(self):
        """Teste que token expirado não é válido"""
        # Simular token criado há 25 horas
        self.profile.token_created_at = timezone.now() - timedelta(hours=25)
        self.profile.save()
        self.assertFalse(self.profile.is_token_valid())

    def test_is_token_valid_verified_email(self):
        """Teste que token não é válido se email já verificado"""
        self.profile.email_verified = True
        self.profile.save()
        self.assertFalse(self.profile.is_token_valid())

    def test_generate_new_token(self):
        """Teste geração de novo token"""
        old_token = self.profile.verification_token
        old_created_at = self.profile.token_created_at
        time.sleep(0.01)  # Pequeno delay para garantir timestamp diferente
        self.profile.generate_new_token()
        self.assertNotEqual(old_token, self.profile.verification_token)
        self.assertGreater(self.profile.token_created_at, old_created_at)

    def test_profile_str_representation(self):
        """Teste representação em string do perfil"""
        self.assertEqual(str(self.profile), "Profile: testuser")


class InviteTokenModelTest(TestCase):
    """Testes para o modelo InviteToken"""

    def setUp(self):
        self.user = User.objects.create_user(
            username="testuser",
            email="test@example.com",
            password="testpass123",
        )

    def test_create_invite_token(self):
        """Teste criação de token de convite"""
        token = InviteToken.objects.create(
            created_by=self.user,
            max_uses=5,
        )
        self.assertIsNotNone(token.token)
        self.assertEqual(token.max_uses, 5)
        self.assertEqual(token.uses_count, 0)
        self.assertTrue(token.is_active)

    def test_token_is_valid_new_token(self):
        """Teste que token novo é válido"""
        token = InviteToken.objects.create(created_by=self.user)
        self.assertTrue(token.is_valid())

    def test_token_is_valid_expired_date(self):
        """Teste que token expirado não é válido"""
        token = InviteToken.objects.create(
            created_by=self.user,
            expires_at=timezone.now() - timedelta(days=1),
        )
        self.assertFalse(token.is_valid())

    def test_token_is_valid_max_uses_reached(self):
        """Teste que token com usos esgotados não é válido"""
        token = InviteToken.objects.create(
            created_by=self.user,
            max_uses=2,
            uses_count=2,
        )
        self.assertFalse(token.is_valid())

    def test_token_is_valid_inactive(self):
        """Teste que token inativo não é válido"""
        token = InviteToken.objects.create(
            created_by=self.user,
            is_active=False,
        )
        self.assertFalse(token.is_valid())

    def test_token_is_valid_unlimited_uses(self):
        """Teste que token com usos ilimitados é sempre válido"""
        token = InviteToken.objects.create(
            created_by=self.user,
            max_uses=0,
            uses_count=100,
        )
        self.assertTrue(token.is_valid())

    def test_token_use(self):
        """Teste uso do token"""
        token = InviteToken.objects.create(
            created_by=self.user,
            max_uses=2,
        )
        with self.assertWarns(DeprecationWarning):
            used = token.use()
        self.assertTrue(used)
        self.assertEqual(token.uses_count, 1)
        self.assertTrue(token.is_active)

        with self.assertWarns(DeprecationWarning):
            used = token.use()
        self.assertTrue(used)
        self.assertEqual(token.uses_count, 2)
        self.assertFalse(token.is_active)

    def test_token_use_token_atomic(self):
        """Teste que use_token é atômico"""
        token = InviteToken.objects.create(
            created_by=self.user,
            max_uses=1,
        )
        result = InviteToken.use_token(token.token)
        self.assertTrue(result)

        token.refresh_from_db()
        self.assertEqual(token.uses_count, 1)
        self.assertFalse(token.is_active)

        # Tentar usar novamente
        result = InviteToken.use_token(token.token)
        self.assertFalse(result)


class InviteTokenRaceConditionTest(TransactionTestCase):
    """Testes de concorrência para InviteToken"""

    def setUp(self):
        self.user = User.objects.create_user(
            username="testuser",
            email="test@example.com",
            password="testpass123",
        )

    def test_concurrent_token_use(self):
        """Teste que previne uso simultâneo do token"""
        token = InviteToken.objects.create(
            created_by=self.user,
            max_uses=1,
        )

        # Simular duas tentativas simultâneas
        result1 = InviteToken.use_token(token.token)
        result2 = InviteToken.use_token(token.token)

        # Apenas uma deve ter sucesso
        self.assertTrue(result1)
        self.assertFalse(result2)

        token.refresh_from_db()
        self.assertEqual(token.uses_count, 1)


class CustomUserCreationFormTest(TestCase):
    """Testes para o formulário de criação de usuário"""

    def setUp(self):
        self.user = User.objects.create_user(
            username="creator",
            email="creator@example.com",
            password="testpass123",
        )
        self.token = InviteToken.objects.create(created_by=self.user)

    def test_form_valid_data(self):
        """Teste formulário com dados válidos"""
        form = CustomUserCreationForm(
            {
                "username": "newuser",
                "email": "newuser@example.com",
                "password1": "testpass123",
                "password2": "testpass123",
                "invite_token": str(self.token.token),
            }
        )
        self.assertTrue(form.is_valid())

    def test_form_email_required(self):
        """Teste que email é obrigatório"""
        form = CustomUserCreationForm(
            {
                "username": "newuser",
                "password1": "testpass123",
                "password2": "testpass123",
                "invite_token": str(self.token.token),
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("email", form.errors)

    def test_form_duplicate_email(self):
        """Teste que não permite email duplicado"""
        form = CustomUserCreationForm(
            {
                "username": "newuser",
                "email": "creator@example.com",
                "password1": "testpass123",
                "password2": "testpass123",
                "invite_token": str(self.token.token),
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("email", form.errors)

    def test_form_duplicate_email_case_insensitive(self):
        """Teste que email duplicado é case-insensitive"""
        form = CustomUserCreationForm(
            {
                "username": "newuser",
                "email": "CREATOR@EXAMPLE.COM",
                "password1": "testpass123",
                "password2": "testpass123",
                "invite_token": str(self.token.token),
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("email", form.errors)

    def test_form_save_creates_inactive_user(self):
        """Teste que o usuário nasce inativo até verificar email"""
        form = CustomUserCreationForm(
            {
                "username": "newinactive",
                "email": "newinactive@example.com",
                "password1": "testpass123",
                "password2": "testpass123",
                "invite_token": str(self.token.token),
            }
        )
        self.assertTrue(form.is_valid())

        user = form.save()
        self.assertFalse(user.is_active)

    def test_form_allows_username_with_spaces(self):
        """Teste que username com espaços entre palavras é aceito"""
        form = CustomUserCreationForm(
            {
                "username": "Andre   Silva",
                "email": "andresilva@example.com",
                "password1": "testpass123",
                "password2": "testpass123",
                "invite_token": str(self.token.token),
            }
        )

        self.assertTrue(form.is_valid())
        user = form.save()
        self.assertEqual(user.username, "Andre Silva")

    def test_form_rejects_username_too_long(self):
        """Teste que username muito longo é rejeitado"""
        long_username = "a" * 41
        form = CustomUserCreationForm(
            {
                "username": long_username,
                "email": "longusername@example.com",
                "password1": "testpass123",
                "password2": "testpass123",
                "invite_token": str(self.token.token),
            }
        )

        self.assertFalse(form.is_valid())
        self.assertIn("username", form.errors)

    def test_form_rejects_username_too_short(self):
        """Teste que username com menos de 3 caracteres é rejeitado"""
        form = CustomUserCreationForm(
            {
                "username": "ab",
                "email": "shortusername@example.com",
                "password1": "testpass123",
                "password2": "testpass123",
                "invite_token": str(self.token.token),
            }
        )

        self.assertFalse(form.is_valid())
        self.assertIn("username", form.errors)

    def test_form_rejects_username_without_letters(self):
        """Teste que username apenas numérico é rejeitado"""
        form = CustomUserCreationForm(
            {
                "username": "12345",
                "email": "numericusername@example.com",
                "password1": "testpass123",
                "password2": "testpass123",
                "invite_token": str(self.token.token),
            }
        )

        self.assertFalse(form.is_valid())
        self.assertIn("username", form.errors)

    def test_form_rejects_username_with_special_characters(self):
        """Teste que username com caracteres especiais é rejeitado"""
        form = CustomUserCreationForm(
            {
                "username": "andre.silva@_",
                "email": "andrespecial@example.com",
                "password1": "testpass123",
                "password2": "testpass123",
                "invite_token": str(self.token.token),
            }
        )

        self.assertFalse(form.is_valid())
        self.assertIn("username", form.errors)

    def test_form_rejects_duplicate_username_case_insensitive(self):
        """Teste que username duplicado é bloqueado ignorando maiúsculas/minúsculas"""
        User.objects.create_user(
            username="Andre Silva",
            email="andre.silva@example.com",
            password="testpass123",
        )

        form = CustomUserCreationForm(
            {
                "username": "andre silva",
                "email": "andresilva2@example.com",
                "password1": "testpass123",
                "password2": "testpass123",
                "invite_token": str(self.token.token),
            }
        )

        self.assertFalse(form.is_valid())
        self.assertIn("username", form.errors)


class CustomPasswordResetFormTest(TestCase):
    """Testes para o formulário de reset de senha"""

    def setUp(self):
        self.active_user = User.objects.create_user(
            username="activeuser",
            email="active@example.com",
            password="testpass123",
            is_active=True,
        )
        self.inactive_user = User.objects.create_user(
            username="inactiveuser",
            email="inactive@example.com",
            password="testpass123",
            is_active=False,
        )

    def test_form_valid_for_active_user(self):
        """Teste que formulário aceita email de usuário ativo"""
        form = CustomPasswordResetForm({"email": "active@example.com"})
        self.assertTrue(form.is_valid())

    def test_form_invalid_for_inactive_user(self):
        """Teste que formulário rejeita email de usuário inativo"""
        form = CustomPasswordResetForm({"email": "inactive@example.com"})
        self.assertFalse(form.is_valid())
        self.assertIn("email", form.errors)

    def test_form_invalid_for_nonexistent_email(self):
        """Teste que formulário rejeita email não cadastrado"""
        form = CustomPasswordResetForm({"email": "nonexistent@example.com"})
        self.assertFalse(form.is_valid())
        self.assertIn("email", form.errors)

    def test_get_users_only_active(self):
        """Teste que get_users retorna apenas usuários ativos"""
        form = CustomPasswordResetForm({"email": "active@example.com"})
        users = list(form.get_users("active@example.com"))
        self.assertEqual(len(users), 1)
        self.assertEqual(users[0], self.active_user)

        users = list(form.get_users("inactive@example.com"))
        self.assertEqual(len(users), 0)


class RegisterViewTest(TestCase):
    """Testes para a view de registro"""

    def setUp(self):
        cache.clear()
        self.creator = User.objects.create_user(
            username="creator",
            email="creator@example.com",
            password="testpass123",
        )
        self.competition = Competition.objects.create(fifa_id=990, name="Comp Teste")
        self.season = Season.objects.create(
            fifa_id=990,
            competition=self.competition,
            name="Season Teste",
            year=2026,
            start_date="2026-06-01",
            end_date="2026-07-20",
        )
        self.pool = Pool.objects.create(
            name="Bolao Registro",
            slug="bolao-registro",
            season=self.season,
            created_by=self.creator,
        )
        self.token = InviteToken.objects.create(created_by=self.creator, pool=self.pool, max_uses=5)
        self.register_url = reverse("accounts:register")

    def test_register_page_loads(self):
        """Teste que página de registro carrega"""
        response = self.client.get(self.register_url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "accounts/register.html")

    def test_register_with_token_in_url(self):
        """Teste registro com token na URL"""
        url = reverse("accounts:register_with_token", kwargs={"token": self.token.token})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, str(self.token.token))

    def test_register_with_invalid_token_in_url(self):
        """Teste registro com token inválido na URL"""
        invalid_token = uuid.uuid4()
        url = reverse("accounts:register_with_token", kwargs={"token": invalid_token})
        response = self.client.get(url)
        self.assertRedirects(response, reverse("accounts:login"))

    @patch("src.accounts.views.send_mail")
    def test_successful_registration(self, mock_send_mail):
        """Teste registro bem-sucedido"""
        data = {
            "username": "newuser",
            "email": "newuser@example.com",
            "password1": "ComplexPass123!",
            "password2": "ComplexPass123!",
            "invite_token": str(self.token.token),
        }
        response = self.client.post(self.register_url, data)

        # Verificar redirecionamento
        self.assertRedirects(response, reverse("accounts:verify_email_sent"))

        # Verificar usuário criado
        user = User.objects.get(username="newuser")
        self.assertFalse(user.is_active)
        self.assertEqual(user.email, "newuser@example.com")

        # Verificar perfil criado
        self.assertTrue(hasattr(user, "profile"))
        self.assertFalse(user.profile.email_verified)

        # Verificar token usado
        self.token.refresh_from_db()
        self.assertEqual(self.token.uses_count, 1)

        # Verificar auto-participacao no bolao ligado ao token
        self.assertTrue(PoolParticipant.objects.filter(pool=self.pool, user=user, is_active=True).exists())

        # Verificar email enviado
        mock_send_mail.assert_called_once()

    def test_registration_without_token(self):
        """Teste registro sem token"""
        data = {
            "username": "newuser",
            "email": "newuser@example.com",
            "password1": "ComplexPass123!",
            "password2": "ComplexPass123!",
        }
        response = self.client.post(self.register_url, data)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Token de convite é obrigatório.", response.context["form"].errors["invite_token"])

    def test_registration_with_expired_token(self):
        """Teste registro com token expirado"""
        expired_token = InviteToken.objects.create(
            created_by=self.creator,
            expires_at=timezone.now() - timedelta(days=1),
        )
        data = {
            "username": "newuser",
            "email": "newuser@example.com",
            "password1": "ComplexPass123!",
            "password2": "ComplexPass123!",
            "invite_token": str(expired_token.token),
        }
        response = self.client.post(self.register_url, data)
        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "Este token de convite expirou ou já foi usado.",
            response.context["form"].errors["invite_token"],
        )

    def test_authenticated_user_redirect(self):
        """Teste que usuário autenticado é redirecionado"""
        self.client.login(username="creator", password="testpass123")
        response = self.client.get(self.register_url)
        self.assertRedirects(response, reverse("penninicup:index"))

    @patch("src.accounts.views.InviteToken.use_token", return_value=False)
    @patch("src.accounts.views.send_mail")
    def test_registration_fails_before_user_creation_when_token_consumption_fails(self, _mock_send_mail, _mock_use):
        data = {
            "username": "blockeduser",
            "email": "blocked@example.com",
            "password1": "ComplexPass123!",
            "password2": "ComplexPass123!",
            "invite_token": str(self.token.token),
        }

        response = self.client.post(self.register_url, data)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username="blockeduser").exists())


class VerifyEmailViewTest(TestCase):
    """Testes para verificação de email"""

    def setUp(self):
        self.user = User.objects.create_user(
            username="testuser",
            email="test@example.com",
            password="testpass123",
            is_active=False,
        )
        self.profile = UserProfile.objects.create(user=self.user)

    def test_verify_email_success(self):
        """Teste verificação de email bem-sucedida"""
        url = reverse("accounts:verify_email", kwargs={"token": self.profile.verification_token})
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "accounts/verification_success.html")

        # Verificar que usuário foi ativado
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_active)

        # Verificar que email foi marcado como verificado
        self.profile.refresh_from_db()
        self.assertTrue(self.profile.email_verified)

    def test_verify_email_invalid_token(self):
        """Teste verificação com token inválido"""
        url = reverse("accounts:verify_email", kwargs={"token": uuid.uuid4()})
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "accounts/verification_failed.html")
        self.assertContains(response, "inválido")

    def test_verify_email_expired_token(self):
        """Teste verificação com token expirado"""
        self.profile.token_created_at = timezone.now() - timedelta(hours=25)
        self.profile.save()

        url = reverse("accounts:verify_email", kwargs={"token": self.profile.verification_token})
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "accounts/verification_failed.html")
        self.assertContains(response, "expirou")

    def test_verify_email_already_verified(self):
        """Teste verificação de email já verificado"""
        self.profile.email_verified = True
        self.profile.save()

        url = reverse("accounts:verify_email", kwargs={"token": self.profile.verification_token})
        response = self.client.get(url)

        self.assertRedirects(response, reverse("accounts:login"))


class ResendVerificationEmailTest(TestCase):
    """Testes para reenvio de email de verificação"""

    def setUp(self):
        self.user = User.objects.create_user(
            username="testuser",
            email="test@example.com",
            password="testpass123",
            is_active=False,
        )
        self.profile = UserProfile.objects.create(user=self.user)
        self.url = reverse("accounts:resend_verification")

    @patch("src.accounts.views.send_mail")
    def test_resend_email_success(self, mock_send_mail):
        """Teste reenvio bem-sucedido"""
        session = self.client.session
        session["registered_user_id"] = self.user.id
        session["allow_resend_page"] = True
        session.save()

        # Ajustar tempo de criação do token para permitir reenvio
        self.profile.token_created_at = timezone.now() - timedelta(seconds=31)
        self.profile.save()

        response = self.client.get(self.url)

        self.assertRedirects(response, reverse("accounts:verify_email_sent"))
        mock_send_mail.assert_called_once()

    def test_resend_email_no_session(self):
        """Teste reenvio sem sessão válida"""
        response = self.client.get(self.url)
        self.assertRedirects(response, reverse("accounts:login"))

    def test_resend_email_too_soon(self):
        """Teste reenvio muito cedo (rate limit)"""
        session = self.client.session
        session["registered_user_id"] = self.user.id
        session["allow_resend_page"] = True
        session.save()

        response = self.client.get(self.url)
        self.assertRedirects(response, reverse("accounts:verify_email_sent"))

    def test_resend_email_already_verified(self):
        """Teste reenvio para email já verificado"""
        self.profile.email_verified = True
        self.profile.save()

        session = self.client.session
        session["registered_user_id"] = self.user.id
        session.save()

        response = self.client.get(self.url)
        self.assertRedirects(response, reverse("penninicup:index"))


class LoginLogoutTest(TestCase):
    """Testes para login e logout"""

    def setUp(self):
        self.user = User.objects.create_user(
            username="testuser",
            email="test@example.com",
            password="testpass123",
            is_active=True,
        )
        self.login_url = reverse("accounts:login")
        self.logout_url = reverse("accounts:logout")

    def test_login_page_loads(self):
        """Teste que página de login carrega"""
        response = self.client.get(self.login_url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "accounts/login.html")

    def test_login_success(self):
        """Teste login bem-sucedido"""
        response = self.client.post(
            self.login_url,
            {"username": "testuser", "password": "testpass123"},
        )
        self.assertRedirects(response, reverse("penninicup:index"))
        self.assertTrue(response.wsgi_request.user.is_authenticated)

    def test_login_success_with_email(self):
        """Teste login bem-sucedido usando e-mail"""
        response = self.client.post(
            self.login_url,
            {"username": "test@example.com", "password": "testpass123"},
        )
        self.assertRedirects(response, reverse("penninicup:index"))
        self.assertTrue(response.wsgi_request.user.is_authenticated)

    def test_login_invalid_credentials(self):
        """Teste login com credenciais inválidas"""
        response = self.client.post(
            self.login_url,
            {"username": "testuser", "password": "wrongpass"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.wsgi_request.user.is_authenticated)

    def test_login_inactive_user(self):
        """Teste que usuário inativo não pode fazer login"""
        self.user.is_active = False
        self.user.save()

        response = self.client.post(
            self.login_url,
            {"username": "testuser", "password": "testpass123"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.wsgi_request.user.is_authenticated)

    def test_logout(self):
        """Teste logout"""
        self.client.login(username="testuser", password="testpass123")
        response = self.client.post(self.logout_url)
        self.assertRedirects(response, reverse("accounts:login"))


class PasswordResetTest(TestCase):
    """Testes para recuperação de senha"""

    def setUp(self):
        self.user = User.objects.create_user(
            username="testuser",
            email="test@example.com",
            password="testpass123",
            is_active=True,
        )
        self.reset_url = reverse("accounts:password_reset")

    def test_password_reset_page_loads(self):
        """Teste que página de reset carrega"""
        response = self.client.get(self.reset_url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "accounts/password_reset.html")

    @patch("src.accounts.forms.EmailMultiAlternatives.send")
    def test_password_reset_request(self, mock_send):
        """Teste solicitação de reset de senha"""
        response = self.client.post(self.reset_url, {"email": "test@example.com"})
        self.assertRedirects(response, reverse("accounts:password_reset_done"))
        mock_send.assert_called_once()

    def test_password_reset_inactive_user(self):
        """Teste reset para usuário inativo"""
        self.user.is_active = False
        self.user.save()

        response = self.client.post(self.reset_url, {"email": "test@example.com"})
        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "Esta conta ainda não foi ativada. "
            "Verifique seu e-mail e clique no link de verificação antes de redefinir a senha.",
            response.context["form"].errors["email"],
        )


class EmailSendingTest(TestCase):
    """Testes para envio de emails"""

    def setUp(self):
        self.creator = User.objects.create_user(
            username="creator",
            email="creator@example.com",
            password="testpass123",
        )
        self.token = InviteToken.objects.create(created_by=self.creator)

    def test_verification_email_sent_on_registration(self):
        """Teste que email de verificação é enviado no registro"""
        data = {
            "username": "newuser",
            "email": "newuser@example.com",
            "password1": "ComplexPass123!",
            "password2": "ComplexPass123!",
            "invite_token": str(self.token.token),
        }
        self.client.post(reverse("accounts:register"), data)

        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Confirme seu e-mail", mail.outbox[0].subject)
        self.assertIn("newuser@example.com", mail.outbox[0].to)

    def test_verification_email_contains_link(self):
        """Teste que email contém link de verificação"""
        data = {
            "username": "newuser",
            "email": "newuser@example.com",
            "password1": "ComplexPass123!",
            "password2": "ComplexPass123!",
            "invite_token": str(self.token.token),
        }
        self.client.post(reverse("accounts:register"), data)

        user = User.objects.get(username="newuser")
        verification_url = reverse("accounts:verify_email", kwargs={"token": user.profile.verification_token})

        self.assertIn(verification_url, mail.outbox[0].body)

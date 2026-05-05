import datetime
import logging
import smtplib

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.views import LoginView, PasswordResetView
from django.core.exceptions import ValidationError
from django.core.mail import BadHeaderError, send_mail
from django.db import transaction
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.generic import CreateView, TemplateView
from django_ratelimit.decorators import ratelimit

from .forms import CustomUserCreationForm
from .models import InviteToken, UserProfile

User = get_user_model()


class RateLimitedLoginView(LoginView):
    @method_decorator(ratelimit(key="ip", rate="10/m", method="POST", block=False))
    def dispatch(self, request, *args, **kwargs):
        if request.method == "POST" and getattr(request, "limited", False):
            form = self.get_form()
            form.add_error(None, "Muitas tentativas de login. Aguarde 1 minuto e tente novamente.")
            response = self.render_to_response(self.get_context_data(form=form))
            response.status_code = 429
            return response
        return super().dispatch(request, *args, **kwargs)


class RateLimitedPasswordResetView(PasswordResetView):
    @method_decorator(ratelimit(key="post:email", rate="3/h", method="POST", block=False))
    def dispatch(self, request, *args, **kwargs):
        if request.method == "POST" and getattr(request, "limited", False):
            form = self.get_form()
            form.add_error("email", "Limite de tentativas atingido. Aguarde e tente novamente mais tarde.")
            response = self.render_to_response(self.get_context_data(form=form))
            response.status_code = 429
            return response
        return super().dispatch(request, *args, **kwargs)


# Create your views here.
class RegisterView(CreateView):
    form_class = CustomUserCreationForm
    success_url = reverse_lazy("penninicup:index")
    template_name = "accounts/register.html"

    @method_decorator(ratelimit(key="ip", rate="5/h", method="POST", block=False))
    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect("penninicup:index")

        if request.method == "POST" and getattr(request, "limited", False):
            messages.error(request, "Muitas tentativas de cadastro. Aguarde e tente novamente mais tarde.")
            return redirect(request.path)

        # Token pode vir via URL ou será validado no formulário
        token_str = kwargs.get("token")
        if token_str:
            try:
                self.invite_token = InviteToken.objects.filter(token=token_str).first()
                if not self.invite_token:
                    messages.error(request, "Token de convite inválido.")
                    return redirect("accounts:login")
                if not self.invite_token.is_valid():
                    messages.error(request, "Este token de convite expirou ou já foi usado.")
                    return redirect("accounts:login")
            except ValueError:
                messages.error(request, "Token de convite inválido.")
                return redirect("accounts:login")
        else:
            self.invite_token = None

        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.invite_token:
            # context["bolao_name"] = self.invite_token.bolao.name  # TODO: Quando criar app bolao
            context["token"] = str(self.invite_token.token)
        return context

    def form_valid(self, form):
        logger = logging.getLogger(__name__)

        # Se não tem token da URL, validar do formulário
        if not self.invite_token:
            token_str = form.cleaned_data.get("invite_token")
            if not token_str:
                form.add_error("invite_token", "Token de convite é obrigatório.")
                messages.error(self.request, "Token de convite é obrigatório.")
                return self.form_invalid(form)

            try:
                self.invite_token = InviteToken.objects.get(token=token_str)
                if not self.invite_token.is_valid():
                    form.add_error("invite_token", "Este token de convite expirou ou já foi usado.")
                    messages.error(self.request, "Este token de convite expirou ou já foi usado.")
                    return self.form_invalid(form)
            except (InviteToken.DoesNotExist, ValidationError, ValueError):
                form.add_error("invite_token", "Token de convite inválido.")
                messages.error(self.request, "Token de convite inválido.")
                return self.form_invalid(form)

        # Usar transação atômica para garantir consistência
        try:
            with transaction.atomic():
                # Consumir token de forma atômica ANTES de criar usuário/perfil.
                if not InviteToken.use_token(self.invite_token.token):
                    logger.warning("Token %s inválido durante pré-validação de registro", self.invite_token.token)
                    form.add_error("invite_token", "Este token de convite expirou ou já foi usado.")
                    messages.error(self.request, "Este token de convite expirou ou já foi usado.")
                    return self.form_invalid(form)

                # Criar o usuário primeiro
                super().form_valid(form)  # type: ignore # noqa

                # Criar perfil para o usuário
                profile = UserProfile.objects.create(user=self.object)

                # Se o token estiver vinculado a um bolao, o usuario entra automaticamente nele.
                if self.invite_token.pool_id:
                    from src.pool.models import PoolParticipant

                    PoolParticipant.objects.get_or_create(
                        pool_id=self.invite_token.pool_id,
                        user=self.object,
                        defaults={"is_active": True},
                    )

                # Enviar e-mail de verificação
                verification_url = self.request.build_absolute_uri(
                    reverse("accounts:verify_email", kwargs={"token": str(profile.verification_token)})
                )

                subject = "Confirme seu e-mail - PenniniCup"
                message = render_to_string(
                    "accounts/emails/verification_email.html",
                    {
                        "user": self.object,
                        "verification_url": verification_url,
                        "bolao_name": self.invite_token.pool.name if self.invite_token.pool_id else None,
                    },
                )

                send_mail(
                    subject,
                    message,
                    settings.EMAIL_HOST_USER,
                    [self.object.email],
                    html_message=message,
                )

                # Marcar na sessão que o usuário acabou de se registrar
                self.request.session["just_registered"] = True
                self.request.session["registered_user_id"] = self.object.id
                self.request.session["allow_resend_page"] = True

                messages.success(
                    self.request,
                    f"Cadastro realizado, {self.object.username}! Verifique seu e-mail para ativar sua conta.",
                )
                return redirect("accounts:verify_email_sent")

        except (BadHeaderError, smtplib.SMTPException) as e:
            logger.error(f"Falha ao enviar email de verificação para {self.object.email}: {e}")
            messages.error(
                self.request, "Erro ao enviar e-mail de verificação. Por favor, entre em contato com o suporte."
            )
            return self.form_invalid(form)
        except Exception as e:
            logger.exception(
                f"Erro inesperado ao processar cadastro do usuário {form.cleaned_data.get('username')}: {e}"
            )
            messages.error(self.request, "Erro ao processar o cadastro. Por favor, tente novamente.")
            return self.form_invalid(form)

    def form_invalid(self, form):
        messages.error(self.request, "Erro ao criar conta. Verifique os campos e tente novamente.")
        return super().form_invalid(form)


class VerifyEmailSentView(TemplateView):
    """Página informando que o e-mail de verificação foi enviado"""

    template_name = "accounts/verification_sent.html"

    def dispatch(self, request, *args, **kwargs):
        # Verificar se o usuário tem permissão para acessar esta página
        allow_resend_page = request.session.get("allow_resend_page", False)
        registered_user_id = request.session.get("registered_user_id")

        # Deve ter AMBAS as flags: allow_resend_page E registered_user_id
        if not allow_resend_page or not registered_user_id:
            messages.warning(
                request,
                "Esta página só pode ser acessada após o cadastro. "
                "Se você não recebeu o e-mail de verificação, entre em contato com o administrador do bolão.",
            )
            return redirect("accounts:login")

        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Buscar usuário pelo ID da sessão
        registered_user_id = self.request.session.get("registered_user_id")
        if registered_user_id:
            try:
                user = User.objects.get(id=registered_user_id)
                context["user"] = user
            except User.DoesNotExist:
                pass
        return context


def verify_email(request, token):
    """Verifica o e-mail do usuário através do token"""
    try:
        profile = UserProfile.objects.get(verification_token=token)

        if profile.email_verified:
            messages.info(request, "Seu e-mail já foi verificado anteriormente.")
            return redirect("accounts:login")

        if not profile.is_token_valid():
            return render(request, "accounts/verification_failed.html", {"reason": "expired"})

        # Verificar e-mail e ativar usuário
        profile.email_verified = True
        profile.save()

        profile.user.is_active = True
        profile.user.save()

        # Limpar flags de sessão após verificação bem-sucedida
        request.session.pop("just_registered", None)
        request.session.pop("registered_user_id", None)
        request.session.pop("allow_resend_page", None)

        messages.success(request, "E-mail verificado com sucesso! Sua conta está ativa. Faça login para continuar.")
        return render(request, "accounts/verification_success.html")

    except UserProfile.DoesNotExist:
        return render(request, "accounts/verification_failed.html", {"reason": "invalid"})


def resend_verification_email(request):
    """Reenvia o e-mail de verificação"""
    # Buscar usuário pela sessão (já que usuário não está autenticado)
    registered_user_id = request.session.get("registered_user_id")

    if not registered_user_id:
        messages.warning(request, "Sessão inválida. Faça login para reenviar o e-mail.")
        return redirect("accounts:login")

    try:
        user = User.objects.get(id=registered_user_id)
        profile = UserProfile.objects.select_for_update().get(user=user)
    except (User.DoesNotExist, UserProfile.DoesNotExist):
        messages.error(request, "Usuário não encontrado.")
        return redirect("accounts:login")

    if profile.email_verified:
        messages.info(request, "Seu e-mail já está verificado.")
        return redirect("penninicup:index")

    time_since_last_email = timezone.now() - profile.token_created_at
    if time_since_last_email < datetime.timedelta(seconds=30):
        seconds_remaining = 30 - int(time_since_last_email.total_seconds())
        messages.warning(
            request,
            f"Aguarde {seconds_remaining} segundo{'s' if seconds_remaining != 1 else ''} antes de reenviar o e-mail.",
        )
        return redirect("accounts:verify_email_sent")

    # Gerar novo token
    profile.generate_new_token()

    # Enviar e-mail
    verification_url = request.build_absolute_uri(
        reverse("accounts:verify_email", kwargs={"token": str(profile.verification_token)})
    )

    subject = "Confirme seu e-mail - PenniniCup"
    message = render_to_string(
        "accounts/emails/verification_email.html",
        {"user": user, "verification_url": verification_url},
    )

    send_mail(
        subject,
        message,
        settings.EMAIL_HOST_USER,
        [user.email],
        html_message=message,
    )

    messages.success(request, "E-mail de verificação reenviado com sucesso!")
    return redirect("accounts:verify_email_sent")

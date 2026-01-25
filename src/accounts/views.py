from django.contrib import messages
from django.contrib.auth import login
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.views.generic import CreateView

from .forms import CustomUserCreationForm
from .models import InviteToken


# Create your views here.
class RegisterView(CreateView):
    form_class = CustomUserCreationForm
    success_url = reverse_lazy("penninicup:index")
    template_name = "accounts/register.html"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect("penninicup:index")

        # Token pode vir via URL ou será validado no formulário
        token_str = kwargs.get("token")
        if token_str:
            try:
                self.invite_token = get_object_or_404(InviteToken, token=token_str)
                if not self.invite_token.is_valid():
                    messages.error(request, "Este token de convite expirou ou já foi usado.")
                    return redirect("accounts:login")
            except Exception:
                messages.error(request, "Token de convite inválido.")
                return redirect("accounts:login")
        else:
            self.invite_token = None

        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.invite_token:
            context["bolao_name"] = self.invite_token.bolao_name
            context["token"] = str(self.invite_token.token)
        return context

    def form_valid(self, form):
        # Se não tem token da URL, validar do formulário
        if not self.invite_token:
            token_str = form.cleaned_data.get("invite_token")
            if not token_str:
                messages.error(self.request, "Token de convite é obrigatório.")
                return self.form_invalid(form)

            try:
                self.invite_token = InviteToken.objects.get(token=token_str)
                if not self.invite_token.is_valid():
                    messages.error(self.request, "Este token de convite expirou ou já foi usado.")
                    return self.form_invalid(form)
            except InviteToken.DoesNotExist:
                messages.error(self.request, "Token de convite inválido.")
                return self.form_invalid(form)

        # Criar o usuário primeiro
        response = super().form_valid(form)  # type: ignore # noqa

        # Registrar uso do token
        self.invite_token.use()

        # Fazer login automático
        login(self.request, self.object)

        messages.success(
            self.request,
            f"Bem-vindo ao {self.invite_token.bolao_name}, {self.object.username}! Sua conta foi criada com sucesso.",
        )
        return redirect("penninicup:index")

    def form_invalid(self, form):
        messages.error(self.request, "Erro ao criar conta. Verifique os campos e tente novamente.")
        return super().form_invalid(form)

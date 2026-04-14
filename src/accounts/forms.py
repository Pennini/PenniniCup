from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import PasswordResetForm, UserCreationForm
from django.core.exceptions import ValidationError
from django.core.mail import EmailMultiAlternatives
from django.template import loader

User = get_user_model()


class CustomPasswordResetForm(PasswordResetForm):
    """Formulário customizado que só permite reset para usuários ativos"""

    def send_mail(
        self,
        subject_template_name,
        email_template_name,
        context,
        from_email,
        to_email,
        html_email_template_name=None,
    ):
        """
        Envia email HTML em vez de texto plano
        """
        subject = loader.render_to_string(subject_template_name, context)
        subject = "".join(subject.splitlines())
        body = loader.render_to_string(email_template_name, context)

        email_message = EmailMultiAlternatives(subject, body, from_email, [to_email])
        if html_email_template_name is not None:
            html_email = loader.render_to_string(html_email_template_name, context)
            email_message.attach_alternative(html_email, "text/html")

        email_message.send()

    def get_users(self, email):
        """Retorna apenas usuários ativos com o email fornecido"""
        active_users = User.objects.filter(
            email__iexact=email,
            is_active=True,
        )
        return (user for user in active_users if user.has_usable_password())

    def clean_email(self):
        email = self.cleaned_data.get("email")
        # Verificar se existe um usuário com esse email
        if not User.objects.filter(email__iexact=email).exists():
            raise ValidationError("Não encontramos nenhuma conta com este e-mail. Verifique o endereço digitado.")

        # Verificar se existe pelo menos um usuário ativo com senha utilizável
        # (espelha a lógica de get_users)
        active_users = User.objects.filter(
            email__iexact=email,
            is_active=True,
        )
        has_active_user = any(user.has_usable_password() for user in active_users)

        if not has_active_user:
            raise ValidationError(
                "Esta conta ainda não foi ativada. "
                "Verifique seu e-mail e clique no link de verificação antes de redefinir a senha."
            )

        return email


class CustomUserCreationForm(UserCreationForm):
    email = forms.EmailField(
        required=True,
        widget=forms.EmailInput(attrs={"placeholder": "Seu melhor e-mail"}),
        label="E-mail",
    )

    invite_token = forms.CharField(
        max_length=36,
        required=False,
        widget=forms.TextInput(attrs={"placeholder": "Cole seu token de convite aqui"}),
        label="Token de Convite",
    )

    class Meta:
        model = User
        fields = ("username", "email", "password1", "password2", "invite_token")

    def clean_email(self):
        email = self.cleaned_data.get("email")
        if User.objects.filter(email__iexact=email).exists():
            raise ValidationError("Este e-mail já está cadastrado. Use outro e-mail ou faça login.")
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data["email"]
        # Conta deve nascer inativa até verificação de email.
        user.is_active = False
        if commit:
            user.save()
        return user

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import PasswordResetForm, UserCreationForm
from django.core.exceptions import ValidationError
from django.core.mail import EmailMultiAlternatives
from django.forms.models import construct_instance
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

    def _post_clean(self):
        """
        Evita que o validador padrão de username do model sobreponha a regra customizada do formulário.
        """
        opts = self._meta
        self.instance = construct_instance(self, self.instance, opts.fields, opts.exclude)

        exclude = self._get_validation_exclusions()
        exclude.add("username")

        try:
            self.instance.full_clean(exclude=exclude, validate_unique=False)
        except ValidationError as error:
            self._update_errors(error)

        if self._validate_unique:
            self.validate_unique()

    def clean_email(self):
        email = self.cleaned_data.get("email")
        if User.objects.filter(email__iexact=email).exists():
            raise ValidationError("Este e-mail não está disponível.")
        return email

    def clean_username(self):
        username = (self.cleaned_data.get("username") or "").strip()
        username = " ".join(username.split())

        if not username:
            raise ValidationError("Informe um nome de usuário.")

        if len(username) > 25:
            raise ValidationError("O nome de usuário pode ter no máximo 25 caracteres.")

        if len(username) < 3:
            raise ValidationError("O username deve ter pelo menos 3 caracteres.")

        if any(char != " " and not char.isalnum() for char in username):
            raise ValidationError("Use apenas letras, números e espaço.")

        if not any(char.isalpha() for char in username):
            raise ValidationError("O username deve conter pelo menos uma letra.")

        if User.objects.filter(username__iexact=username).exists():
            raise ValidationError("Este nome de usuário não está disponível.")

        return username

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data["email"]
        # Conta deve nascer inativa até verificação de email.
        user.is_active = False
        if commit:
            user.save()
        return user

from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend


class UsernameOrEmailBackend(ModelBackend):
    """Permite autenticar com username ou e-mail no mesmo campo de login."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        user_model = get_user_model()
        identifier = (username or kwargs.get(user_model.USERNAME_FIELD) or "").strip()

        if not identifier or password is None:
            return None

        user = None

        # E-mail tem comparação case-insensitive.
        if "@" in identifier:
            user = user_model.objects.filter(email__iexact=identifier).first()

        # Fallback para username (também case-insensitive para melhor UX).
        if user is None:
            user = user_model.objects.filter(username__iexact=identifier).first()

        if user and user.check_password(password) and self.user_can_authenticate(user):
            return user

        return None

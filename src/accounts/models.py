import uuid

from django.contrib.auth.models import AbstractUser
from django.db import models, transaction
from django.db.models import F
from django.utils import timezone


class CustomUser(AbstractUser):
    """User model customizado com email único e obrigatório"""

    email = models.EmailField(
        unique=True, blank=False, null=False, verbose_name="E-mail", help_text="Endereço de e-mail único do usuário"
    )

    class Meta:
        verbose_name = "Usuário"
        verbose_name_plural = "Usuários"
        # Constraint adicional para garantir email case-insensitive único
        constraints = [
            models.UniqueConstraint(
                models.functions.Lower("email"),
                name="unique_email_case_insensitive",
            ),
        ]

    def __str__(self):
        return self.username


class UserProfile(models.Model):
    """Perfil estendido do usuário com verificação de e-mail"""

    user = models.OneToOneField("accounts.CustomUser", on_delete=models.CASCADE, related_name="profile")
    email_verified = models.BooleanField(default=False)
    verification_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    token_created_at = models.DateTimeField(auto_now_add=True)
    profile_image = models.FileField(upload_to="profiles/", blank=True, null=True)
    favorite_team = models.CharField(max_length=120, blank=True)
    world_cup_team = models.ForeignKey(
        "football.Team",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="supporter_profiles",
    )

    def __str__(self):
        return f"Profile: {self.user.username}"

    def is_token_valid(self):
        """Verifica se o token ainda é válido (24 horas)"""
        if self.email_verified:
            return False
        expiration = self.token_created_at + timezone.timedelta(hours=24)
        return timezone.now() < expiration

    def generate_new_token(self):
        """Gera um novo token de verificação"""
        self.verification_token = uuid.uuid4()
        self.token_created_at = timezone.now()
        self.save()


class InviteToken(models.Model):
    """Token de convite para registro de novos usuários"""

    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    created_by = models.ForeignKey(
        "accounts.CustomUser", on_delete=models.CASCADE, related_name="invite_tokens_created"
    )
    pool = models.ForeignKey(
        "pool.Pool", on_delete=models.SET_NULL, null=True, blank=True, related_name="invite_tokens"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    max_uses = models.IntegerField(default=10, help_text="Número máximo de usos (0 = ilimitado)")
    uses_count = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Token: {self.expires_at} - {self.token}"

    def is_valid(self):
        """Verifica se o token ainda é válido"""
        if not self.is_active:
            return False

        if self.expires_at and timezone.now() > self.expires_at:
            return False

        # Token válido se não tem limite ou ainda não atingiu o limite
        return self.max_uses == 0 or self.uses_count < self.max_uses

    def use(self):
        """Registra o uso do token"""
        self.uses_count += 1
        if self.max_uses > 0 and self.uses_count >= self.max_uses:
            self.is_active = False
        self.save()

    @classmethod
    def use_token(cls, token_uuid):
        """Registra o uso do token de forma atômica, prevenindo race conditions"""

        try:
            with transaction.atomic():
                # Travar a linha do token para evitar consumo simultâneo
                token = cls.objects.select_for_update().get(token=token_uuid)

                if not token.is_valid():
                    return False

                # Atualização atômica do contador
                token.uses_count = F("uses_count") + 1
                token.save(update_fields=["uses_count"])
                token.refresh_from_db(fields=["uses_count", "max_uses"])

                # Desativar se atingir o limite
                if token.max_uses > 0 and token.uses_count >= token.max_uses:
                    token.is_active = False
                    token.save(update_fields=["is_active"])

                return True
        except cls.DoesNotExist:
            return False

import uuid

from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone


class InviteToken(models.Model):
    """Token de convite para registro de novos usuários"""

    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    created_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name="invite_tokens_created")
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

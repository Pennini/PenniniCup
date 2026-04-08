from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from src.pool.models import Pool, PoolParticipant


class RankingTieBreakOverride(models.Model):
    pool = models.ForeignKey(Pool, on_delete=models.CASCADE, related_name="ranking_tie_break_overrides")
    participant = models.ForeignKey(
        PoolParticipant,
        on_delete=models.CASCADE,
        related_name="ranking_tie_break_overrides",
    )
    manual_position = models.PositiveIntegerField()
    reason = models.CharField(max_length=255, blank=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="ranking_tie_break_overrides_updated",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = (
            ("pool", "participant"),
            ("pool", "manual_position"),
        )
        ordering = ["pool", "manual_position", "participant_id"]

    def __str__(self):
        return f"{self.pool.slug} - {self.participant.user} - {self.manual_position}"

    def clean(self):
        if self.participant_id and self.pool_id and self.participant.pool_id != self.pool_id:
            raise ValidationError("Participante deve pertencer ao mesmo bolao do override.")

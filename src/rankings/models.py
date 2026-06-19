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


class PoolRankingHistory(models.Model):
    pool = models.ForeignKey(Pool, on_delete=models.CASCADE, related_name="ranking_history")
    participant = models.ForeignKey(PoolParticipant, on_delete=models.CASCADE, related_name="ranking_history")
    match = models.ForeignKey("football.Match", on_delete=models.CASCADE, related_name="ranking_history")
    round_index = models.PositiveIntegerField()
    position = models.PositiveIntegerField()

    total_points = models.IntegerField(default=0)
    group_points = models.IntegerField(default=0)
    knockout_points = models.IntegerField(default=0)
    exact_score_hits = models.IntegerField(default=0)
    advancing_hits = models.IntegerField(default=0)
    champion_hit = models.BooleanField(default=False)
    top_scorer_hit = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = (("pool", "participant", "match"),)
        indexes = [
            models.Index(fields=["pool", "round_index"], name="pool_rank_hist_round_idx"),
        ]
        ordering = ["pool", "round_index", "position"]

    def __str__(self):
        return f"{self.pool.slug} r{self.round_index} #{self.position} {self.participant.user}"

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


class PoolRankingSnapshotJob(models.Model):
    """Fila de snapshot de ranking por jogo encerrado.

    Tira o `snapshot_round_for_match` do caminho do request: o signal de Match
    só enfileira (1 linha por jogo); o worker constrói o leaderboard e grava o
    histórico fora do ciclo do request. Idempotente — re-enfileirar o mesmo jogo
    (correção de placar) reusa a linha e o UPSERT do snapshot mantém o round_index.
    """

    STATUS_PENDING = "PENDING"
    STATUS_PROCESSING = "PROCESSING"
    STATUS_FAILED = "FAILED"
    STATUS_IDLE = "IDLE"

    STATUS_CHOICES = (
        (STATUS_PENDING, "Pending"),
        (STATUS_PROCESSING, "Processing"),
        (STATUS_FAILED, "Failed"),
        (STATUS_IDLE, "Idle"),
    )

    match = models.OneToOneField(
        "football.Match",
        on_delete=models.CASCADE,
        related_name="ranking_snapshot_job",
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    requested_at = models.DateTimeField(auto_now=True)
    last_started_at = models.DateTimeField(null=True, blank=True)
    last_finished_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True)

    class Meta:
        ordering = ["-requested_at"]

    def __str__(self):
        return f"SnapshotQueue {self.match_id} ({self.status})"


class PoolDashboardSnapshot(models.Model):
    """Cache do payload pesado da dashboard de visão geral, por bolão.

    A dashboard recomputava tudo (hall da fama, evolução, aproveitamento) a cada
    acesso. Aqui guardamos só a parte *pool-wide* (igual para todos) num JSON; o
    request sobrepõe os dados baratos por participante (KPIs, flags). Recalculado
    fora do request pelo worker quando um placar muda — mesmo gatilho do snapshot
    de ranking (ver [[PoolDashboardSnapshotJob]]).
    """

    pool = models.OneToOneField(Pool, on_delete=models.CASCADE, related_name="dashboard_snapshot")
    payload = models.JSONField(default=dict)
    computed_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"DashboardSnapshot {self.pool_id}"


class PoolDashboardSnapshotJob(models.Model):
    """Fila de recálculo da dashboard de visão geral, por bolão.

    Espelha `PoolRankingSnapshotJob`: tira o agregado pesado do caminho do
    request. Enfileirado *depois* do snapshot de ranking gravar o histórico (a
    dashboard lê `PoolRankingHistory`), garantindo que o worker recompute sobre
    dados frescos.
    """

    STATUS_PENDING = "PENDING"
    STATUS_PROCESSING = "PROCESSING"
    STATUS_FAILED = "FAILED"
    STATUS_IDLE = "IDLE"

    STATUS_CHOICES = (
        (STATUS_PENDING, "Pending"),
        (STATUS_PROCESSING, "Processing"),
        (STATUS_FAILED, "Failed"),
        (STATUS_IDLE, "Idle"),
    )

    pool = models.OneToOneField(Pool, on_delete=models.CASCADE, related_name="dashboard_snapshot_job")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    requested_at = models.DateTimeField(auto_now=True)
    last_started_at = models.DateTimeField(null=True, blank=True)
    last_finished_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True)

    class Meta:
        ordering = ["-requested_at"]

    def __str__(self):
        return f"DashboardQueue {self.pool_id} ({self.status})"

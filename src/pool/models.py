from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import DecimalField, Exists, OuterRef, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from src.accounts.models import InviteToken
from src.football.models import Group, Match, Season, Team
from src.payments.models import Payment
from src.pool.services.rules import PHASE_GROUP, PHASE_KNOCKOUT, phase_for_match


class Pool(models.Model):
    name = models.CharField(max_length=120, unique=True)
    slug = models.SlugField(max_length=120, unique=True)
    season = models.ForeignKey(Season, on_delete=models.CASCADE, related_name="pools")
    description = models.TextField(blank=True)
    entry_fee = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    admin_fee_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("5.00"))
    admin_fee_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    total_prize_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    first_place_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("70.00"))
    second_place_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("20.00"))
    third_place_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("10.00"))
    first_place_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    second_place_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    third_place_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    requires_payment = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="pools_created")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def get_phase_lock_time(self, phase):
        custom_lock = self.lock_windows.filter(phase=phase).first()
        if custom_lock:
            return custom_lock.lock_at

        season_matches = Match.objects.filter(season=self.season).select_related("stage")
        phase_matches = [match for match in season_matches if phase_for_match(match) == phase]
        if not phase_matches:
            return None
        return min(match.match_date_brasilia for match in phase_matches)

    def is_phase_locked(self, phase, now=None):
        now = now or timezone.now()
        lock_time = self.get_phase_lock_time(phase)
        if lock_time is None:
            return False
        return now >= lock_time

    def get_invite_token(self, token_value):
        return InviteToken.objects.filter(token=token_value).select_related("pool").first()

    def validate_invite_token(self, token_value):
        token_obj = self.get_invite_token(token_value)
        if not token_obj:
            return None, "Token de convite invalido."

        if token_obj.pool_id != self.id:
            return None, "Este token nao pertence ao bolao selecionado."

        if not token_obj.is_valid():
            return None, "Token de convite expirado, inativo ou sem usos disponiveis."

        return token_obj, None

    def consume_invite_token(self, token_obj):
        return InviteToken.use_token(token_obj.token)

    def get_scoring_config(self):
        config, _ = PoolScoringConfig.objects.get_or_create(pool=self)
        return config

    def refresh_prize_distribution(self, save=True):
        active_participant_subquery = PoolParticipant.objects.filter(
            pool_id=self.id,
            user_id=OuterRef("user_id"),
            is_active=True,
        )

        total_paid = (
            Payment.objects.filter(
                pool=self,
                status="approved",
            )
            .filter(Exists(active_participant_subquery))
            .aggregate(
                total=Coalesce(
                    Sum(
                        Coalesce(
                            "amount_received", "amount", output_field=DecimalField(max_digits=10, decimal_places=2)
                        )
                    ),
                    Decimal("0.00"),
                )
            )
            .get("total", Decimal("0.00"))
        )

        total_paid = Decimal(total_paid).quantize(Decimal("0.01"))
        admin_fee_amount = (total_paid * self.admin_fee_percentage / Decimal("100")).quantize(Decimal("0.01"))
        total_prize_amount = (total_paid - admin_fee_amount).quantize(Decimal("0.01"))

        first_amount = (total_prize_amount * self.first_place_percentage / Decimal("100")).quantize(Decimal("0.01"))
        second_amount = (total_prize_amount * self.second_place_percentage / Decimal("100")).quantize(Decimal("0.01"))
        third_amount = (total_prize_amount - first_amount - second_amount).quantize(Decimal("0.01"))

        self.admin_fee_amount = admin_fee_amount
        self.total_prize_amount = total_prize_amount
        self.first_place_amount = first_amount
        self.second_place_amount = second_amount
        self.third_place_amount = third_amount

        if save:
            self.save(
                update_fields=[
                    "admin_fee_amount",
                    "total_prize_amount",
                    "first_place_amount",
                    "second_place_amount",
                    "third_place_amount",
                    "updated_at",
                ]
            )

    def get_official_results(self):
        results, _ = PoolOfficialResult.objects.get_or_create(pool=self)
        return results


class PoolLockWindow(models.Model):
    PHASE_CHOICES = (
        (PHASE_GROUP, "Fase de grupos"),
        (PHASE_KNOCKOUT, "Mata-mata"),
    )

    pool = models.ForeignKey(Pool, on_delete=models.CASCADE, related_name="lock_windows")
    phase = models.CharField(max_length=20, choices=PHASE_CHOICES)
    lock_at = models.DateTimeField()

    class Meta:
        unique_together = ("pool", "phase")
        ordering = ["phase"]

    def __str__(self):
        return f"{self.pool.name} - {self.phase}"


class PoolParticipant(models.Model):
    pool = models.ForeignKey(Pool, on_delete=models.CASCADE, related_name="participants")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="pool_participations")
    joined_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)

    # Campos agregados de ranking e desempate.
    total_points = models.IntegerField(default=0)
    group_points = models.IntegerField(default=0)
    knockout_points = models.IntegerField(default=0)
    exact_score_hits = models.IntegerField(default=0)
    winner_or_draw_hits = models.IntegerField(default=0)
    bonus_points = models.IntegerField(default=0)
    champion_hit = models.BooleanField(default=False)
    top_scorer_hit = models.BooleanField(default=False)
    champion_pred = models.ForeignKey(
        Team,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="participant_champion_predictions",
    )
    runner_up_pred = models.ForeignKey(
        Team,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="participant_runner_up_predictions",
    )
    third_place_pred = models.ForeignKey(
        Team,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="participant_third_place_predictions",
    )
    top_scorer_pred = models.ForeignKey(
        "football.Player",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="participant_top_scorer_predictions",
    )

    class Meta:
        unique_together = ("pool", "user")
        ordering = ["-total_points", "joined_at"]
        indexes = [
            models.Index(fields=["-total_points", "joined_at"], name="pool_part_rank_idx"),
        ]

    def __str__(self):
        return f"{self.user} @ {self.pool}"

    def can_bet(self):
        if not self.is_active:
            return False
        if self.pool.requires_payment:
            return Payment.objects.filter(user=self.user, pool=self.pool, status="approved").exists()
        return True


class PoolBet(models.Model):
    participant = models.ForeignKey(PoolParticipant, on_delete=models.CASCADE, related_name="bets")
    match = models.ForeignKey(Match, on_delete=models.CASCADE, related_name="pool_bets")

    home_score_pred = models.PositiveSmallIntegerField(null=True, blank=True)
    away_score_pred = models.PositiveSmallIntegerField(null=True, blank=True)
    winner_pred = models.ForeignKey(
        Team, null=True, blank=True, on_delete=models.SET_NULL, related_name="predicted_wins"
    )
    is_active = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("participant", "match")
        ordering = ["match__match_date_brasilia", "match__match_number"]

    def __str__(self):
        return f"Palpite {self.participant_id}:{self.match_id}"

    def _has_scores(self):
        return self.home_score_pred is not None and self.away_score_pred is not None

    def _is_empty_prediction(self):
        return self.home_score_pred is None and self.away_score_pred is None and self.winner_pred_id is None

    def refresh_is_active(self):
        phase = phase_for_match(self.match)
        if phase == PHASE_KNOCKOUT:
            if not self._has_scores():
                self.is_active = False
            elif self.home_score_pred == self.away_score_pred:
                self.is_active = self.winner_pred_id is not None
            else:
                self.is_active = True
        else:
            self.is_active = self._has_scores()

    def save(self, *args, **kwargs):
        if self.match_id:
            self.refresh_is_active()
        super().save(*args, **kwargs)

    def clean(self):
        if self.match.season_id != self.participant.pool.season_id:
            raise ValidationError("Partida fora da temporada do bolao.")

        if self._is_empty_prediction():
            self.is_active = False
            return

        phase = phase_for_match(self.match)
        if self.participant.pool.is_phase_locked(phase):
            raise ValidationError("Janela de palpites desta fase esta fechada.")

        if not self.participant.can_bet():
            raise ValidationError("Participante sem permissao para palpitar.")

        if self.home_score_pred is None or self.away_score_pred is None:
            raise ValidationError("Informe o placar completo da partida.")

        if phase == PHASE_KNOCKOUT:
            if self.home_score_pred == self.away_score_pred and self.winner_pred is None:
                self.is_active = False
                return

            if self.home_score_pred > self.away_score_pred and self.match.home_team_id:
                self.winner_pred = self.match.home_team
            elif self.away_score_pred > self.home_score_pred and self.match.away_team_id:
                self.winner_pred = self.match.away_team

        if self.match.home_team_id and self.match.away_team_id and self.winner_pred_id:
            valid_winners = {self.match.home_team_id, self.match.away_team_id}
            if self.winner_pred_id not in valid_winners:
                raise ValidationError("Classificado deve ser um dos times da partida.")

        self.refresh_is_active()


class PoolBetScore(models.Model):
    bet = models.OneToOneField(PoolBet, on_delete=models.CASCADE, related_name="score")
    points = models.IntegerField(default=0)
    exact_score = models.BooleanField(default=False)
    winner_or_draw = models.BooleanField(default=False)
    winner_advancing = models.BooleanField(default=False)
    one_team_score = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Score bet {self.bet_id}: {self.points}"


class PoolParticipantStanding(models.Model):
    participant = models.ForeignKey(PoolParticipant, on_delete=models.CASCADE, related_name="projected_standings")
    group = models.ForeignKey("football.Group", on_delete=models.CASCADE, related_name="participant_standings")
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="participant_standings")

    position = models.PositiveSmallIntegerField()
    played = models.PositiveSmallIntegerField(default=0)
    won = models.PositiveSmallIntegerField(default=0)
    drawn = models.PositiveSmallIntegerField(default=0)
    lost = models.PositiveSmallIntegerField(default=0)
    goals_for = models.PositiveSmallIntegerField(default=0)
    goals_against = models.PositiveSmallIntegerField(default=0)
    goal_difference = models.SmallIntegerField(default=0)
    points = models.PositiveSmallIntegerField(default=0)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("participant", "group", "team")
        ordering = ["group__name", "position", "team__code"]

    def __str__(self):
        return f"Standing {self.participant_id}:{self.group.name}:{self.team.code}"


class PoolParticipantThirdPlace(models.Model):
    participant = models.ForeignKey(PoolParticipant, on_delete=models.CASCADE, related_name="projected_third_places")
    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name="participant_third_places")
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="participant_third_places")

    position_global = models.PositiveSmallIntegerField()
    points = models.PositiveSmallIntegerField(default=0)
    goal_difference = models.SmallIntegerField(default=0)
    goals_for = models.PositiveSmallIntegerField(default=0)
    score = models.IntegerField(default=0)
    is_qualified = models.BooleanField(default=False)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("participant", "group")
        ordering = ["position_global", "group__name", "team__code"]

    def __str__(self):
        return f"Third {self.participant_id}:{self.group.name}:{self.team.code}"


class PoolProjectionRecalc(models.Model):
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

    participant = models.OneToOneField(
        PoolParticipant,
        on_delete=models.CASCADE,
        related_name="projection_recalc",
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
        return f"ProjectionQueue {self.participant_id} ({self.status})"


class PoolScoringConfig(models.Model):
    pool = models.OneToOneField(Pool, on_delete=models.CASCADE, related_name="scoring_config")

    group_winner_or_draw_points = models.PositiveSmallIntegerField(default=6)
    group_exact_score_points = models.PositiveSmallIntegerField(default=4)
    group_one_team_score_points = models.PositiveSmallIntegerField(default=2)

    knockout_winner_advancing_points = models.PositiveSmallIntegerField(default=8)
    knockout_exact_score_points = models.PositiveSmallIntegerField(default=6)
    knockout_one_team_score_points = models.PositiveSmallIntegerField(default=2)

    bonus_champion_points = models.PositiveSmallIntegerField(default=50)
    bonus_runner_up_points = models.PositiveSmallIntegerField(default=30)
    bonus_third_place_points = models.PositiveSmallIntegerField(default=20)
    bonus_top_scorer_points = models.PositiveSmallIntegerField(default=50)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Configuracao de pontuacao"
        verbose_name_plural = "Configuracoes de pontuacao"

    def __str__(self):
        return f"Pontuacao {self.pool.slug}"


class PoolOfficialResult(models.Model):
    pool = models.OneToOneField(Pool, on_delete=models.CASCADE, related_name="official_result")
    champion = models.ForeignKey(
        Team,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="official_champion_pools",
    )
    runner_up = models.ForeignKey(
        Team,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="official_runner_up_pools",
    )
    third_place = models.ForeignKey(
        Team,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="official_third_place_pools",
    )
    top_scorer = models.ForeignKey(
        "football.Player",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="official_top_scorer_pools",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Resultado oficial do bolao"
        verbose_name_plural = "Resultados oficiais do bolao"

    def __str__(self):
        return f"Resultado oficial {self.pool.slug}"

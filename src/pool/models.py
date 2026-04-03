from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from src.football.models import Match, Season, Team
from src.pool.services.rules import PHASE_GROUP, PHASE_KNOCKOUT, phase_for_match


class Pool(models.Model):
    name = models.CharField(max_length=120, unique=True)
    slug = models.SlugField(max_length=120, unique=True)
    season = models.ForeignKey(Season, on_delete=models.CASCADE, related_name="pools")
    description = models.TextField(blank=True)
    entry_fee = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
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
        from src.accounts.models import InviteToken

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
        from src.accounts.models import InviteToken

        return InviteToken.use_token(token_obj.token)


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
    champion_hit = models.BooleanField(default=False)
    top_scorer_hit = models.BooleanField(default=False)

    class Meta:
        unique_together = ("pool", "user")
        ordering = ["-total_points", "joined_at"]

    def __str__(self):
        return f"{self.user} @ {self.pool}"

    def can_bet(self):
        if not self.is_active:
            return False
        if self.pool.requires_payment:
            from src.payments.models import Payment

            return Payment.objects.filter(user=self.user, pool=self.pool, status="approved").exists()
        return True


class PoolBet(models.Model):
    participant = models.ForeignKey(PoolParticipant, on_delete=models.CASCADE, related_name="bets")
    match = models.ForeignKey(Match, on_delete=models.CASCADE, related_name="pool_bets")

    home_score_pred = models.PositiveSmallIntegerField()
    away_score_pred = models.PositiveSmallIntegerField()
    winner_pred = models.ForeignKey(
        Team, null=True, blank=True, on_delete=models.SET_NULL, related_name="predicted_wins"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("participant", "match")
        ordering = ["match__match_date_brasilia", "match__match_number"]

    def __str__(self):
        return f"Palpite {self.participant_id}:{self.match_id}"

    def clean(self):
        if self.match.season_id != self.participant.pool.season_id:
            raise ValidationError("Partida fora da temporada do bolao.")

        phase = phase_for_match(self.match)
        if self.participant.pool.is_phase_locked(phase):
            raise ValidationError("Janela de palpites desta fase esta fechada.")

        if not self.participant.can_bet():
            raise ValidationError("Participante sem permissao para palpitar.")

        if phase == PHASE_KNOCKOUT and self.winner_pred is None:
            raise ValidationError("No mata-mata e obrigatorio informar o classificado.")

        if self.match.home_team_id and self.match.away_team_id and self.winner_pred_id:
            valid_winners = {self.match.home_team_id, self.match.away_team_id}
            if self.winner_pred_id not in valid_winners:
                raise ValidationError("Classificado deve ser um dos times da partida.")


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

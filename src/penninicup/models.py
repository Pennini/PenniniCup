# Create your models here.
import uuid

from django.conf import settings
from django.db import models

# =========================
# ENUMS
# =========================


class AppRole(models.TextChoices):
    ADMIN = "admin", "Admin"
    USER = "user", "User"


class TournamentPhase(models.TextChoices):
    GROUP_STAGE = "group_stage", "Group Stage"
    ROUND_OF_16 = "round_of_16", "Round of 16"
    QUARTER_FINALS = "quarter_finals", "Quarter Finals"
    SEMI_FINALS = "semi_finals", "Semi Finals"
    THIRD_PLACE = "third_place", "Third Place"
    FINAL = "final", "Final"


# =========================
# USERS & ROLES
# =========================


class Profile(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile")

    nickname = models.CharField(max_length=100)
    avatar_url = models.URLField(blank=True, null=True)
    favorite_team = models.CharField(max_length=100, blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.nickname


class UserRole(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="roles")

    role = models.CharField(max_length=10, choices=AppRole.choices, default=AppRole.USER)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("user", "role")

    def __str__(self):
        return f"{self.user} - {self.role}"


# =========================
# POOLS (BOLÕES)
# =========================


class Pool(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)

    invite_token = models.CharField(max_length=64, unique=True)
    entry_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    first_place_prize = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    second_place_prize = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    third_place_prize = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    predictions_locked = models.BooleanField(default=False)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="created_pools"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name


class PoolMember(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    pool = models.ForeignKey(Pool, on_delete=models.CASCADE, related_name="members")

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="pool_memberships")

    has_paid = models.BooleanField(default=False)
    total_points = models.IntegerField(default=0)
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("pool", "user")

    def __str__(self):
        return f"{self.user} @ {self.pool}"


# =========================
# TOURNAMENT
# =========================


class Team(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    name = models.CharField(max_length=100, unique=True)
    code = models.CharField(max_length=10, unique=True)
    flag_url = models.URLField(blank=True, null=True)
    group_name = models.CharField(max_length=10, blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class Match(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    pool = models.ForeignKey(Pool, on_delete=models.CASCADE, related_name="matches")

    home_team = models.ForeignKey(Team, on_delete=models.SET_NULL, null=True, related_name="home_matches")

    away_team = models.ForeignKey(Team, on_delete=models.SET_NULL, null=True, related_name="away_matches")

    home_team_score = models.IntegerField(blank=True, null=True)
    away_team_score = models.IntegerField(blank=True, null=True)

    phase = models.CharField(max_length=20, choices=TournamentPhase.choices, default=TournamentPhase.GROUP_STAGE)

    match_date = models.DateTimeField()
    is_finished = models.BooleanField(default=False)

    # Pontuação configurável por jogo
    points_exact = models.IntegerField(default=10)
    points_winner = models.IntegerField(default=6)
    points_one_score = models.IntegerField(default=3)
    points_qualified = models.IntegerField(default=5)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.home_team} x {self.away_team}"


# =========================
# PREDICTIONS (PALPITES)
# =========================


class Prediction(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="predictions")

    match = models.ForeignKey(Match, on_delete=models.CASCADE, related_name="predictions")

    home_score = models.IntegerField()
    away_score = models.IntegerField()
    points_earned = models.IntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("user", "match")

    def __str__(self):
        return f"{self.user} → {self.match}"

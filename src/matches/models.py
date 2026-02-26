from django.db import models
from django.utils import timezone

STAGE_CHOICES = [
    ("GROUP", "Fase de Grupos"),
    ("R32", "32 avos de final"),
    ("R16", "Oitavas de final"),
    ("QF", "Quartas de final"),
    ("SF", "Semifinal"),
    ("FINAL", "Final"),
]

KNOCKOUT_ROUNDS = ["R32", "R16", "QF", "SF", "FINAL"]

SIDE_CHOICES = [
    ("HOME", "Home"),
    ("AWAY", "Away"),
]


class Team(models.Model):
    api_id = models.IntegerField(unique=True)
    api_fonte = models.CharField(max_length=100)

    name = models.CharField(max_length=100)
    short_name = models.CharField(max_length=50)
    code = models.CharField(max_length=3)  # Exemplo: BRA, ARG

    crest_url = models.URLField(max_length=200, null=True, blank=True)

    def __str__(self):
        return self.name


class Player(models.Model):
    name = models.CharField(max_length=100)
    team = models.ForeignKey(Team, on_delete=models.CASCADE)
    goals = models.IntegerField(default=0)

    def __str__(self):
        return self.name


# Create your models here.
class Match(models.Model):
    api_id = models.IntegerField(unique=True)
    api_fonte = models.CharField(max_length=100)
    api_matchday = models.IntegerField()
    api_ultimo_update = models.DateTimeField()

    home_team = models.ForeignKey(Team, related_name="home_matches", on_delete=models.CASCADE)
    away_team = models.ForeignKey(Team, related_name="away_matches", on_delete=models.CASCADE)
    winner = models.ForeignKey(Team, null=True, blank=True, related_name="won_matches", on_delete=models.CASCADE)

    home_score = models.IntegerField(null=True, blank=True)
    away_score = models.IntegerField(null=True, blank=True)

    start_time = models.DateTimeField()

    stage = models.CharField(max_length=20, choices=STAGE_CHOICES)

    finished = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def is_knockout(self):
        return self.stage in KNOCKOUT_ROUNDS

    def has_started(self):
        return timezone.now() >= self.start_time

    def is_draw(self):
        return self.finished and self.home_score == self.away_score

    def winner_team(self):
        return self.winner

    def __str__(self):
        return f"{self.home_team.code} X {self.away_team.code} - {self.start_time.strftime('%Y-%m-%d %H:%M')}"


class GroupStage(models.Model):
    """Representa um grupo da fase de grupos (ex: Grupo A, Grupo B)."""

    name = models.CharField(max_length=10)  # ex: "A", "B", "C"

    def __str__(self):
        return f"Grupo {self.name}"


class GroupEntry(models.Model):
    """Tabela de classificação de um time dentro de um grupo."""

    group = models.ForeignKey(GroupStage, related_name="entries", on_delete=models.CASCADE)
    team = models.ForeignKey(Team, on_delete=models.CASCADE)

    points = models.IntegerField(default=0)
    games = models.IntegerField(default=0)
    wins = models.IntegerField(default=0)
    draws = models.IntegerField(default=0)
    losses = models.IntegerField(default=0)
    goals_for = models.IntegerField(default=0)
    goals_against = models.IntegerField(default=0)
    goal_difference = models.IntegerField(default=0)
    position = models.IntegerField(null=True, blank=True)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("group", "team")

    def __str__(self):
        return f"{self.team.code} - Grupo {self.group.name}"


class Knockout(models.Model):
    match = models.OneToOneField(Match, on_delete=models.CASCADE)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.match}"


class KnockoutSlot(models.Model):
    knockout = models.ForeignKey(Knockout, related_name="slots", on_delete=models.CASCADE)

    group = models.ForeignKey(GroupStage, related_name="knockout_slots", on_delete=models.CASCADE)
    position_in_group = models.PositiveSmallIntegerField()  # 1 ou 2

    side = models.CharField(max_length=4, choices=SIDE_CHOICES)

    def __str__(self):
        return f"{self.group.name}{self.position_in_group} → {self.side}"

    @staticmethod
    def resolve_knockout(knockout):
        for slot in knockout.slots.all():
            team = GroupEntry.objects.get(group=slot.group, position=slot.position_in_group).team

            if slot.side == "HOME":
                knockout.match.home_team = team
            else:
                knockout.match.away_team = team

        knockout.match.save()

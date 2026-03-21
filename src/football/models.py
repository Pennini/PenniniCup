from django.db import models


class Competition(models.Model):
    fifa_id = models.IntegerField(unique=True)
    name = models.CharField(max_length=255)
    gender = models.CharField(max_length=20, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Season(models.Model):
    fifa_id = models.IntegerField(unique=True)
    competition = models.ForeignKey(Competition, on_delete=models.CASCADE, related_name="seasons")
    name = models.CharField(max_length=255)
    year = models.IntegerField()
    start_date = models.DateField()
    end_date = models.DateField()

    class Meta:
        ordering = ["-year"]

    def __str__(self):
        return f"{self.name} ({self.year})"


class Stage(models.Model):
    fifa_id = models.CharField(max_length=20, unique=True)
    season = models.ForeignKey(Season, on_delete=models.CASCADE, related_name="stages")
    name = models.CharField(max_length=255)
    order = models.PositiveIntegerField(blank=True, null=True, unique=True)
    sync_status = models.BooleanField(default=True)

    create_date = models.DateTimeField(auto_now_add=True)
    update_date = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["order"]

    def __str__(self):
        return self.name


class Group(models.Model):
    fifa_id = models.CharField(max_length=20, unique=True)
    stage = models.ForeignKey(Stage, on_delete=models.CASCADE, related_name="groups")
    name = models.CharField(max_length=50)
    sync_status = models.BooleanField(default=True)

    create_date = models.DateTimeField(auto_now_add=True)
    update_date = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


# =========================
# TEAMS & PLAYERS
# =========================


class Team(models.Model):
    fifa_id = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=255, unique=True)
    name_norm = models.CharField(max_length=100)
    code = models.CharField(max_length=10, unique=True)
    confederation = models.CharField(max_length=50, blank=True)
    flag_url = models.URLField(blank=True)
    page_url = models.URLField(blank=True)
    flag_local = models.CharField(max_length=255, blank=True)
    group = models.ForeignKey(Group, on_delete=models.SET_NULL, null=True, blank=True, related_name="teams")
    is_host = models.BooleanField(default=False, blank=True, null=True)
    appearances = models.PositiveIntegerField(default=0)
    world_ranking = models.PositiveIntegerField(null=True, blank=True)
    sync_status = models.BooleanField(default=True)

    create_date = models.DateTimeField(auto_now_add=True)
    update_date = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return f"{self.name} ({self.code})"


class Player(models.Model):
    fifa_id = models.CharField(max_length=20, unique=True)
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="players")
    name = models.CharField(max_length=255)
    short_name = models.CharField(max_length=100, blank=True)
    position = models.CharField(max_length=50)
    shirt_number = models.PositiveIntegerField(null=True, blank=True)
    sync_status = models.BooleanField(default=True)

    create_date = models.DateTimeField(auto_now_add=True)
    update_date = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} - {self.team.code}"


class Official(models.Model):
    fifa_id = models.CharField(max_length=20, unique=True)
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="officials")
    name = models.CharField(max_length=255)
    short_name = models.CharField(max_length=100, blank=True)
    role_code = models.IntegerField()
    role_description = models.CharField(max_length=255, blank=True, null=True)
    sync_status = models.BooleanField(default=True)

    create_date = models.DateTimeField(auto_now_add=True)
    update_date = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} - {self.team.code}"


# =========================
# STADIUMS
# =========================


class Stadium(models.Model):
    fifa_id = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=255)
    city = models.CharField(max_length=255, blank=True, null=True)
    country_code = models.CharField(max_length=10, blank=True, null=True)
    sync_status = models.BooleanField(default=True)

    create_date = models.DateTimeField(auto_now_add=True)
    update_date = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


# =========================
# MATCHES
# =========================


class Match(models.Model):
    STATUS_SCHEDULED = 1
    STATUS_FINISHED = 0

    STATUS_CHOICES = (
        (STATUS_SCHEDULED, "Scheduled"),
        (STATUS_FINISHED, "Finished"),
    )

    fifa_id = models.CharField(max_length=20, unique=True)

    season = models.ForeignKey(Season, on_delete=models.CASCADE, related_name="matches")
    stage = models.ForeignKey(Stage, on_delete=models.CASCADE, related_name="matches")
    group = models.ForeignKey(Group, on_delete=models.SET_NULL, null=True, blank=True, related_name="matches")

    match_number = models.PositiveIntegerField()

    match_date_utc = models.DateTimeField()
    match_date_local = models.DateTimeField()
    match_date_brasilia = models.DateTimeField()

    stadium = models.ForeignKey(Stadium, on_delete=models.SET_NULL, null=True, related_name="matches")

    home_team = models.ForeignKey(Team, on_delete=models.SET_NULL, null=True, blank=True, related_name="home_matches")
    away_team = models.ForeignKey(Team, on_delete=models.SET_NULL, null=True, blank=True, related_name="away_matches")

    # Suporte a placeholder (A1, B2, etc)
    home_placeholder = models.CharField(max_length=50, blank=True)
    away_placeholder = models.CharField(max_length=50, blank=True)

    home_score = models.PositiveIntegerField(null=True, blank=True)
    away_score = models.PositiveIntegerField(null=True, blank=True)

    home_penalty_score = models.PositiveIntegerField(null=True, blank=True)
    away_penalty_score = models.PositiveIntegerField(null=True, blank=True)

    winner = models.ForeignKey(Team, on_delete=models.SET_NULL, null=True, blank=True, related_name="wins")

    status = models.IntegerField(choices=STATUS_CHOICES, default=STATUS_SCHEDULED)

    class Meta:
        ordering = ["match_date_utc"]

    def __str__(self):
        home = self.home_team.name if self.home_team else self.home_placeholder
        away = self.away_team.name if self.away_team else self.away_placeholder
        return f"{home} x {away}"


# =========================
# STANDINGS
# =========================


class Standing(models.Model):
    season = models.ForeignKey(Season, on_delete=models.CASCADE, related_name="standings")
    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name="standings")
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="standings")

    position = models.PositiveIntegerField()

    played = models.PositiveIntegerField(default=0)
    won = models.PositiveIntegerField(default=0)
    drawn = models.PositiveIntegerField(default=0)
    lost = models.PositiveIntegerField(default=0)

    goals_for = models.PositiveIntegerField(default=0)
    goals_against = models.PositiveIntegerField(default=0)
    goal_difference = models.IntegerField(default=0)

    points = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = ("season", "group", "team")
        ordering = ["group", "position"]

    def __str__(self):
        return f"{self.team} - {self.group}"

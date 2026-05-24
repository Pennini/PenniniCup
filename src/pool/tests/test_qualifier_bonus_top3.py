from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from src.football.models import Competition, Group, Match, Season, Stage, Standing, Team
from src.payments.models import Payment
from src.pool.models import Pool, PoolParticipant, PoolParticipantStanding

User = get_user_model()


class QualifierBonusBase(TestCase):
    """Builds 3 groups (A, B, C) of 4 teams + a Pool with one paid participant.

    Helpers:
      - set_real_position(group_name, position, team_index)
      - set_proj_position(group_name, position, team_index)
      - create_r32_match(home_group, home_index, away_group, away_index, *, fifa_id)
    """

    def setUp(self):
        self.user = User.objects.create_user(username="qb-user", email="qb@example.com", password="123456Aa!")
        self.competition = Competition.objects.create(fifa_id=100, name="QB Cup")
        self.season = Season.objects.create(
            fifa_id=100,
            competition=self.competition,
            name="QB 2026",
            year=2026,
            start_date="2026-06-01",
            end_date="2026-07-30",
        )
        self.group_stage = Stage.objects.create(fifa_id="QB-GROUP", season=self.season, name="Group", order=1)
        self.r32_stage = Stage.objects.create(fifa_id="QB-R32", season=self.season, name="R32", order=2)

        self.groups = {}
        self.teams_by_group = {}
        for group_name in ("A", "B", "C"):
            group = Group.objects.create(stage=self.group_stage, name=group_name, fifa_id=f"QB-G{group_name}")
            self.groups[group_name] = group
            self.teams_by_group[group_name] = []
            for i in range(1, 5):
                team = Team.objects.create(
                    fifa_id=f"QB-{group_name}{i}",
                    name=f"Team {group_name}{i}",
                    name_norm=f"team {group_name}{i}",
                    code=f"{group_name}{i}",
                    group=group,
                )
                self.teams_by_group[group_name].append(team)

        self.pool = Pool.objects.create(name="QB Pool", slug="qb-pool", season=self.season, created_by=self.user)
        self.participant = PoolParticipant.objects.create(pool=self.pool, user=self.user, is_active=True)
        Payment.objects.create(
            user=self.user,
            pool=self.pool,
            amount=self.pool.entry_fee,
            amount_received=self.pool.entry_fee,
            status="approved",
            payment_method="pix",
        )
        self.scoring_config = self.pool.get_scoring_config()
        assert self.scoring_config is not None

    def team(self, group_name, index):
        """Index is 1-based: team('A', 1) -> 'Team A1'."""
        return self.teams_by_group[group_name][index - 1]

    def set_real_position(self, group_name, position, team_index):
        team = self.team(group_name, team_index)
        Standing.objects.update_or_create(
            season=self.season,
            group=self.groups[group_name],
            team=team,
            defaults={"position": position, "points": 9 - position},
        )

    def set_proj_position(self, group_name, position, team_index):
        team = self.team(group_name, team_index)
        PoolParticipantStanding.objects.update_or_create(
            participant=self.participant,
            group=self.groups[group_name],
            team=team,
            defaults={"position": position, "points": 9 - position},
        )

    def create_r32_match(self, home_group, home_index, away_group, away_index, *, fifa_id):
        match_number = int(fifa_id[-2:]) if fifa_id[-2:].isdigit() else 90
        now = timezone.now()
        return Match.objects.create(
            fifa_id=fifa_id,
            season=self.season,
            stage=self.r32_stage,
            match_number=match_number,
            match_date_utc=now,
            match_date_local=now,
            match_date_brasilia=now + timezone.timedelta(hours=2),
            home_team=self.team(home_group, home_index),
            away_team=self.team(away_group, away_index),
        )

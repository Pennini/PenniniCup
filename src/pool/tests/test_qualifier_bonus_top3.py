from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from src.football.models import Competition, Group, Match, Season, Stage, Standing, Team
from src.payments.models import Payment
from src.pool.models import Pool, PoolParticipant, PoolParticipantStanding
from src.pool.services.ranking import _real_qualifier_position_map

User = get_user_model()

# Reasonable points-per-position for a 4-team group: clearly-separated values
# so tie-breakers don't accidentally fire in helper-driven test setups.
_POINTS_BY_POSITION = {1: 9, 2: 6, 3: 3, 4: 1}


class QualifierBonusBase(TestCase):
    """Builds 3 groups (A, B, C) of 4 teams + a Pool with one paid participant.

    Helpers:
      - set_real_position(group_name, position, team_index)
      - set_proj_position(group_name, position, team_index)
      - create_r32_match(home_group, home_index, away_group, away_index, *, fifa_id, match_number)
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
            defaults={"position": position, "points": _POINTS_BY_POSITION[position]},
        )

    def set_proj_position(self, group_name, position, team_index):
        team = self.team(group_name, team_index)
        PoolParticipantStanding.objects.update_or_create(
            participant=self.participant,
            group=self.groups[group_name],
            team=team,
            defaults={"position": position, "points": _POINTS_BY_POSITION[position]},
        )

    def create_r32_match(self, home_group, home_index, away_group, away_index, *, fifa_id, match_number):
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


class RealQualifierPositionMapTest(QualifierBonusBase):
    def test_no_standings_returns_empty_and_not_drawn(self):
        result, r32_drawn = _real_qualifier_position_map(self.season)
        self.assertEqual(result, {})
        self.assertFalse(r32_drawn)

    def test_top2_only_when_r32_not_drawn(self):
        self.set_real_position("A", 1, 1)
        self.set_real_position("A", 2, 2)
        self.set_real_position("A", 3, 3)
        self.set_real_position("A", 4, 4)

        result, r32_drawn = _real_qualifier_position_map(self.season)

        self.assertFalse(r32_drawn)
        gid = self.groups["A"].id
        self.assertEqual(
            result[gid],
            {1: self.team("A", 1).id, 2: self.team("A", 2).id},
        )

    def test_third_included_when_team_in_r32(self):
        for group_name in ("A", "B"):
            for pos in (1, 2, 3, 4):
                self.set_real_position(group_name, pos, pos)

        # A3 is placed in an R32 match → qualifies. B3 is not → does not.
        self.create_r32_match("A", 1, "B", 2, fifa_id="QB-R3201", match_number=101)
        self.create_r32_match("A", 3, "B", 1, fifa_id="QB-R3202", match_number=102)

        result, r32_drawn = _real_qualifier_position_map(self.season)

        self.assertTrue(r32_drawn)
        a_id = self.groups["A"].id
        b_id = self.groups["B"].id
        self.assertIn(3, result[a_id])
        self.assertEqual(result[a_id][3], self.team("A", 3).id)
        self.assertNotIn(3, result[b_id])

    def test_third_excluded_when_r32_empty_teams(self):
        for pos in (1, 2, 3, 4):
            self.set_real_position("A", pos, pos)

        # R32 match exists but no teams assigned yet.
        Match.objects.create(
            fifa_id="QB-R3299",
            season=self.season,
            stage=self.r32_stage,
            match_number=99,
            match_date_utc=timezone.now(),
            match_date_local=timezone.now(),
            match_date_brasilia=timezone.now() + timezone.timedelta(hours=2),
            home_team=None,
            away_team=None,
        )

        result, r32_drawn = _real_qualifier_position_map(self.season)
        self.assertFalse(r32_drawn)
        self.assertNotIn(3, result[self.groups["A"].id])

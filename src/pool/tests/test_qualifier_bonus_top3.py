from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from src.football.models import Competition, Group, Match, Season, Stage, Standing, Team
from src.payments.models import Payment
from src.pool.models import Pool, PoolParticipant, PoolParticipantStanding
from src.pool.services.ranking import (
    _calculate_group_qualifier_bonus,
    _real_qualifier_position_map,
    recalculate_participant_scores,
)

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
        # By default the group stage is over (its last match day already
        # passed), so the qualifier bonus is eligible. Use set_group_stage_*()
        # to flip this per test.
        self.group_match = Match.objects.create(
            fifa_id="QB-GROUPM",
            season=self.season,
            stage=self.group_stage,
            match_number=1,
            match_date_utc=timezone.now() - timezone.timedelta(days=2),
            match_date_local=timezone.now() - timezone.timedelta(days=2),
            match_date_brasilia=timezone.now() - timezone.timedelta(days=2),
        )

        self.scoring_config = self.pool.get_scoring_config()
        assert self.scoring_config is not None

    def set_group_stage_finished(self):
        self.group_match.match_date_brasilia = timezone.now() - timezone.timedelta(days=2)
        self.group_match.save(update_fields=["match_date_brasilia"])

    def set_group_stage_ongoing(self):
        self.group_match.match_date_brasilia = timezone.now() + timezone.timedelta(days=2)
        self.group_match.save(update_fields=["match_date_brasilia"])

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


class CalculateGroupQualifierBonusTopThreeTest(QualifierBonusBase):
    def _qpts(self):
        return self.scoring_config.group_qualifier_points

    def _pbonus(self):
        return self.scoring_config.group_qualifier_position_bonus

    def _set_full_group_real(self, group_name, draw_r32_for_third=True):
        # Team {name}1 → 1st, {name}2 → 2nd, {name}3 → 3rd, {name}4 → 4th.
        for pos in (1, 2, 3, 4):
            self.set_real_position(group_name, pos, pos)
        if draw_r32_for_third:
            # Put 3rd-place team in an R32 match so r32_drawn=True and team qualifies.
            self.create_r32_match(
                group_name,
                3,
                group_name,
                1,
                fifa_id=f"QB-RX{group_name}",
                match_number=200 + ord(group_name),
            )

    def test_predicted_third_finishes_third_and_advances(self):
        self._set_full_group_real("A")
        self.set_proj_position("A", 3, 3)
        result = _calculate_group_qualifier_bonus(self.participant, self.scoring_config)
        self.assertEqual(result, self._qpts() + self._pbonus())

    def test_predicted_third_finishes_third_but_does_not_advance(self):
        # Real top 4 set, but no R32 match places the 3rd team → does not qualify.
        for pos in (1, 2, 3, 4):
            self.set_real_position("A", pos, pos)
        # R32 only contains 1st/2nd teams.
        self.create_r32_match("A", 1, "A", 2, fifa_id="QB-RZ01", match_number=301)

        self.set_proj_position("A", 3, 3)
        result = _calculate_group_qualifier_bonus(self.participant, self.scoring_config)
        self.assertEqual(result, 0)

    def test_predicted_third_finishes_first(self):
        self._set_full_group_real("A")
        # Predict team A1 (the actual 1st) in 3rd slot.
        self.set_proj_position("A", 3, 1)
        result = _calculate_group_qualifier_bonus(self.participant, self.scoring_config)
        # Qualifies (A1 is in real top 2), no position match.
        self.assertEqual(result, self._qpts())

    def test_predicted_third_finishes_fourth(self):
        self._set_full_group_real("A")
        self.set_proj_position("A", 3, 4)
        result = _calculate_group_qualifier_bonus(self.participant, self.scoring_config)
        self.assertEqual(result, 0)

    def test_predicted_first_finishes_third_and_advances(self):
        self._set_full_group_real("A")
        # Predict team A3 (the actual 3rd) in 1st slot.
        self.set_proj_position("A", 1, 3)
        result = _calculate_group_qualifier_bonus(self.participant, self.scoring_config)
        # A3 is a real qualifier → qualifier_points, no position match.
        self.assertEqual(result, self._qpts())

    def test_predicted_first_finishes_third_but_does_not_advance(self):
        for pos in (1, 2, 3, 4):
            self.set_real_position("A", pos, pos)
        self.create_r32_match("A", 1, "A", 2, fifa_id="QB-RZ02", match_number=302)  # 3rd not in R32

        self.set_proj_position("A", 1, 3)
        result = _calculate_group_qualifier_bonus(self.participant, self.scoring_config)
        self.assertEqual(result, 0)

    def test_r32_not_drawn_predicted_third_is_zero(self):
        for pos in (1, 2, 3, 4):
            self.set_real_position("A", pos, pos)
        # No R32 match created at all → r32_drawn=False.

        self.set_proj_position("A", 3, 3)
        result = _calculate_group_qualifier_bonus(self.participant, self.scoring_config)
        self.assertEqual(result, 0)

    def test_perfect_top_three_match(self):
        self._set_full_group_real("A")
        self.set_proj_position("A", 1, 1)
        self.set_proj_position("A", 2, 2)
        self.set_proj_position("A", 3, 3)
        result = _calculate_group_qualifier_bonus(self.participant, self.scoring_config)
        self.assertEqual(result, 3 * (self._qpts() + self._pbonus()))

    def test_empty_standings_returns_zero(self):
        result = _calculate_group_qualifier_bonus(self.participant, self.scoring_config)
        self.assertEqual(result, 0)


class GroupStageGateTest(QualifierBonusBase):
    """Qualifier bonus is only awarded once the group stage is over."""

    def _set_full_group_real(self, group_name):
        for pos in (1, 2, 3, 4):
            self.set_real_position(group_name, pos, pos)

    def test_no_bonus_while_group_stage_ongoing(self):
        self.set_group_stage_ongoing()
        self._set_full_group_real("A")
        self.set_proj_position("A", 1, 1)
        self.set_proj_position("A", 2, 2)

        result = _calculate_group_qualifier_bonus(self.participant, self.scoring_config)
        self.assertEqual(result, 0)

    def test_bonus_awarded_once_group_stage_finished(self):
        self.set_group_stage_finished()
        self._set_full_group_real("A")
        self.set_proj_position("A", 1, 1)
        self.set_proj_position("A", 2, 2)

        result = _calculate_group_qualifier_bonus(self.participant, self.scoring_config)
        cfg = self.scoring_config
        expected = 2 * (cfg.group_qualifier_points + cfg.group_qualifier_position_bonus)
        self.assertEqual(result, expected)


class QualifierBonusAccountingTest(QualifierBonusBase):
    """Verify recalculate_participant_scores rolls qualifier_bonus into group_points."""

    def _set_full_group_real(self, group_name):
        for pos in (1, 2, 3, 4):
            self.set_real_position(group_name, pos, pos)

    def test_qualifier_bonus_included_in_group_points(self):
        self._set_full_group_real("A")
        # Predict top-2 correctly (positions 1 and 2) → 2 qualifier hits, no position bonus mismatch
        self.set_proj_position("A", 1, 1)
        self.set_proj_position("A", 2, 2)

        recalculate_participant_scores(self.participant, scoring_config=self.scoring_config)

        self.participant.refresh_from_db()
        expected_qualifier = (
            self.scoring_config.group_qualifier_points * 2 + self.scoring_config.group_qualifier_position_bonus * 2
        )
        self.assertEqual(self.participant.qualifier_bonus_points, expected_qualifier)
        self.assertLessEqual(self.participant.qualifier_bonus_points, self.participant.group_points)
        # group_points must include the qualifier bonus
        self.assertGreaterEqual(self.participant.group_points, expected_qualifier)
        self.assertEqual(
            self.participant.total_points,
            self.participant.group_points + self.participant.knockout_points + self.participant.bonus_points,
        )

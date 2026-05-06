import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from src.football.models import Competition, Group, Match, Season, Stage, Team
from src.pool.models import Pool, PoolBet, PoolParticipant
from src.pool.services.context_builder import build_pool_participant_view_context

User = get_user_model()


class ContextBuilderProgressTest(TestCase):
    def setUp(self):
        """Set up base fixtures for all tests."""
        self.user = User.objects.create_user(username="testuser", email="test@example.com", password="123456Aa!")
        self.competition = Competition.objects.create(fifa_id=100, name="Test Competition")
        self.season = Season.objects.create(
            fifa_id=100,
            competition=self.competition,
            name="Test Season",
            year=2026,
            start_date="2026-06-01",
            end_date="2026-07-30",
        )
        self.stage_group = Stage.objects.create(fifa_id="GROUP", season=self.season, name="Group Stage", order=1)
        self.group_a = Group.objects.create(
            season=self.season,
            name="A",
        )

        # Create teams
        self.team_a = Team.objects.create(fifa_id="A", name="Team A", name_norm="teama", code="TAA")
        self.team_b = Team.objects.create(fifa_id="B", name="Team B", name_norm="teamb", code="TAB")
        self.team_c = Team.objects.create(fifa_id="C", name="Team C", name_norm="teamc", code="TAC")
        self.team_d = Team.objects.create(fifa_id="D", name="Team D", name_norm="teamd", code="TAD")

        # Create pool
        self.pool = Pool.objects.create(
            name="Test Pool",
            slug="test-pool",
            season=self.season,
            created_by=self.user,
            requires_payment=False,
        )

        # Create participant (active so can_bet=True)
        self.participant = PoolParticipant.objects.create(
            pool=self.pool,
            user=self.user,
            is_active=True,
        )

        # Base time for match dates (timezone-aware)
        self.base_time = timezone.make_aware(
            datetime.datetime(2026, 6, 15, 16, 0, 0),
            timezone=timezone.get_fixed_timezone(offset=-180),  # America/Sao_Paulo offset
        )

    def test_saved_bets_count_counts_only_active_bets(self):
        """Test that saved_bets_count counts only active bets (is_active=True)."""
        # Create 4 matches with different bet states
        match1 = Match.objects.create(
            fifa_id="M1",
            season=self.season,
            stage=self.stage_group,
            group=self.group_a,
            match_number=1,
            match_date_utc=self.base_time,
            match_date_local=self.base_time,
            match_date_brasilia=self.base_time,
            home_team=self.team_a,
            away_team=self.team_b,
        )
        match2 = Match.objects.create(
            fifa_id="M2",
            season=self.season,
            stage=self.stage_group,
            group=self.group_a,
            match_number=2,
            match_date_utc=self.base_time,
            match_date_local=self.base_time,
            match_date_brasilia=self.base_time,
            home_team=self.team_c,
            away_team=self.team_d,
        )
        match3 = Match.objects.create(
            fifa_id="M3",
            season=self.season,
            stage=self.stage_group,
            group=self.group_a,
            match_number=3,
            match_date_utc=self.base_time,
            match_date_local=self.base_time,
            match_date_brasilia=self.base_time,
            home_team=self.team_a,
            away_team=self.team_c,
        )
        match4 = Match.objects.create(
            fifa_id="M4",
            season=self.season,
            stage=self.stage_group,
            group=self.group_a,
            match_number=4,
            match_date_utc=self.base_time,
            match_date_local=self.base_time,
            match_date_brasilia=self.base_time,
            home_team=self.team_b,
            away_team=self.team_d,
        )

        # Create bets: 2 active, 1 inactive, 1 none
        # match4 will have no bet associated with it
        assert match4 is not None
        PoolBet.objects.create(
            participant=self.participant,
            match=match1,
            home_score_pred=1,
            away_score_pred=0,
            is_active=True,
        )
        PoolBet.objects.create(
            participant=self.participant,
            match=match2,
            home_score_pred=2,
            away_score_pred=1,
            is_active=True,
        )
        PoolBet.objects.create(
            participant=self.participant,
            match=match3,
            home_score_pred=0,
            away_score_pred=0,
            is_active=False,  # Inactive
        )
        # match4 has no bet

        ctx = build_pool_participant_view_context(pool=self.pool, participant=self.participant)

        self.assertEqual(ctx["saved_bets_count"], 2)
        self.assertEqual(ctx["total_group_matches"], 4)

    def test_saved_bets_count_zero_when_no_bets(self):
        """Test that saved_bets_count is 0 when no bets are active."""
        # Create 3 matches with no bets
        Match.objects.create(
            fifa_id="M1",
            season=self.season,
            stage=self.stage_group,
            group=self.group_a,
            match_number=1,
            match_date_utc=self.base_time,
            match_date_local=self.base_time,
            match_date_brasilia=self.base_time,
            home_team=self.team_a,
            away_team=self.team_b,
        )
        Match.objects.create(
            fifa_id="M2",
            season=self.season,
            stage=self.stage_group,
            group=self.group_a,
            match_number=2,
            match_date_utc=self.base_time,
            match_date_local=self.base_time,
            match_date_brasilia=self.base_time,
            home_team=self.team_c,
            away_team=self.team_d,
        )
        Match.objects.create(
            fifa_id="M3",
            season=self.season,
            stage=self.stage_group,
            group=self.group_a,
            match_number=3,
            match_date_utc=self.base_time,
            match_date_local=self.base_time,
            match_date_brasilia=self.base_time,
            home_team=self.team_a,
            away_team=self.team_c,
        )

        ctx = build_pool_participant_view_context(pool=self.pool, participant=self.participant)

        self.assertEqual(ctx["saved_bets_count"], 0)
        self.assertEqual(ctx["total_group_matches"], 3)

    def test_group_rows_sorted_by_match_date(self):
        """Test that group_rows is sorted by match_date_brasilia in ascending order."""
        # Create 3 matches in non-chronological insertion order
        date1 = self.base_time
        date2 = self.base_time + timezone.timedelta(days=1)
        date3 = self.base_time + timezone.timedelta(days=2)

        # Insert in order: date3, date1, date2 (scrambled)
        Match.objects.create(
            fifa_id="M3",
            season=self.season,
            stage=self.stage_group,
            group=self.group_a,
            match_number=3,
            match_date_utc=date3,
            match_date_local=date3,
            match_date_brasilia=date3,
            home_team=self.team_a,
            away_team=self.team_b,
        )
        Match.objects.create(
            fifa_id="M1",
            season=self.season,
            stage=self.stage_group,
            group=self.group_a,
            match_number=1,
            match_date_utc=date1,
            match_date_local=date1,
            match_date_brasilia=date1,
            home_team=self.team_c,
            away_team=self.team_d,
        )
        Match.objects.create(
            fifa_id="M2",
            season=self.season,
            stage=self.stage_group,
            group=self.group_a,
            match_number=2,
            match_date_utc=date2,
            match_date_local=date2,
            match_date_brasilia=date2,
            home_team=self.team_a,
            away_team=self.team_c,
        )

        ctx = build_pool_participant_view_context(pool=self.pool, participant=self.participant)

        # Extract dates from returned group_rows
        dates = [row["match"].match_date_brasilia for row in ctx["group_rows"]]

        # Verify they are sorted in ascending order
        self.assertEqual(dates, sorted(dates))
        # Also verify they match the expected order
        self.assertEqual(dates, [date1, date2, date3])

    def test_existing_keys_preserved(self):
        """Test that all existing context keys are still present."""
        # Create one match for a minimal fixture
        Match.objects.create(
            fifa_id="M1",
            season=self.season,
            stage=self.stage_group,
            group=self.group_a,
            match_number=1,
            match_date_utc=self.base_time,
            match_date_local=self.base_time,
            match_date_brasilia=self.base_time,
            home_team=self.team_a,
            away_team=self.team_b,
        )

        ctx = build_pool_participant_view_context(pool=self.pool, participant=self.participant)

        # Assert all expected keys are present
        expected_keys = [
            "match_rows",
            "group_rows",
            "knockout_rows",
            "projected_groups",
            "can_bet",
            "group_locked",
            "knockout_locked",
            "projection_pending",
            "top_scorer_options",
            "page_mode",
            "saved_bets_count",
            "total_group_matches",
        ]
        for key in expected_keys:
            self.assertIn(key, ctx, f"Missing key: {key}")

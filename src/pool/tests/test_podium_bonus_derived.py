from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from src.football.models import Competition, Match, Season, Stage, Team
from src.pool.models import Pool, PoolBet, PoolParticipant
from src.pool.services.ranking import recalculate_participant_scores
from src.pool.services.rules import POOL_TYPE_2

User = get_user_model()


class DerivedPodiumBonusTest(TestCase):
    """Bônus de campeão/vice/terceiro deve sair do bracket projetado pelo palpite.

    Usuário real nunca preenche champion_pred/runner_up_pred/third_place_pred
    (não há formulário); o palpite de pódio vive nas apostas da final e da
    disputa de 3º lugar. O bônus precisa ser concedido a partir delas.
    """

    def setUp(self):
        self.owner = User.objects.create_user(username="podium-owner", email="po@example.com", password="123456Aa!")
        self.user = User.objects.create_user(username="podium-user", email="pu@example.com", password="123456Aa!")

        competition = Competition.objects.create(fifa_id=900, name="Copa Podio")
        self.season = Season.objects.create(
            fifa_id=900,
            competition=competition,
            name="Temporada Podio",
            year=2026,
            start_date="2026-06-01",
            end_date="2026-07-30",
        )
        stage_r32 = Stage.objects.create(fifa_id="POD-R32", season=self.season, name="Segundas de Final", order=2)
        stage_third = Stage.objects.create(
            fifa_id="POD-3RD", season=self.season, name="Disputa de terceiro lugar", order=6
        )
        stage_final = Stage.objects.create(fifa_id="POD-F", season=self.season, name="Final", order=7)

        self.team_a = Team.objects.create(fifa_id="PODA", name="Pod A", name_norm="poda", code="PDA")
        self.team_b = Team.objects.create(fifa_id="PODB", name="Pod B", name_norm="podb", code="PDB")
        self.team_c = Team.objects.create(fifa_id="PODC", name="Pod C", name_norm="podc", code="PDC")
        self.team_d = Team.objects.create(fifa_id="PODD", name="Pod D", name_norm="podd", code="PDD")

        now = timezone.now()

        def make_match(fifa_id, stage, number, home=None, away=None, home_ph="", away_ph=""):
            return Match.objects.create(
                fifa_id=fifa_id,
                season=self.season,
                stage=stage,
                match_number=number,
                match_date_utc=now,
                match_date_local=now,
                match_date_brasilia=now,
                home_team=home,
                away_team=away,
                home_placeholder=home_ph,
                away_placeholder=away_ph,
            )

        self.r32_1 = make_match("POD-M61", stage_r32, 61, home=self.team_a, away=self.team_b)
        self.r32_2 = make_match("POD-M62", stage_r32, 62, home=self.team_c, away=self.team_d)
        self.third = make_match("POD-M63", stage_third, 63, home_ph="RU61", away_ph="RU62")
        self.final = make_match("POD-M64", stage_final, 64, home_ph="W61", away_ph="W62")

        self.pool = Pool.objects.create(
            name="Pool Podio",
            slug="pool-podio",
            season=self.season,
            created_by=self.owner,
            requires_payment=False,
            pool_type=POOL_TYPE_2,
        )
        self.participant = PoolParticipant.objects.create(pool=self.pool, user=self.user, is_active=True)

        config = self.pool.get_scoring_config()
        config.bonus_champion_points = 9
        config.bonus_runner_up_points = 7
        config.bonus_third_place_points = 5
        config.save()

        # Palpites por placar: A e C avançam do R32; final A x C com A campeão;
        # disputa de 3º projetada B x D com B em 3º. Nenhum campo *_pred de pódio
        # é preenchido — como acontece com usuários reais.
        PoolBet.objects.create(participant=self.participant, match=self.r32_1, home_score_pred=2, away_score_pred=1)
        PoolBet.objects.create(participant=self.participant, match=self.r32_2, home_score_pred=2, away_score_pred=1)
        PoolBet.objects.create(participant=self.participant, match=self.third, home_score_pred=2, away_score_pred=1)
        PoolBet.objects.create(participant=self.participant, match=self.final, home_score_pred=2, away_score_pred=1)

        official = self.pool.get_official_results()
        official.champion = self.team_a
        official.runner_up = self.team_c
        official.third_place = self.team_b
        official.save()

    def test_podium_bonus_derived_from_bracket_bets(self):
        recalculate_participant_scores(self.participant)
        self.participant.refresh_from_db()

        self.assertTrue(self.participant.champion_hit)
        self.assertEqual(self.participant.bonus_points, 9 + 7 + 5)

    def test_asof_podium_bonus_derived_from_bracket_bets(self):
        from src.pool.services.asof_standings import compute_asof_standings

        allowed = {self.r32_1.id, self.r32_2.id, self.third.id, self.final.id}
        rows = compute_asof_standings(
            self.pool, allowed, self.pool.get_scoring_config(), self.pool.get_official_results()
        )
        row = next(r for r in rows if r.participant.id == self.participant.id)

        self.assertTrue(row.champion_hit)
        self.assertEqual(row.total_points, 9 + 7 + 5)

    def test_no_bonus_when_bracket_disagrees_with_official(self):
        official = self.pool.get_official_results()
        official.champion = self.team_d
        official.runner_up = self.team_b
        official.third_place = self.team_c
        official.save()

        recalculate_participant_scores(self.participant)
        self.participant.refresh_from_db()

        self.assertFalse(self.participant.champion_hit)
        self.assertEqual(self.participant.bonus_points, 0)

from dataclasses import dataclass

from src.football.models import Match
from src.pool.models import PoolParticipant
from src.pool.services.ranking import _match_winner_loser, _real_qualifier_position_map
from src.pool.services.rules import PHASE_GROUP, POOL_TYPE_1, POOL_TYPE_2, normalize_stage_key, phase_for_match
from src.pool.services.scoring import calculate_bet_points
from src.rankings.services.leaderboard import eligible_participants


@dataclass
class AsOfStanding:
    participant: PoolParticipant
    total_points: int = 0
    group_points: int = 0
    knockout_points: int = 0
    exact_score_hits: int = 0
    advancing_hits: int = 0
    champion_hit: bool = False
    top_scorer_hit: bool = False


def _group_match_ids(season):
    return {
        m.id
        for m in Match.objects.filter(season=season).select_related("stage")
        if normalize_stage_key(m.stage) == "GROUP"
    }


def _asof_team_advancement_bonus(participant, allowed_match_ids, scoring_config, advancing_map):
    """Tipo 1: bônus por time previsto que avançou, só de jogos no conjunto.

    O classificado palpitado vem de `advancing_map` (bracket projetado com fallback
    por placar), pois `winner_pred` fica None em palpites decisivos de jogos
    projetados. Cai para `winner_pred_id` quando o mapa não resolve a partida.
    """
    total = 0
    stage_winners_cache = {}
    for bet in participant.bets.select_related("match", "match__stage").all():
        if bet.match_id not in allowed_match_ids:
            continue
        if phase_for_match(bet.match) == PHASE_GROUP:
            continue
        stage_id = bet.match.stage_id
        if stage_id not in stage_winners_cache:
            stage_winners_cache[stage_id] = set(
                Match.objects.filter(stage_id=stage_id, winner_id__isnull=False, id__in=allowed_match_ids).values_list(
                    "winner_id", flat=True
                )
            )
        predicted_id = advancing_map.get(bet.match_id) or bet.winner_pred_id
        if predicted_id and predicted_id in stage_winners_cache[stage_id]:
            total += scoring_config.knockout_team_advancement_bonus
    return total


def _asof_group_qualifier_bonus(participant, season, allowed_match_ids, scoring_config):
    """Classificados de grupo: só quando todos os jogos de grupo estão no conjunto."""
    group_ids = _group_match_ids(season)
    if not group_ids or not group_ids <= allowed_match_ids:
        return 0

    real_qualifiers_by_group, _ = _real_qualifier_position_map(season)
    if not real_qualifiers_by_group:
        return 0

    proj_positions_by_group = {}
    for s in participant.projected_standings.filter(position__lte=3).values("group_id", "position", "team_id"):
        proj_positions_by_group.setdefault(s["group_id"], {})[s["position"]] = s["team_id"]

    total = 0
    for group_id, real_positions in real_qualifiers_by_group.items():
        proj_positions = proj_positions_by_group.get(group_id, {})
        real_qualifier_ids = set(real_positions.values())
        for position, team_id in proj_positions.items():
            if team_id in real_qualifier_ids:
                total += scoring_config.group_qualifier_points
                if real_positions.get(position) == team_id:
                    total += scoring_config.group_qualifier_position_bonus
    return total


def _asof_podium(season, allowed_match_ids, official_result):
    """(champion_id, runner_up_id, third_id) derivados só dos jogos no conjunto.

    Prefere os campos oficial_result (editáveis pelo admin) quando o jogo
    correspondente está no conjunto permitido, com fallback para _match_winner_loser.
    """
    champion_id = runner_up_id = third_id = None
    final_match = (
        Match.objects.filter(season=season, stage__order=7).select_related("home_team", "away_team", "winner").first()
    )
    third_match = (
        Match.objects.filter(season=season, stage__order=6).select_related("home_team", "away_team", "winner").first()
    )
    if final_match and final_match.id in allowed_match_ids:
        if official_result.champion_id is not None:
            champion_id = official_result.champion_id
        else:
            champion, _ = _match_winner_loser(final_match)
            champion_id = champion.id if champion else None
        if official_result.runner_up_id is not None:
            runner_up_id = official_result.runner_up_id
        else:
            _, runner_up = _match_winner_loser(final_match)
            runner_up_id = runner_up.id if runner_up else None
    if third_match and third_match.id in allowed_match_ids:
        if official_result.third_place_id is not None:
            third_id = official_result.third_place_id
        else:
            third, _ = _match_winner_loser(third_match)
            third_id = third.id if third else None
    return champion_id, runner_up_id, third_id


def _asof_podium_bonus(participant, podium, official_result, scoring_config):
    """Bônus de pódio/artilheiro. Retorna (points, champion_hit, top_scorer_hit)."""
    champion_id, runner_up_id, third_id = podium
    points = 0
    champion_hit = bool(participant.champion_pred_id and participant.champion_pred_id == champion_id)
    runner_up_hit = bool(participant.runner_up_pred_id and participant.runner_up_pred_id == runner_up_id)
    third_place_hit = bool(participant.third_place_pred_id and participant.third_place_pred_id == third_id)

    top_scorer_tied_ids = list(official_result.top_scorers.values_list("id", flat=True))
    if top_scorer_tied_ids:
        top_scorer_hit = bool(participant.top_scorer_pred_id and participant.top_scorer_pred_id in top_scorer_tied_ids)
    else:
        top_scorer_hit = bool(
            participant.top_scorer_pred_id
            and official_result.top_scorer_id
            and participant.top_scorer_pred_id == official_result.top_scorer_id
        )

    if champion_hit:
        points += scoring_config.bonus_champion_points
    if runner_up_hit:
        points += scoring_config.bonus_runner_up_points
    if third_place_hit:
        points += scoring_config.bonus_third_place_points
    if top_scorer_hit:
        points += scoring_config.bonus_top_scorer_points

    return points, champion_hit, top_scorer_hit


def compute_asof_standings(pool, allowed_match_ids, scoring_config, official_result):
    """Standings do bolão considerando só os jogos em allowed_match_ids.

    Não toca o banco: retorna uma lista de AsOfStanding (uma por participante
    elegível). Espelha recalculate_participant_scores, mas restrito ao conjunto
    de jogos permitidos.
    """
    allowed_match_ids = set(allowed_match_ids)
    pool_type = pool.pool_type

    knockout_phase_scoring = None
    if pool_type == POOL_TYPE_2:
        knockout_phase_scoring = {row.phase_key: row for row in scoring_config.knockout_phases.all()}

    participants = list(eligible_participants(pool).select_related("user"))

    podium = _asof_podium(pool.season, allowed_match_ids, official_result)

    # O classificado palpitado por partida resolve tanto o gate do Tipo 2 quanto
    # o bônus de avanço do Tipo 1; o conjunto de jogos é o mesmo para todos os
    # participantes, então resolvemos a lista uma vez só (o walk do bracket
    # projetado abaixo é que varia por participante).
    knockout_matches = []
    if pool_type in (POOL_TYPE_1, POOL_TYPE_2):
        from src.pool.services.context_builder import resolve_knockout_advancing_by_match

        knockout_matches = [
            m
            for m in Match.objects.filter(season=pool.season)
            .select_related("stage", "home_team", "away_team", "winner")
            .order_by("match_number")
            if phase_for_match(m) != PHASE_GROUP
        ]

    rows = []
    for participant in participants:
        total_points = 0
        group_points = 0
        knockout_points = 0
        exact_score_hits = 0
        advancing_hits = 0

        bets = participant.bets.select_related("match", "match__stage", "winner_pred").all()

        advancing_map = {}
        if pool_type in (POOL_TYPE_1, POOL_TYPE_2):
            bets_by_match_id = {b.match_id: b for b in bets}
            advancing_map = resolve_knockout_advancing_by_match(
                participant=participant,
                matches=knockout_matches,
                season=pool.season,
                bets_by_match_id=bets_by_match_id,
            )

        for bet in bets:
            if bet.match_id not in allowed_match_ids:
                continue
            score_data = calculate_bet_points(
                bet,
                scoring_config=scoring_config,
                pool_type=pool_type,
                predicted_advancing_id=advancing_map.get(bet.match_id),
                knockout_phase_scoring=knockout_phase_scoring,
            )
            total_points += score_data["points"]
            if phase_for_match(bet.match) == PHASE_GROUP:
                group_points += score_data["points"]
            else:
                knockout_points += score_data["points"]
            if score_data["exact_score"]:
                exact_score_hits += 1
            if score_data["advancing_correct"]:
                advancing_hits += 1

        if pool_type == POOL_TYPE_1:
            advancement_bonus = _asof_team_advancement_bonus(
                participant, allowed_match_ids, scoring_config, advancing_map
            )
            knockout_points += advancement_bonus
            total_points += advancement_bonus

        qualifier_bonus = _asof_group_qualifier_bonus(participant, pool.season, allowed_match_ids, scoring_config)
        group_points += qualifier_bonus
        total_points += qualifier_bonus

        podium_points, champion_hit, top_scorer_hit = _asof_podium_bonus(
            participant, podium, official_result, scoring_config
        )
        total_points += podium_points

        rows.append(
            AsOfStanding(
                participant=participant,
                total_points=total_points,
                group_points=group_points,
                knockout_points=knockout_points,
                exact_score_hits=exact_score_hits,
                advancing_hits=advancing_hits,
                champion_hit=champion_hit,
                top_scorer_hit=top_scorer_hit,
            )
        )

    return rows

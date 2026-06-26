from django.db import transaction

from src.pool.models import Pool, PoolBetScore, PoolParticipant
from src.pool.services.rules import PHASE_GROUP, POOL_TYPE_1, POOL_TYPE_2, is_group_stage_finished, phase_for_match
from src.pool.services.scoring import calculate_bet_points


def _calculate_bonus(participant, scoring_config, official_result):
    bonus_points = 0
    champion_hit = bool(
        participant.champion_pred_id
        and official_result.champion_id
        and participant.champion_pred_id == official_result.champion_id
    )
    runner_up_hit = bool(
        participant.runner_up_pred_id
        and official_result.runner_up_id
        and participant.runner_up_pred_id == official_result.runner_up_id
    )
    third_place_hit = bool(
        participant.third_place_pred_id
        and official_result.third_place_id
        and participant.third_place_pred_id == official_result.third_place_id
    )
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
        bonus_points += scoring_config.bonus_champion_points
    if runner_up_hit:
        bonus_points += scoring_config.bonus_runner_up_points
    if third_place_hit:
        bonus_points += scoring_config.bonus_third_place_points
    if top_scorer_hit:
        bonus_points += scoring_config.bonus_top_scorer_points

    return bonus_points, champion_hit, top_scorer_hit


def _real_qualifier_position_map(season):
    """Return ({group_id: {position: team_id}}, r32_drawn).

    Positions 1 and 2 always come from Standings. Position 3 is included only
    when FIFA has placed the team in an R32 match (the 8 best thirds rule).
    r32_drawn is True iff at least one R32 match has any team assigned.
    """
    from src.football.models import Match, Standing
    from src.pool.services.rules import normalize_stage_key

    real = {}
    for s in Standing.objects.filter(season=season, position__lte=2).values("group_id", "position", "team_id"):
        real.setdefault(s["group_id"], {})[s["position"]] = s["team_id"]

    r32_team_ids = set()
    for match in Match.objects.filter(season=season).select_related("stage"):
        if normalize_stage_key(match.stage) != "R32":
            continue
        if match.home_team_id:
            r32_team_ids.add(match.home_team_id)
        if match.away_team_id:
            r32_team_ids.add(match.away_team_id)

    r32_drawn = bool(r32_team_ids)
    if r32_drawn:
        for s in Standing.objects.filter(season=season, position=3).values("group_id", "team_id"):
            if s["team_id"] in r32_team_ids:
                real.setdefault(s["group_id"], {})[3] = s["team_id"]

    return real, r32_drawn


def _calculate_group_qualifier_bonus(participant, scoring_config):
    """Award points for correctly predicting group-stage qualifiers.

    Top 2 always qualify; 3rd place qualifies only if the team is among the
    8 best thirds (i.e. FIFA placed it in an R32 match). For each predicted
    team that matches a real qualifier: +group_qualifier_points; +position_bonus
    additionally if the predicted position equals the real Standings position.
    """
    season = participant.pool.season

    # Award qualifier bonus only after the group stage is over. Mid-stage the
    # Standings positions are still provisional, so points must not be granted.
    if not is_group_stage_finished(season):
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


def _calculate_team_advancement_bonus(bets_by_stage, scoring_config):
    """Tipo 1 only: award bonus if predicted winner advanced from that stage (anywhere in the stage).

    Returns (team_advancement dict {bet_id: bool}, total bonus points).
    """
    from src.football.models import Match as FootballMatch

    stage_winners_cache = {}
    team_advancement = {}
    total = 0

    for stage_id, bets in bets_by_stage.items():
        if stage_id not in stage_winners_cache:
            stage_winners_cache[stage_id] = set(
                FootballMatch.objects.filter(stage_id=stage_id, winner_id__isnull=False).values_list(
                    "winner_id", flat=True
                )
            )
        real_winners = stage_winners_cache[stage_id]
        for bet in bets:
            advanced = bool(bet.winner_pred_id and bet.winner_pred_id in real_winners)
            team_advancement[bet.id] = advanced
            if advanced:
                total += scoring_config.knockout_team_advancement_bonus

    return team_advancement, total


@transaction.atomic
def recalculate_participant_scores(participant, scoring_config=None, official_result=None):
    scoring_config = scoring_config or participant.pool.get_scoring_config()
    official_result = official_result or participant.pool.get_official_results()
    pool_type = participant.pool.pool_type

    knockout_phase_scoring = None
    if pool_type == POOL_TYPE_2:
        knockout_phase_scoring = {row.phase_key: row for row in scoring_config.knockout_phases.all()}

    bets = list(participant.bets.select_related("match", "match__stage", "winner_pred").all())

    # Tipo 2: resolve advancing team per knockout match to gate scoring
    advancing_map = {}
    if pool_type == POOL_TYPE_2:
        from src.football.models import Match as FootballMatch
        from src.pool.services.context_builder import resolve_knockout_advancing_by_match

        knockout_matches = [
            m
            for m in FootballMatch.objects.filter(season=participant.pool.season)
            .select_related("stage", "home_team", "away_team", "winner")
            .order_by("match_number")
            if phase_for_match(m) != PHASE_GROUP
        ]
        bets_by_match_id = {bet.match_id: bet for bet in bets}
        advancing_map = resolve_knockout_advancing_by_match(
            participant=participant,
            matches=knockout_matches,
            season=participant.pool.season,
            bets_by_match_id=bets_by_match_id,
        )

    # Tipo 1: pre-calculate team advancement bonus (needs cross-match lookup per stage)
    team_advancement = {}
    team_advancement_bonus_total = 0
    if pool_type == POOL_TYPE_1:
        knockout_bets_by_stage = {}
        for bet in bets:
            if phase_for_match(bet.match) != PHASE_GROUP:
                knockout_bets_by_stage.setdefault(bet.match.stage_id, []).append(bet)
        if knockout_bets_by_stage:
            team_advancement, team_advancement_bonus_total = _calculate_team_advancement_bonus(
                knockout_bets_by_stage, scoring_config
            )

    total_points = 0
    group_points = 0
    knockout_points = 0
    exact_score_hits = 0
    advancing_correct_hits = 0

    scores_to_upsert = []
    for bet in bets:
        score_data = calculate_bet_points(
            bet,
            scoring_config=scoring_config,
            pool_type=pool_type,
            predicted_advancing_id=advancing_map.get(bet.match_id),
            knockout_phase_scoring=knockout_phase_scoring,
        )
        scores_to_upsert.append(
            PoolBetScore(
                bet=bet,
                points=score_data["points"],
                exact_score=score_data["exact_score"],
                advancing_correct=score_data["advancing_correct"],
                advancing_goals_correct=score_data["advancing_goals_correct"],
                diff_correct=score_data["diff_correct"],
                eliminated_goals_correct=score_data["eliminated_goals_correct"],
                team_advancement_bonus=team_advancement.get(bet.id, False),
            )
        )

        total_points += score_data["points"]
        if phase_for_match(bet.match) == PHASE_GROUP:
            group_points += score_data["points"]
        else:
            knockout_points += score_data["points"]

        if score_data["exact_score"]:
            exact_score_hits += 1
        if score_data["advancing_correct"]:
            advancing_correct_hits += 1

    # Include team advancement bonus in knockout_points
    knockout_points += team_advancement_bonus_total
    total_points += team_advancement_bonus_total

    if scores_to_upsert:
        PoolBetScore.objects.bulk_create(
            scores_to_upsert,
            update_conflicts=True,
            update_fields=[
                "points",
                "exact_score",
                "advancing_correct",
                "advancing_goals_correct",
                "diff_correct",
                "eliminated_goals_correct",
                "team_advancement_bonus",
                "updated_at",
            ],
            unique_fields=["bet"],
        )

    bonus_points, champion_hit, top_scorer_hit = _calculate_bonus(
        participant=participant,
        scoring_config=scoring_config,
        official_result=official_result,
    )

    qualifier_bonus_points = _calculate_group_qualifier_bonus(participant, scoring_config)

    total_points += bonus_points + qualifier_bonus_points
    group_points += qualifier_bonus_points

    participant.total_points = total_points
    participant.group_points = group_points
    participant.knockout_points = knockout_points
    participant.bonus_points = bonus_points
    participant.qualifier_bonus_points = qualifier_bonus_points
    participant.exact_score_hits = exact_score_hits
    participant.advancing_hits = advancing_correct_hits
    participant.champion_hit = champion_hit
    participant.top_scorer_hit = top_scorer_hit
    participant.save(
        update_fields=[
            "total_points",
            "group_points",
            "knockout_points",
            "bonus_points",
            "qualifier_bonus_points",
            "exact_score_hits",
            "advancing_hits",
            "champion_hit",
            "top_scorer_hit",
        ]
    )


def _match_winner_loser(match):
    """Returns (winner, loser) for a completed knockout match, or (None, None)."""
    if match.winner_id and match.home_team and match.away_team:
        loser = match.away_team if match.winner_id == match.home_team_id else match.home_team
        return match.winner, loser
    if match.home_score is not None and match.away_score is not None and match.home_team and match.away_team:
        if match.home_score > match.away_score:
            return match.home_team, match.away_team
        if match.away_score > match.home_score:
            return match.away_team, match.home_team
    return None, None


def _sync_podium_from_matches(official_result):
    """Auto-fill champion/runner_up/third_place from the final and 3rd-place matches."""
    from src.football.models import Match

    season = official_result.pool.season
    update_fields = []

    final = (
        Match.objects.filter(season=season, stage__order=7).select_related("home_team", "away_team", "winner").first()
    )
    third_match = (
        Match.objects.filter(season=season, stage__order=6).select_related("home_team", "away_team", "winner").first()
    )

    if final:
        champion, runner_up = _match_winner_loser(final)
        if champion and official_result.champion_id != champion.id:
            official_result.champion = champion
            update_fields.append("champion")
        if runner_up and official_result.runner_up_id != runner_up.id:
            official_result.runner_up = runner_up
            update_fields.append("runner_up")

    if third_match:
        third, _ = _match_winner_loser(third_match)
        if third and official_result.third_place_id != third.id:
            official_result.third_place = third
            update_fields.append("third_place")

    if update_fields:
        official_result.save(update_fields=update_fields)


@transaction.atomic
def recalculate_pool_scores(pool):
    scoring_config = pool.get_scoring_config()
    official_result = pool.get_official_results()
    _sync_podium_from_matches(official_result)
    participants = PoolParticipant.objects.filter(pool=pool, is_active=True).all()
    for participant in participants:
        recalculate_participant_scores(
            participant,
            scoring_config=scoring_config,
            official_result=official_result,
        )


def recalculate_all_pools(season=None):
    from src.rankings.services.derived import refresh_pool_derived_data

    pools = Pool.objects.filter(is_active=True)
    if season is not None:
        pools = pools.filter(season=season)

    for pool in pools:
        recalculate_pool_scores(pool)
        refresh_pool_derived_data(pool)


def recalculate_match_scores(match):
    participants = (
        PoolParticipant.objects.filter(pool__season=match.season, is_active=True, bets__match=match).distinct().all()
    )
    for participant in participants:
        recalculate_participant_scores(participant)


def recalculate_after_sync(season, changed_matches, *, podium_changed, group_stage_just_closed):
    """Recalcula só o necessário após um sync de partidas.

    - podium_changed ou group_stage_just_closed: recalc do pool inteiro (bônus de
      pódio/classificados afetam todos os participantes), igual ao caminho antigo.
    - caso contrário: recalc só dos participantes que apostaram nos jogos alterados.
    - nada mudou: não faz nada.
    """
    from src.rankings.services.derived import refresh_pool_derived_data

    if podium_changed or group_stage_just_closed:
        for pool in Pool.objects.filter(is_active=True, season=season):
            recalculate_pool_scores(pool)
            refresh_pool_derived_data(pool)
        return

    if not changed_matches:
        return

    for match in changed_matches:
        recalculate_match_scores(match)

    affected_pool_ids = set(
        PoolParticipant.objects.filter(
            pool__season=season,
            pool__is_active=True,
            is_active=True,
            bets__match__in=changed_matches,
        )
        .values_list("pool_id", flat=True)
        .distinct()
    )
    for pool in Pool.objects.filter(id__in=affected_pool_ids):
        refresh_pool_derived_data(pool)

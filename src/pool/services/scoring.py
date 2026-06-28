from types import SimpleNamespace

from src.pool.services.rules import PHASE_GROUP, POOL_TYPE_2, normalize_stage_key, phase_for_match


def _winner_from_score(home_score, away_score):
    if home_score > away_score:
        return "HOME"
    if away_score > home_score:
        return "AWAY"
    return "DRAW"


def _is_winner_goals_correct(actual_winner, guess_home, guess_away, home, away):
    if actual_winner == "HOME":
        return guess_home == home
    if actual_winner == "AWAY":
        return guess_away == away
    return False


def _is_loser_goals_correct(actual_winner, guess_home, guess_away, home, away):
    if actual_winner == "HOME":
        return guess_away == away
    if actual_winner == "AWAY":
        return guess_home == home
    return False


def _knockout_points_by_score(tier, home, away, guess_home, guess_away):
    """Faixa de pontos do mata-mata pelo placar (posicional), assumindo classificado correto.

    `tier` é um objeto com os atributos exact/advancing_goals/diff/loser_goals/advancing_only.
    Retorna (points, is_exact, advancing_goals, diff_correct, eliminated_goals).
    """
    is_exact = guess_home == home and guess_away == away
    if is_exact:
        return tier.exact, True, False, False, False

    is_diff = (guess_home - guess_away) == (home - away)

    if home == away:
        # Empate real (decidido nos pênaltis): sem vencedor posicional.
        if is_diff:
            return tier.diff, False, False, True, False
        return tier.advancing_only, False, False, False, False

    actual_direction = _winner_from_score(home, away)
    # Gate por direção: gols do vencedor/perdedor só contam se o palpite cravou o
    # mesmo resultado (mandante/visitante/empate) do tempo regulamentar. Sem isso,
    # um palpite de empate (ou do vencedor errado) ganharia "gols do vencedor" só
    # porque o número de um dos lados coincidiu. Errou o resultado -> só classificado.
    direction_correct = _winner_from_score(guess_home, guess_away) == actual_direction
    winner_goals = direction_correct and _is_winner_goals_correct(actual_direction, guess_home, guess_away, home, away)
    loser_goals = direction_correct and _is_loser_goals_correct(actual_direction, guess_home, guess_away, home, away)

    if winner_goals:
        return tier.advancing_goals, False, True, False, False
    if is_diff:
        return tier.diff, False, False, True, False
    if loser_goals:
        return tier.loser_goals, False, False, False, True
    return tier.advancing_only, False, False, False, False


def _tier_from_flat_config(scoring_config):
    return SimpleNamespace(
        exact=scoring_config.knockout_exact_and_advancing,
        advancing_goals=scoring_config.knockout_advancing_and_winner_goals,
        diff=scoring_config.knockout_advancing_and_diff,
        loser_goals=scoring_config.knockout_advancing_and_loser_goals,
        advancing_only=scoring_config.knockout_advancing_only,
    )


def calculate_bet_points(
    bet, scoring_config, pool_type=None, predicted_advancing_id=None, knockout_phase_scoring=None
):
    match = bet.match
    if (
        not bet.is_active
        or bet.home_score_pred is None
        or bet.away_score_pred is None
        or match.home_score is None
        or match.away_score is None
    ):
        return {
            "points": 0,
            "exact_score": False,
            "advancing_correct": False,
            "advancing_goals_correct": False,
            "diff_correct": False,
            "eliminated_goals_correct": False,
        }

    home = match.home_score
    away = match.away_score
    guess_home = bet.home_score_pred
    guess_away = bet.away_score_pred

    is_exact_score = guess_home == home and guess_away == away

    phase = phase_for_match(match)

    if phase == PHASE_GROUP:
        actual_winner = _winner_from_score(home, away)
        guess_winner = _winner_from_score(guess_home, guess_away)
        is_winner_correct = actual_winner == guess_winner
        is_diff_correct = is_winner_correct and (guess_home - guess_away) == (home - away)
        is_winner_goals = is_winner_correct and _is_winner_goals_correct(
            actual_winner, guess_home, guess_away, home, away
        )
        is_loser_goals = is_winner_correct and _is_loser_goals_correct(
            actual_winner, guess_home, guess_away, home, away
        )

        if is_exact_score:
            points = scoring_config.group_exact_score
        elif is_winner_correct and is_winner_goals:
            points = scoring_config.group_winner_and_winner_goals
        elif is_winner_correct and is_diff_correct:
            points = scoring_config.group_winner_and_diff
        elif is_winner_correct and is_loser_goals:
            points = scoring_config.group_winner_and_loser_goals
        elif is_winner_correct:
            points = scoring_config.group_winner_only
        else:
            points = 0

        return {
            "points": points,
            "exact_score": is_exact_score,
            "advancing_correct": is_winner_correct,
            "advancing_goals_correct": is_winner_goals,
            "diff_correct": is_diff_correct,
            "eliminated_goals_correct": is_loser_goals,
        }

    # KNOCKOUT Tipo 2: gate por classificado (identidade do time), não por posição.
    if pool_type == POOL_TYPE_2:
        is_advancing_correct = bool(match.winner_id) and predicted_advancing_id == match.winner_id
        if not is_advancing_correct:
            return {
                "points": 0,
                "exact_score": is_exact_score,
                "advancing_correct": False,
                "advancing_goals_correct": False,
                "diff_correct": False,
                "eliminated_goals_correct": False,
            }
        stage_key = normalize_stage_key(match.stage)
        tier = (knockout_phase_scoring or {}).get(stage_key)
        if tier is None:
            # Fallback retrocompatível: pool sem faixas por fase usa os campos flat.
            tier = _tier_from_flat_config(scoring_config)
        points, is_exact, advancing_goals, diff_correct, eliminated_goals = _knockout_points_by_score(
            tier, home, away, guess_home, guess_away
        )
        return {
            "points": points,
            "exact_score": is_exact,
            "advancing_correct": True,
            "advancing_goals_correct": advancing_goals,
            "diff_correct": diff_correct,
            "eliminated_goals_correct": eliminated_goals,
        }

    # KNOCKOUT phase — positional scoring (Tipo 1 only; Tipo 2 handled above).
    # Palpite de empate (home == away): requer que o tempo regulamentar tambem
    # termine empatado. Placar exato (ex: 1-1 pred, 1-1 real) vale mais que
    # empate generico (ex: 0-0 pred, 1-1 real). Pênaltis contam como empate no
    # regulamentar, portanto winner_pred determina quem avançou.
    if guess_home == guess_away:
        is_advancing_correct = bool(match.winner_id and bet.winner_pred_id == match.winner_id)
        real_is_draw = home == away
        if not real_is_draw:
            points = 0
        elif is_exact_score:
            points = scoring_config.knockout_exact_and_advancing
        else:
            points = scoring_config.knockout_draw_prediction_points
        return {
            "points": points,
            "exact_score": is_exact_score,
            "advancing_correct": is_advancing_correct,
            "advancing_goals_correct": False,
            "diff_correct": False,
            "eliminated_goals_correct": False,
        }

    # Scoring uses the regulation-time score: a 1-1 that goes to penalties is
    # still a DRAW for the match-winner check, even if one team advances.
    actual_direction = _winner_from_score(home, away)

    guess_direction = _winner_from_score(guess_home, guess_away)
    is_winner_correct = actual_direction == guess_direction

    if actual_direction == "HOME":
        raw_winner_goals = guess_home == home
        raw_loser_goals = guess_away == away
    elif actual_direction == "AWAY":
        raw_winner_goals = guess_away == away
        raw_loser_goals = guess_home == home
    else:
        raw_winner_goals = False
        raw_loser_goals = False

    is_winner_goals = is_winner_correct and raw_winner_goals
    is_eliminated_goals = is_winner_correct and raw_loser_goals
    is_diff_correct = is_winner_correct and (guess_home - guess_away) == (home - away)

    if is_exact_score and is_winner_correct:
        points = scoring_config.knockout_exact_and_advancing
    elif is_winner_correct and is_winner_goals:
        points = scoring_config.knockout_advancing_and_winner_goals
    elif is_winner_correct and is_diff_correct:
        points = scoring_config.knockout_advancing_and_diff
    elif is_winner_correct and is_eliminated_goals:
        points = scoring_config.knockout_advancing_and_loser_goals
    elif is_winner_correct:
        points = scoring_config.knockout_advancing_only
    else:
        points = 0

    return {
        "points": points,
        "exact_score": is_exact_score,
        "advancing_correct": is_winner_correct,
        "advancing_goals_correct": is_winner_goals,
        "diff_correct": is_diff_correct,
        "eliminated_goals_correct": is_eliminated_goals,
    }

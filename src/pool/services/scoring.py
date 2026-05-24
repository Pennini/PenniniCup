from src.pool.services.rules import PHASE_GROUP, phase_for_match


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


def calculate_bet_points(bet, scoring_config, pool_type=None):
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

    # KNOCKOUT phase — positional scoring for both Tipo 1 and Tipo 2.
    # Palpite de empate (home == away): pontos fixos somente quando o placar do tempo
    # regulamentar tambem termina empatado (qualquer placar). Jogos decididos por
    # penaltis ainda contam como empate no regulamentar e pagam os pontos fixos.
    if guess_home == guess_away:
        is_advancing_correct = bool(match.winner_id and bet.winner_pred_id == match.winner_id)
        real_is_draw = home == away
        return {
            "points": scoring_config.knockout_draw_prediction_points if real_is_draw else 0,
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

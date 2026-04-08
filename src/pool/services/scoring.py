from src.pool.services.rules import PHASE_GROUP, phase_for_match


def _winner_from_score(home_score, away_score):
    if home_score > away_score:
        return "HOME"
    if away_score > home_score:
        return "AWAY"
    return "DRAW"


def calculate_bet_points(bet, scoring_config):
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
            "winner_or_draw": False,
            "winner_advancing": False,
            "one_team_score": False,
        }

    predicted_winner = _winner_from_score(bet.home_score_pred, bet.away_score_pred)
    real_winner = _winner_from_score(match.home_score, match.away_score)

    exact_score = bet.home_score_pred == match.home_score and bet.away_score_pred == match.away_score
    one_team_score = (
        bet.home_score_pred == match.home_score or bet.away_score_pred == match.away_score
    ) and not exact_score

    points = 0
    winner_or_draw = predicted_winner == real_winner
    winner_advancing = False

    phase = phase_for_match(match)
    if phase == PHASE_GROUP:
        if exact_score:
            points += scoring_config.group_winner_or_draw_points
            points += scoring_config.group_exact_score_points
        else:
            if winner_or_draw:
                points += scoring_config.group_winner_or_draw_points
            if one_team_score:
                points += scoring_config.group_one_team_score_points
    else:
        if match.winner_id and bet.winner_pred_id == match.winner_id:
            winner_advancing = True
            points += scoring_config.knockout_winner_advancing_points

        if exact_score:
            points += scoring_config.knockout_exact_score_points
        elif one_team_score:
            points += scoring_config.knockout_one_team_score_points

    return {
        "points": points,
        "exact_score": exact_score,
        "winner_or_draw": winner_or_draw,
        "winner_advancing": winner_advancing,
        "one_team_score": one_team_score,
    }

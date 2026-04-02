from collections import defaultdict

from src.football.models import Group, Match
from src.pool.services.rules import PHASE_GROUP, phase_for_match


class GroupTableLine:
    def __init__(self, team):
        self.team = team
        self.points = 0
        self.goal_difference = 0
        self.goals_for = 0


def _sort_group_lines(lines):
    return sorted(
        lines,
        key=lambda line: (
            line.points,
            line.goal_difference,
            line.goals_for,
            line.team.code,
        ),
        reverse=True,
    )


def projected_group_top2(participant, season):
    matches = (
        Match.objects.filter(season=season, group__isnull=False)
        .select_related("group", "home_team", "away_team", "stage")
        .order_by("match_number")
    )

    bets_by_match_id = {bet.match_id: bet for bet in participant.bets.select_related("match").all()}
    table = defaultdict(dict)

    for match in matches:
        if phase_for_match(match) != PHASE_GROUP:
            continue
        if not (match.home_team_id and match.away_team_id):
            continue

        group_id = match.group_id
        if match.home_team_id not in table[group_id]:
            table[group_id][match.home_team_id] = GroupTableLine(match.home_team)
        if match.away_team_id not in table[group_id]:
            table[group_id][match.away_team_id] = GroupTableLine(match.away_team)

        bet = bets_by_match_id.get(match.id)
        if not bet:
            continue

        home = bet.home_score_pred
        away = bet.away_score_pred

        home_line = table[group_id][match.home_team_id]
        away_line = table[group_id][match.away_team_id]

        home_line.goals_for += home
        home_line.goal_difference += home - away
        away_line.goals_for += away
        away_line.goal_difference += away - home

        if home > away:
            home_line.points += 3
        elif away > home:
            away_line.points += 3
        else:
            home_line.points += 1
            away_line.points += 1

    group_map = {}
    for group in Group.objects.filter(stage__season=season).order_by("name"):
        lines = list(table[group.id].values())
        if not lines:
            continue

        ranking = _sort_group_lines(lines)
        if len(ranking) >= 2:
            group_map[f"{group.name}1"] = ranking[0].team
            group_map[f"{group.name}2"] = ranking[1].team

    return group_map


def resolve_knockout_placeholder_team(placeholder, projected_top2):
    if not placeholder:
        return None

    normalized = placeholder.replace(" ", "").upper()
    return projected_top2.get(normalized)

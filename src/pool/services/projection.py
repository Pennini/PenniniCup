from collections import defaultdict
from itertools import groupby

from src.football.models import Group, Match
from src.pool.services.rules import PHASE_GROUP, phase_for_match


class GroupTableLine:
    def __init__(self, team):
        self.team = team
        self.position = 0
        self.played = 0
        self.won = 0
        self.drawn = 0
        self.lost = 0
        self.points = 0
        self.goals_against = 0
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
    projected_groups = projected_group_standings(participant=participant, season=season)
    group_map = {}

    for group_data in projected_groups:
        standings = group_data["standings"]
        if len(standings) < 2:
            continue
        group_name = group_data["group"].name
        group_map[f"{group_name}1"] = standings[0].team
        group_map[f"{group_name}2"] = standings[1].team

    return group_map


def projected_group_standings(participant, season):
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

        home_line.played += 1
        away_line.played += 1
        home_line.goals_for += home
        home_line.goals_against += away
        home_line.goal_difference += home - away
        away_line.goals_for += away
        away_line.goals_against += home
        away_line.goal_difference += away - home

        if home > away:
            home_line.points += 3
            home_line.won += 1
            away_line.lost += 1
        elif away > home:
            away_line.points += 3
            away_line.won += 1
            home_line.lost += 1
        else:
            home_line.points += 1
            away_line.points += 1
            home_line.drawn += 1
            away_line.drawn += 1

    projected_groups = []
    for group in Group.objects.filter(stage__season=season).order_by("name"):
        lines = list(table[group.id].values())
        if not lines:
            continue

        ranking = _sort_group_lines(lines)
        for position, line in enumerate(ranking, start=1):
            line.position = position

        projected_groups.append({"group": group, "standings": ranking})

    return projected_groups


def load_persisted_group_standings(participant):
    standings = list(
        participant.projected_standings.select_related("group", "team").order_by(
            "group__name", "position", "team__code"
        )
    )

    projected_groups = []
    for group, rows in groupby(standings, key=lambda row: row.group):
        projected_groups.append({"group": group, "standings": list(rows)})

    return projected_groups


def projected_group_top2_from_groups(projected_groups):
    group_map = {}

    for group_data in projected_groups:
        standings = group_data["standings"]
        if len(standings) < 2:
            continue
        group_name = group_data["group"].name
        group_map[f"{group_name}1"] = standings[0].team
        group_map[f"{group_name}2"] = standings[1].team

    return group_map


def sync_persisted_group_standings(participant):
    from src.pool.models import PoolParticipantStanding

    projected_groups = projected_group_standings(participant=participant, season=participant.pool.season)

    rows_to_save = []
    for group_data in projected_groups:
        group = group_data["group"]
        for line in group_data["standings"]:
            rows_to_save.append(
                PoolParticipantStanding(
                    participant=participant,
                    group=group,
                    team=line.team,
                    position=line.position,
                    played=line.played,
                    won=line.won,
                    drawn=line.drawn,
                    lost=line.lost,
                    goals_for=line.goals_for,
                    goals_against=line.goals_against,
                    goal_difference=line.goal_difference,
                    points=line.points,
                )
            )

    PoolParticipantStanding.objects.filter(participant=participant).delete()
    if rows_to_save:
        PoolParticipantStanding.objects.bulk_create(rows_to_save)

    return projected_groups


def resolve_knockout_placeholder_team(placeholder, projected_top2):
    if not placeholder:
        return None

    normalized = placeholder.replace(" ", "").upper()
    return projected_top2.get(normalized)

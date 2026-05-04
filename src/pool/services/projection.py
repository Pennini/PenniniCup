from collections import defaultdict
from itertools import groupby

from django.db import transaction

from src.football.models import AssignThird, Group, Match
from src.pool.services.rules import PHASE_GROUP, phase_for_match

GROUP_SCORE_WEIGHT = 1_000_000
GOAL_DIFF_SCORE_WEIGHT = 1_000


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


def calculate_ranking_score(points, goal_difference, goals_for):
    return (points * GROUP_SCORE_WEIGHT) + (goal_difference * GOAL_DIFF_SCORE_WEIGHT) + goals_for


def _sort_key_with_official_tiebreakers(line):
    # Official extra criteria can be incrementally added here without changing callers.
    world_ranking = line.team.world_ranking if line.team.world_ranking else 9999
    return (
        -line.points,
        -line.goal_difference,
        -line.goals_for,
        world_ranking,
        line.team.code,
    )


def _h2h_stats_for_cluster(cluster, match_results):
    team_ids = {line.team.id for line in cluster}
    h2h = {line.team.id: GroupTableLine(line.team) for line in cluster}
    for home_id, away_id, home_score, away_score in match_results:
        if home_id not in team_ids or away_id not in team_ids:
            continue
        home_h = h2h[home_id]
        away_h = h2h[away_id]
        home_h.goals_for += home_score
        home_h.goals_against += away_score
        home_h.goal_difference += home_score - away_score
        away_h.goals_for += away_score
        away_h.goals_against += home_score
        away_h.goal_difference += away_score - home_score
        if home_score > away_score:
            home_h.points += 3
            home_h.won += 1
            away_h.lost += 1
        elif away_score > home_score:
            away_h.points += 3
            away_h.won += 1
            home_h.lost += 1
        else:
            home_h.points += 1
            away_h.points += 1
            home_h.drawn += 1
            away_h.drawn += 1
    return h2h


def _resolve_cluster_h2h(cluster, match_results):
    if len(cluster) <= 1:
        return cluster

    h2h = _h2h_stats_for_cluster(cluster, match_results)
    by_h2h = sorted(
        cluster,
        key=lambda team: (
            -h2h[team.team.id].points,
            -h2h[team.team.id].goal_difference,
            -h2h[team.team.id].goals_for,
        ),
    )

    result = []
    i = 0
    while i < len(by_h2h):
        j = i + 1
        ref = h2h[by_h2h[i].team.id]
        while j < len(by_h2h):
            curr = h2h[by_h2h[j].team.id]
            if (
                curr.points == ref.points
                and curr.goal_difference == ref.goal_difference
                and curr.goals_for == ref.goals_for
            ):
                j += 1
            else:
                break
        sub_cluster = by_h2h[i:j]
        if len(sub_cluster) == 1:
            result.extend(sub_cluster)
        elif len(sub_cluster) < len(cluster):
            # Smaller sub-cluster: recalculate H2H for just these teams
            result.extend(_resolve_cluster_h2h(sub_cluster, match_results))
        else:
            # H2H made no progress (circular results or no matches between them)
            result.extend(sorted(sub_cluster, key=_sort_key_with_official_tiebreakers))
        i = j

    return result


def _sort_group_lines(lines, match_results):
    globally_sorted = sorted(lines, key=lambda team: (-team.points, -team.goal_difference, -team.goals_for))

    result = []
    i = 0
    while i < len(globally_sorted):
        j = i + 1
        ref = globally_sorted[i]
        while j < len(globally_sorted):
            curr = globally_sorted[j]
            if (
                curr.points == ref.points
                and curr.goal_difference == ref.goal_difference
                and curr.goals_for == ref.goals_for
            ):
                j += 1
            else:
                break
        cluster = globally_sorted[i:j]
        if len(cluster) == 1:
            result.extend(cluster)
        else:
            result.extend(_resolve_cluster_h2h(cluster, match_results))
        i = j

    return result


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
    match_results_by_group = defaultdict(list)

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
        if not bet or not bet.is_active:
            continue

        home = bet.home_score_pred
        away = bet.away_score_pred
        if home is None or away is None:
            continue

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

        match_results_by_group[group_id].append((match.home_team_id, match.away_team_id, home, away))

    projected_groups = []
    for group in Group.objects.filter(stage__season=season).order_by("name"):
        lines = list(table[group.id].values())
        if not lines:
            continue

        ranking = _sort_group_lines(lines, match_results_by_group[group.id])
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


def select_projected_best_thirds(projected_groups, limit=8):
    candidates = []
    for group_data in projected_groups:
        standings = group_data["standings"]
        if len(standings) < 3:
            continue

        third_line = standings[2]
        candidates.append(
            {
                "group": group_data["group"],
                "line": third_line,
                "score": calculate_ranking_score(
                    points=third_line.points,
                    goal_difference=third_line.goal_difference,
                    goals_for=third_line.goals_for,
                ),
            }
        )

    ranked = sorted(
        candidates,
        key=lambda row: (
            _sort_key_with_official_tiebreakers(row["line"]),
            row["group"].name,
        ),
    )

    qualified_group_names = {row["group"].name for row in ranked[:limit]}
    for position, row in enumerate(ranked, start=1):
        row["position_global"] = position
        row["is_qualified"] = row["group"].name in qualified_group_names

    qualified_groups_sorted = sorted(qualified_group_names)
    return {
        "ranked": ranked,
        "qualified": [row for row in ranked if row["is_qualified"]],
        "qualified_groups": qualified_groups_sorted,
        "groups_key": ",".join(qualified_groups_sorted),
    }


def build_projected_placeholder_map(projected_groups, third_rows):
    projected = projected_group_top2_from_groups(projected_groups=projected_groups)

    for row in third_rows:
        if not row["is_qualified"]:
            continue
        projected[f"{row['group'].name}3"] = row["line"].team

    return projected


def _normalize_placeholder(placeholder):
    normalized = (placeholder or "").upper()
    return "".join(ch for ch in normalized if ch.isalnum())


def load_assign_third_map(season, qualified_groups):
    if not qualified_groups:
        return {}

    groups_key = ",".join(sorted(qualified_groups))
    rows = AssignThird.objects.filter(season=season, groups_key=groups_key)
    return {_normalize_placeholder(row.placeholder): row.third_group.upper() for row in rows}


@transaction.atomic
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


def load_persisted_third_places(participant):
    persisted_rows = list(
        participant.projected_third_places.select_related("group", "team").order_by(
            "position_global", "group__name", "team__code"
        )
    )

    return [
        {
            "group": row.group,
            "line": row,
            "score": row.score,
            "position_global": row.position_global,
            "is_qualified": row.is_qualified,
        }
        for row in persisted_rows
    ]


@transaction.atomic
def sync_persisted_third_places(participant, projected_groups=None):
    from src.pool.models import PoolParticipantThirdPlace

    if projected_groups is None:
        projected_groups = projected_group_standings(participant=participant, season=participant.pool.season)

    selection = select_projected_best_thirds(projected_groups=projected_groups)
    rows_to_save = []

    for row in selection["ranked"]:
        line = row["line"]
        rows_to_save.append(
            PoolParticipantThirdPlace(
                participant=participant,
                group=row["group"],
                team=line.team,
                position_global=row["position_global"],
                points=line.points,
                goal_difference=line.goal_difference,
                goals_for=line.goals_for,
                score=row["score"],
                is_qualified=row["is_qualified"],
            )
        )

    PoolParticipantThirdPlace.objects.filter(participant=participant).delete()
    if rows_to_save:
        PoolParticipantThirdPlace.objects.bulk_create(rows_to_save)

    return selection


def resolve_knockout_placeholder_team(placeholder, projected_slots, assign_third_map=None):
    if not placeholder:
        return None

    normalized = _normalize_placeholder(placeholder)
    direct = projected_slots.get(normalized)
    if direct:
        return direct

    if len(normalized) == 2 and normalized[0].isalpha() and normalized[1] == "3":
        return projected_slots.get(normalized)

    if len(normalized) == 2 and normalized[0].isdigit() and normalized[1].isalpha():
        return projected_slots.get(f"{normalized[1]}{normalized[0]}")

    if len(normalized) == 2 and normalized[0] == "3" and normalized[1].isalpha():
        return projected_slots.get(f"{normalized[1]}3")

    if assign_third_map:
        mapped_group = assign_third_map.get(normalized)
        if mapped_group:
            return projected_slots.get(f"{mapped_group}3")

    return None

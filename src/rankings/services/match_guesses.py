"""Per-match guesses view: pick the default game (live → next → last played),
list the season's games for the selector, and build the rows of every eligible
participant's guess for one game — revealed only after that game's phase locks.
"""

from datetime import timedelta

from django.utils import timezone

from src.football.models import Match
from src.pool.models import PoolBet
from src.pool.services.rules import normalize_stage_key, phase_for_match
from src.rankings.services.leaderboard import eligible_participants

# A game counts as "live" while inside this window after kickoff.
LIVE_WINDOW = timedelta(hours=2)

_GROUP_LABEL = "Fase de Grupos"
_KNOCKOUT_STAGE_LABELS = {
    "R32": "32 Avos de Final",
    "R16": "Oitavas de Final",
    "QF": "Quartas de Final",
    "SF": "Semifinal",
    "FINAL": "Final",
    "THIRD": "3º Lugar",
}

_MATCH_RELATIONS = ("stage", "group", "home_team", "away_team")


def resolve_default_match(season, now=None):
    """Game pre-selected when the user opens the tab: a live game (within
    LIVE_WINDOW after kickoff), else the next upcoming game, else — once the
    tournament is over — the most recent game so the tab is never empty.
    """
    now = now or timezone.now()
    base = Match.objects.filter(season=season).select_related(*_MATCH_RELATIONS)

    live = (
        base.filter(match_date_brasilia__lte=now, match_date_brasilia__gt=now - LIVE_WINDOW)
        .order_by("-match_date_brasilia")
        .first()
    )
    if live:
        return live

    upcoming = base.filter(match_date_brasilia__gt=now).order_by("match_date_brasilia").first()
    if upcoming:
        return upcoming

    return base.order_by("-match_date_brasilia").first()


def stage_label(match):
    key = normalize_stage_key(match.stage)
    if key == "GROUP":
        return _GROUP_LABEL
    return _KNOCKOUT_STAGE_LABELS.get(key, match.stage.name)


def get_selectable_matches(season):
    """All season games in selector/carousel order (chronological, then match
    number as tiebreak). Returned as a list so the same fetch feeds both the
    <optgroup> selector and the prev/next carousel navigation.
    """
    return list(
        Match.objects.filter(season=season)
        .select_related(*_MATCH_RELATIONS)
        .order_by("match_date_brasilia", "match_number")
    )


def group_matches_by_phase(matches):
    """Bucket an ordered match list into <optgroup>s by phase label, preserving
    the incoming chronological order both across and within buckets.
    """
    groups = []
    index_by_label = {}
    for match in matches:
        label = stage_label(match)
        if label not in index_by_label:
            index_by_label[label] = len(groups)
            groups.append({"label": label, "matches": []})
        groups[index_by_label[label]]["matches"].append(match)
    return groups


def resolve_adjacent(matches, selected):
    """Previous/next game around `selected` within the ordered `matches` list,
    for the carousel arrows. Returns (None, None) when there is no selection or
    the selection is not in the list; None on either side marks an endpoint.
    """
    if selected is None:
        return None, None
    ids = [match.id for match in matches]
    try:
        index = ids.index(selected.id)
    except ValueError:
        return None, None
    prev_match = matches[index - 1] if index > 0 else None
    next_match = matches[index + 1] if index < len(matches) - 1 else None
    return prev_match, next_match


def resolve_selected_match(request, season):
    """The game chosen via ?match=<id> (validated against the pool's season),
    falling back to the default game for missing/invalid ids.
    """
    raw_id = request.GET.get("match")
    try:
        match_id = int(raw_id) if raw_id else None
    except (TypeError, ValueError):
        match_id = None

    if match_id is not None:
        match = Match.objects.filter(season=season, pk=match_id).select_related(*_MATCH_RELATIONS).first()
        if match:
            return match

    return resolve_default_match(season)


def _build_guess_rows(pool, match):
    participants = list(eligible_participants(pool).select_related("user", "user__profile").order_by("user__username"))
    bets = {
        bet.participant_id: bet
        for bet in PoolBet.objects.filter(participant__pool=pool, match=match, is_active=True).select_related(
            "winner_pred", "score"
        )
    }
    return [{"participant": participant, "bet": bets.get(participant.id)} for participant in participants]


def build_match_guesses_context(*, pool, request):
    matches = get_selectable_matches(pool.season)
    selected_match = resolve_selected_match(request, pool.season)
    prev_match, next_match = resolve_adjacent(matches, selected_match)
    context = {
        "selectable_match_groups": group_matches_by_phase(matches),
        "selected_match": selected_match,
        "prev_match": prev_match,
        "next_match": next_match,
        "selected_stage_label": None,
        "guesses_locked": False,
        "match_finished": False,
        "guess_rows": [],
    }
    if selected_match is None:
        return context

    context["selected_stage_label"] = stage_label(selected_match)
    context["match_finished"] = selected_match.status == Match.STATUS_FINISHED

    revealed = pool.is_phase_locked(phase_for_match(selected_match))
    context["guesses_locked"] = not revealed
    if revealed:
        context["guess_rows"] = _build_guess_rows(pool, selected_match)

    return context

import logging
from collections import defaultdict
from itertools import groupby
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from src.accounts.models import UserProfile
from src.football.models import Match, Season, Team
from src.pool.models import Pool, PoolBet, PoolParticipant
from src.pool.services.context_builder import build_pool_participant_view_context
from src.pool.services.rules import PHASE_GROUP, PHASE_KNOCKOUT, POOL_TYPE_1, POOL_TYPE_2, normalize_stage_key
from src.rankings.services.leaderboard import build_pool_leaderboard

from .forms import ProfilePreferencesForm

logger = logging.getLogger(__name__)
User = get_user_model()


def _active_or_current_season():
    today = timezone.localdate()
    season = Season.objects.filter(start_date__lte=today, end_date__gte=today).order_by("-year", "-start_date").first()
    if season is not None:
        return season
    return Season.objects.order_by("-year", "-start_date").first()


def _season_has_started(season):
    first_match = (
        Match.objects.filter(season=season)
        .order_by("match_date_brasilia", "match_number")
        .only("match_date_brasilia")
        .first()
    )
    if first_match is None:
        return False
    return timezone.now() >= first_match.match_date_brasilia


_KNOCKOUT_STAGE_LABELS = {
    "R32": "32 Avos de Final",
    "R16": "Oitavas de Final",
    "QF": "Quartas de Final",
    "SF": "Semifinal",
    "FINAL": "Final",
    "THIRD": "3º Lugar",
}

_KNOCKOUT_STAGE_ORDER = {"R32": 0, "R16": 1, "QF": 2, "SF": 3, "FINAL": 4, "THIRD": 5}


def _resolve_stage_key(stage):
    # Unknown stages (normalize returns "") keep their raw name so the label
    # falls back to whatever the FIFA sync stored instead of rendering blank.
    return normalize_stage_key(stage) or (stage.name if stage else "")


def _stage_sort_key(row):
    stage = row["match"].stage
    normalized = normalize_stage_key(stage)
    return (_KNOCKOUT_STAGE_ORDER.get(normalized, 99), row["match"].match_number or 0)


def _build_knockout_by_phase(knockout_rows, scoring_config):
    bonus_pts_each = scoring_config.knockout_team_advancement_bonus if scoring_config else 0
    sorted_rows = sorted(knockout_rows, key=_stage_sort_key)
    phases = []
    for stage_key, rows in groupby(sorted_rows, key=lambda r: _resolve_stage_key(r["match"].stage)):
        rows = list(rows)
        real_winners = [r["match"].winner for r in rows if r["match"].winner]
        real_winners_ids = {t.id for t in real_winners}
        predicted = [
            {
                "team": r["bet"].winner_pred,
                "advanced": r["bet"].winner_pred_id in real_winners_ids,
                "decided": r["match"].winner is not None,
            }
            for r in rows
            if r.get("bet") and r["bet"] and r["bet"].winner_pred
        ]
        bonus_rows = [r for r in rows if r["bet_score"] and r["bet_score"].team_advancement_bonus]
        bonus_count = len(bonus_rows)
        phases.append(
            {
                "stage_name": stage_key,
                "stage_label": _KNOCKOUT_STAGE_LABELS.get(stage_key, stage_key),
                "rows": rows,
                "predicted_winners": predicted,
                "real_winners": real_winners,
                "bonus_count": bonus_count,
                "bonus_total": bonus_count * bonus_pts_each,
                "bonus_pts_each": bonus_pts_each,
                "has_results": bool(real_winners),
                "has_bonus": bonus_count > 0,
            }
        )
    return phases


def _build_group_audit(participant, season, scoring_config):
    """Per-group audit of the group_qualifier_bonus the participant earned.

    Returns list[{group, rows[3], group_points, has_real}] where each row is
    {position, predicted_team, real_team, qualified, position_match, points,
    settled, third_advanced}.

    settled is False for the 3rd-place row when R32 has not yet been drawn,
    so the template can render that row as 'pending' instead of 'wrong'.
    """
    from src.football.models import Group, Standing
    from src.pool.services.ranking import _real_qualifier_position_map

    if scoring_config is None or participant is None:
        return []

    real_rows = (
        Standing.objects.filter(season=season, position__lte=3)
        .select_related("team", "group")
        .order_by("group__name", "position")
    )
    proj_rows = (
        participant.projected_standings.filter(position__lte=3)
        .select_related("team", "group")
        .order_by("group__name", "position")
    )

    real_by_group = defaultdict(dict)
    for s in real_rows:
        real_by_group[s.group_id][s.position] = s

    real_qualifier_positions, r32_drawn = _real_qualifier_position_map(season)
    real_qualifier_ids_by_group = {gid: set(positions.values()) for gid, positions in real_qualifier_positions.items()}

    proj_by_group = defaultdict(dict)
    for p in proj_rows:
        proj_by_group[p.group_id][p.position] = p

    audit = []
    for group in Group.objects.filter(stage__season=season).order_by("name"):
        real_positions = real_by_group.get(group.id, {})
        proj_positions = proj_by_group.get(group.id, {})
        has_real = bool(real_positions)
        real_qualifier_ids = real_qualifier_ids_by_group.get(group.id, set())

        rows = []
        group_points = 0
        for position in (1, 2, 3):
            proj = proj_positions.get(position)
            real = real_positions.get(position)
            predicted_team = proj.team if proj else None
            real_team = real.team if real else None

            settled = has_real if position <= 2 else r32_drawn
            qualified = bool(settled and predicted_team and predicted_team.id in real_qualifier_ids)
            position_match = bool(qualified and real_team and predicted_team.id == real_team.id)
            third_advanced = bool(position == 3 and real_team and real_team.id in real_qualifier_ids)

            points = 0
            if qualified:
                points = scoring_config.group_qualifier_points
                if position_match:
                    points += scoring_config.group_qualifier_position_bonus

            group_points += points
            rows.append(
                {
                    "position": position,
                    "predicted_team": predicted_team,
                    "real_team": real_team,
                    "qualified": qualified,
                    "position_match": position_match,
                    "points": points,
                    "settled": settled,
                    "third_advanced": third_advanced,
                }
            )

        audit.append(
            {
                "group": group,
                "rows": rows,
                "group_points": group_points,
                "has_real": has_real,
            }
        )

    return audit


def _build_profile_context(request, *, profile_user, is_owner):
    profile_obj, _ = UserProfile.objects.get_or_create(user=profile_user)
    active_tab = (request.GET.get("tab") or "bets").strip()
    selected_slug = (request.GET.get("pool") or "").strip()

    if active_tab not in ("bets", "classification", "knockout"):
        params = {}
        if selected_slug:
            params["pool"] = selected_slug
        params["tab"] = "bets"
        return None, redirect(f"{request.path}?{urlencode(params)}")

    active_season = _active_or_current_season()
    world_cup_teams = Team.objects.none()
    if active_season is not None:
        world_cup_teams = Team.objects.filter(group__stage__season=active_season).order_by("name").distinct()

    form = ProfilePreferencesForm(request.POST or None, request.FILES or None, instance=profile_obj)
    form.fields["world_cup_team"].queryset = world_cup_teams

    if request.method == "POST" and is_owner:
        selected_slug = (request.POST.get("selected_pool") or "").strip()
        active_tab = (request.POST.get("active_tab") or "bets").strip()

        if form.is_valid():
            form.save()
            messages.success(request, "Perfil atualizado com sucesso.")
        else:
            messages.error(request, "Não foi possível salvar seu perfil. Verifique os campos e tente novamente.")

        params = {}
        if selected_slug:
            params["pool"] = selected_slug
        if active_tab in ("bets", "classification", "knockout"):
            params["tab"] = active_tab
        return None, redirect(f"{request.path}?{urlencode(params)}" if params else request.path)

    participations = list(
        PoolParticipant.objects.filter(user=profile_user, is_active=True)
        .select_related("pool", "pool__season", "user")
        .order_by("pool__name")
    )

    selected_participation = None
    if selected_slug:
        selected_participation = next((item for item in participations if item.pool.slug == selected_slug), None)
        if selected_participation is None:
            messages.warning(request, "Bolão selecionado não pertence ao perfil informado.")

    if selected_participation is None and participations:
        selected_participation = participations[0]

    selected_pool = selected_participation.pool if selected_participation else None
    selected_slug = selected_pool.slug if selected_pool else ""

    is_superuser_viewer = request.user.is_superuser
    is_public_predictions_visible = is_owner or is_superuser_viewer
    if selected_pool is not None and not is_owner and not is_superuser_viewer:
        is_public_predictions_visible = _season_has_started(selected_pool.season)

    # Non-owners (non-superuser) only see bets for matches already closed to betting.
    show_locked_only = not is_owner and not is_superuser_viewer

    predictions_context = {
        "match_rows": [],
        "group_rows": [],
        "knockout_rows": [],
        "projected_groups": [],
        "can_bet": False,
        "group_locked": False,
        "knockout_locked": False,
        "projection_pending": False,
        "top_scorer_options": [],
        "page_mode": "result",
        "bracket_left": [],
        "bracket_right": [],
        "final_match": None,
        "third_place_match": None,
        "bracket_height": 280,
    }
    if selected_participation is not None and is_public_predictions_visible:
        predictions_context = build_pool_participant_view_context(
            pool=selected_participation.pool,
            participant=selected_participation,
            ensure_bets=False,
        )
        if show_locked_only:
            filtered_group = [r for r in predictions_context["group_rows"] if r["locked"]]
            filtered_knockout = [r for r in predictions_context["knockout_rows"] if r["locked"]]
            predictions_context["group_rows"] = filtered_group
            predictions_context["knockout_rows"] = filtered_knockout
            predictions_context["match_rows"] = filtered_group + filtered_knockout

    scoring_config = selected_pool.get_scoring_config() if selected_pool else None
    knockout_by_phase = _build_knockout_by_phase(predictions_context.get("knockout_rows", []), scoring_config)
    group_audit = (
        _build_group_audit(selected_participation, selected_pool.season, scoring_config)
        if (selected_participation and selected_pool and is_public_predictions_visible)
        else []
    )

    context = {
        "profile_user": profile_user,
        "profile_obj": profile_obj,
        "profile_form": form,
        "is_owner_profile": is_owner,
        "active_season": active_season,
        "participations": participations,
        "selected_pool": selected_pool,
        "selected_pool_slug": selected_slug,
        "selected_participant": selected_participation,
        "active_tab": active_tab,
        "can_view_predictions": is_public_predictions_visible,
        "show_locked_only": show_locked_only,
        "scoring_config": scoring_config,
        "knockout_by_phase": knockout_by_phase,
        "group_audit": group_audit,
        **predictions_context,
    }
    return context, None


def _resolve_selected_participation(request, participations):
    selected_slug = (request.GET.get("pool") or "").strip()
    selected_participation = None

    if selected_slug:
        selected_participation = next((item for item in participations if item.pool.slug == selected_slug), None)
        if selected_participation is None:
            messages.warning(request, "Bolão selecionado não encontrado entre suas participações ativas.")

    if selected_participation is None and participations:
        selected_participation = participations[0]

    return selected_participation, selected_slug


def _build_home_next_matches_context(*, participant, pool, limit=3):
    upcoming_matches = list(
        Match.objects.filter(
            season=pool.season,
            status=Match.STATUS_SCHEDULED,
            match_date_brasilia__gte=timezone.now(),
        )
        .select_related("stage", "group", "home_team", "away_team")
        .order_by("match_date_brasilia", "match_number")[:limit]
    )

    if not upcoming_matches:
        return []

    bets_by_match_id = {
        bet.match_id: bet
        for bet in PoolBet.objects.filter(
            participant=participant,
            match_id__in=[match.id for match in upcoming_matches],
        ).all()
    }

    rows = []
    for match in upcoming_matches:
        bet = bets_by_match_id.get(match.id)
        rows.append(
            {
                "match": match,
                "bet": bet,
                "has_prediction": bool(bet and bet.is_active),
                "is_prediction_incomplete": bool(bet and not bet.is_active),
            }
        )

    return rows


def _build_home_dashboard_context(*, participant, pool):
    pool.refresh_prize_distribution(save=True)
    leaderboard_rows = build_pool_leaderboard(pool=pool)
    current_row = next((row for row in leaderboard_rows if row.participant.id == participant.id), None)

    group_lock_at = pool.get_phase_lock_time(PHASE_GROUP)
    knockout_lock_at = pool.get_phase_lock_time(PHASE_KNOCKOUT)
    now = timezone.now()
    upcoming_locks = [lock for lock in (group_lock_at, knockout_lock_at) if lock and lock >= now]
    next_lock_at = min(upcoming_locks) if upcoming_locks else None

    total_participants = len(leaderboard_rows)
    is_paid = participant.can_bet()

    return {
        "selected_pool": pool,
        "selected_participant": participant,
        "selected_pool_slug": pool.slug,
        "pool_is_paid": is_paid,
        "current_position": current_row.position if current_row else None,
        "current_points": participant.total_points,
        "total_participants": total_participants,
        "group_lock_at": group_lock_at,
        "knockout_lock_at": knockout_lock_at,
        "next_lock_at": next_lock_at,
        "next_matches_rows": _build_home_next_matches_context(participant=participant, pool=pool),
    }


# Create your views here.
def index(request):
    context = {
        "hero_background_url": "",
        "active_home_tab": (request.GET.get("tab") or "overview").strip(),
    }

    if not request.user.is_authenticated:
        return render(request, "penninicup/index.html", context)

    participations = list(
        PoolParticipant.objects.filter(user=request.user, is_active=True)
        .select_related("pool", "pool__season")
        .order_by("pool__name")
    )

    selected_participation, selected_slug = _resolve_selected_participation(request, participations)

    if selected_participation is None:
        context.update(
            {
                "participations": participations,
                "selected_pool": None,
                "selected_pool_slug": selected_slug,
                "selected_participant": None,
                "pool_is_paid": False,
                "current_position": None,
                "current_points": 0,
                "total_participants": 0,
                "group_lock_at": None,
                "knockout_lock_at": None,
                "next_lock_at": None,
                "next_matches_rows": [],
            }
        )
        return render(request, "penninicup/index.html", context)

    context.update(
        {
            "participations": participations,
            **_build_home_dashboard_context(
                participant=selected_participation,
                pool=selected_participation.pool,
            ),
        }
    )

    return render(request, "penninicup/index.html", context)


@login_required
def rules(request):
    pools = list(Pool.objects.filter(is_active=True).select_related("season").order_by("name"))
    source = request.POST if request.method == "POST" else request.GET
    selected_slug = (source.get("pool") or "").strip()

    selected_pool = None
    if selected_slug:
        selected_pool = next((pool for pool in pools if pool.slug == selected_slug), None)
    if selected_pool is None and pools:
        selected_pool = pools[0]

    if request.method == "POST":
        params = {}
        if selected_pool:
            selected_pool.refresh_prize_distribution(save=True)
            messages.success(request, "Premiação atualizada com sucesso.")
            params["pool"] = selected_pool.slug
        elif selected_slug:
            params["pool"] = selected_slug
        if params:
            return redirect(f"{reverse('penninicup:rules')}?{urlencode(params)}")
        return redirect(reverse("penninicup:rules"))

    scoring_config = selected_pool.get_scoring_config() if selected_pool else None
    if selected_pool:
        selected_pool.refresh_prize_distribution(save=True)
        selected_pool.refresh_from_db()
    group_lock_at = selected_pool.get_phase_lock_time(PHASE_GROUP) if selected_pool else None
    knockout_lock_at = selected_pool.get_phase_lock_time(PHASE_KNOCKOUT) if selected_pool else None

    context = {
        "pools": pools,
        "selected_pool": selected_pool,
        "scoring_config": scoring_config,
        "group_lock_at": group_lock_at,
        "knockout_lock_at": knockout_lock_at,
        "pool_type_1": POOL_TYPE_1,
        "pool_type_2": POOL_TYPE_2,
        "group_max_points": (scoring_config.group_exact_score if scoring_config else 0),
        "knockout_max_points": (scoring_config.knockout_exact_and_advancing if scoring_config else 0),
        "bonus_total_points": (
            scoring_config.bonus_champion_points
            + scoring_config.bonus_runner_up_points
            + scoring_config.bonus_third_place_points
            + scoring_config.bonus_top_scorer_points
            if scoring_config
            else 0
        ),
        "qualifier_bonus_max": (
            scoring_config.group_qualifier_points + scoring_config.group_qualifier_position_bonus
            if scoring_config
            else 0
        ),
    }
    return render(request, "penninicup/rules.html", context)


@login_required
def profile(request):
    context, redirect_response = _build_profile_context(request, profile_user=request.user, is_owner=True)
    if redirect_response is not None:
        return redirect_response
    return render(request, "penninicup/profile.html", context)


@login_required
def profile_user(request, username):
    profile_user_obj = get_object_or_404(User, username=username)
    context, redirect_response = _build_profile_context(
        request,
        profile_user=profile_user_obj,
        is_owner=profile_user_obj.id == request.user.id,
    )
    if redirect_response is not None:
        return redirect_response
    return render(request, "penninicup/profile.html", context)

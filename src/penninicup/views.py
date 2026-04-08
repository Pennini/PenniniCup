import logging

from django.shortcuts import render

from src.pool.models import Pool
from src.pool.services.rules import PHASE_GROUP, PHASE_KNOCKOUT

logger = logging.getLogger(__name__)


# Create your views here.
def index(request):
    return render(request, "penninicup/index.html")


def rules(request):
    pools = list(Pool.objects.filter(is_active=True).select_related("season").order_by("name"))
    selected_slug = (request.GET.get("pool") or "").strip()

    selected_pool = None
    if selected_slug:
        selected_pool = next((pool for pool in pools if pool.slug == selected_slug), None)
    if selected_pool is None and pools:
        selected_pool = pools[0]

    scoring_config = selected_pool.get_scoring_config() if selected_pool else None
    group_lock_at = selected_pool.get_phase_lock_time(PHASE_GROUP) if selected_pool else None
    knockout_lock_at = selected_pool.get_phase_lock_time(PHASE_KNOCKOUT) if selected_pool else None

    context = {
        "pools": pools,
        "selected_pool": selected_pool,
        "scoring_config": scoring_config,
        "group_lock_at": group_lock_at,
        "knockout_lock_at": knockout_lock_at,
        "group_max_points": (
            scoring_config.group_winner_or_draw_points + scoring_config.group_exact_score_points
            if scoring_config
            else 0
        ),
        "knockout_max_points": (
            scoring_config.knockout_winner_advancing_points + scoring_config.knockout_exact_score_points
            if scoring_config
            else 0
        ),
        "bonus_total_points": (
            scoring_config.bonus_champion_points
            + scoring_config.bonus_runner_up_points
            + scoring_config.bonus_third_place_points
            + scoring_config.bonus_top_scorer_points
            if scoring_config
            else 0
        ),
    }
    return render(request, "penninicup/rules.html", context)

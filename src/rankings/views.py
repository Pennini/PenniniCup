from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden, HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from src.pool.models import Pool, PoolParticipant
from src.rankings.services.dashboard import build_dashboard_data
from src.rankings.services.divisions import build_divisions
from src.rankings.services.leaderboard import build_pool_leaderboard
from src.rankings.services.match_guesses import build_match_guesses_context


def build_ranking_dashboard_context(*, pool, participant):
    leaderboard_rows = build_pool_leaderboard(pool=pool)
    total_participants = len(leaderboard_rows)

    current_row = next(
        (row for row in leaderboard_rows if row.participant.id == participant.id),
        None,
    )
    leader_points = leaderboard_rows[0].participant.total_points if leaderboard_rows else 0
    points_gap = max(leader_points - participant.total_points, 0)

    podium_rows = leaderboard_rows[:3]
    podium_prizes = [
        "Premiação 1º lugar",
        "Premiação 2º lugar",
        "Premiação 3º lugar",
    ]
    podium_cards = []
    for row in podium_rows:
        prize_text = (
            podium_prizes[row.position - 1] if row.position <= len(podium_prizes) else "Premiação não definida"
        )
        podium_cards.append(
            {
                "position": row.position,
                "username": row.participant.user.username,
                "points": row.participant.total_points,
                "prize": prize_text,
                "prize_amount": (
                    pool.first_place_amount
                    if row.position == 1
                    else pool.second_place_amount
                    if row.position == 2
                    else pool.third_place_amount
                ),
            }
        )

    return {
        "pool": pool,
        "leaderboard_rows": leaderboard_rows,
        "leaderboard_divisions": build_divisions(leaderboard_rows),
        "podium_cards": podium_cards,
        "current_participant": participant,
        "current_position": current_row.position if current_row else None,
        "total_participants": total_participants,
        "leader_points": leader_points,
        "points_gap": points_gap,
        "total_prize_amount": pool.total_prize_amount,
        "first_place_amount": pool.first_place_amount,
        "second_place_amount": pool.second_place_amount,
        "third_place_amount": pool.third_place_amount,
    }


def build_dashboard_tab_context(*, pool, participant, request):
    """Branch the ranking dashboard between the leaderboard and the per-match
    guesses view based on ?tab. Shared by both entry points — the slug-based
    `pool_ranking_dashboard` and the slugless `pool.views.ranking_tab` (the one
    the navbar links to) — so the toggle behaves identically on both.
    """
    active_tab = request.GET.get("tab") or "ranking"
    if active_tab not in ("ranking", "palpites"):
        active_tab = "ranking"

    if active_tab == "palpites":
        context = {"pool": pool, "current_participant": participant}
        context.update(build_match_guesses_context(pool=pool, request=request))
    else:
        pool.refresh_prize_distribution()
        context = build_ranking_dashboard_context(pool=pool, participant=participant)

    context["active_tab"] = active_tab
    return context


@login_required
def pool_ranking_dashboard(request, slug):
    pool = get_object_or_404(Pool.objects.select_related("season"), slug=slug, is_active=True)
    current_participant = get_object_or_404(PoolParticipant, pool=pool, user=request.user, is_active=True)
    context = build_dashboard_tab_context(pool=pool, participant=current_participant, request=request)
    return render(request, "rankings/pool_dashboard.html", context)


@login_required
def match_guesses_partial(request, slug):
    """Server-rendered body of the per-match guesses carousel, fetched via AJAX
    when the user moves the carousel or picks a game — so switching games is
    dynamic without a full reload. The per-phase lock is re-applied here, so a
    user can never reveal a still-locked game's guesses by forging ?match=<id>.
    """
    pool = get_object_or_404(Pool.objects.select_related("season"), slug=slug, is_active=True)
    get_object_or_404(PoolParticipant, pool=pool, user=request.user, is_active=True)
    context = {"pool": pool}
    context.update(build_match_guesses_context(pool=pool, request=request))
    return render(request, "rankings/partials/_match_guesses_body.html", context)


@login_required
def pool_dashboard_overview(request, slug):
    """Standalone overview dashboard for a single pool (donut, KPIs, evolution,
    utilization, hall of fame). Renders only the shell — the page fetches the
    aggregated metrics from `pool_dashboard_data` and draws them client-side.
    """
    pool = get_object_or_404(Pool.objects.select_related("season"), slug=slug, is_active=True)
    participant = get_object_or_404(PoolParticipant, pool=pool, user=request.user, is_active=True)
    return render(
        request,
        "rankings/dashboard_overview.html",
        {"pool": pool, "current_participant": participant},
    )


@login_required
def pool_dashboard_data(request, slug):
    """Aggregated dashboard metrics as JSON, consumed by the overview page."""
    pool = get_object_or_404(Pool.objects.select_related("season"), slug=slug, is_active=True)
    participant = get_object_or_404(PoolParticipant, pool=pool, user=request.user, is_active=True)
    return JsonResponse(build_dashboard_data(pool=pool, participant=participant))


@login_required
def toggle_supporter_stars(request, slug):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    if not request.user.is_superuser:
        return HttpResponseForbidden()
    pool = get_object_or_404(Pool, slug=slug, is_active=True)
    pool.show_supporter_stars = not pool.show_supporter_stars
    pool.save(update_fields=["show_supporter_stars"])
    return redirect("rankings:pool-dashboard", slug=slug)

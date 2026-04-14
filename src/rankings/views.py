from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, render

from src.pool.models import Pool, PoolParticipant
from src.rankings.services.leaderboard import build_pool_leaderboard


@login_required
def pool_ranking_dashboard(request, slug):
    pool = get_object_or_404(Pool.objects.select_related("season"), slug=slug, is_active=True)
    pool.refresh_prize_distribution()
    current_participant = get_object_or_404(PoolParticipant, pool=pool, user=request.user, is_active=True)

    leaderboard_rows = build_pool_leaderboard(pool=pool)
    total_participants = len(leaderboard_rows)

    current_row = next(
        (row for row in leaderboard_rows if row.participant.id == current_participant.id),
        None,
    )
    leader_points = leaderboard_rows[0].participant.total_points if leaderboard_rows else 0
    points_gap = max(leader_points - current_participant.total_points, 0)

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

    context = {
        "pool": pool,
        "leaderboard_rows": leaderboard_rows,
        "podium_cards": podium_cards,
        "current_participant": current_participant,
        "current_position": current_row.position if current_row else None,
        "total_participants": total_participants,
        "leader_points": leader_points,
        "points_gap": points_gap,
        "total_prize_amount": pool.total_prize_amount,
        "first_place_amount": pool.first_place_amount,
        "second_place_amount": pool.second_place_amount,
        "third_place_amount": pool.third_place_amount,
    }
    return render(request, "rankings/pool_dashboard.html", context)

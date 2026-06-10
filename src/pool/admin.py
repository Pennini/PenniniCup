from io import StringIO

from django.contrib import admin
from django.core.management import call_command
from django.shortcuts import render
from django.urls import path

from src.football.models import Match
from src.pool.management.commands.notify_missing_bets import _current_active_phase, _get_participants_with_missing_bets
from src.pool.models import (
    Pool,
    PoolBet,
    PoolBetScore,
    PoolLockWindow,
    PoolOfficialResult,
    PoolParticipant,
    PoolParticipantStanding,
    PoolParticipantThirdPlace,
    PoolProjectionRecalc,
    PoolScoringConfig,
)


@admin.register(Pool)
class PoolAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "season",
        "pool_type",
        "entry_fee",
        "admin_fee_percentage",
        "requires_payment",
        "is_active",
    )
    search_fields = ("name", "slug")
    list_filter = ("is_active", "requires_payment", "season", "pool_type")
    change_list_template = "admin/pool/pool_change_list.html"

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "notify-missing-bets/",
                self.admin_site.admin_view(self.notify_missing_bets_view),
                name="pool_notify_missing_bets",
            ),
        ]
        return custom + urls

    def notify_missing_bets_view(self, request):
        ctx = self.admin_site.each_context(request)
        ctx["title"] = "Notificar Palpites Pendentes"
        ctx["active_pools"] = Pool.objects.filter(is_active=True).order_by("name")
        ctx["dry_run_results"] = None
        ctx["send_output"] = None

        if request.method == "POST":
            action = request.POST.get("action")
            pool_ids = [int(x) for x in request.POST.getlist("pool_ids") if x] or None

            if action == "dry_run":
                missing_by_user, user_map = _get_participants_with_missing_bets(pool_ids)
                results = []
                for user_id, pools_missing in missing_by_user.items():
                    user = user_map[user_id]
                    results.append(
                        {
                            "username": user.username,
                            "email": user.email or "—",
                            "pools": sorted(
                                [
                                    (name, data["count"], data["deadline"], data["phase_label"])
                                    for name, data in pools_missing.items()
                                ],
                                key=lambda x: x[0],
                            ),
                            "total": sum(d["count"] for d in pools_missing.values()),
                        }
                    )
                results.sort(key=lambda x: x["username"])
                ctx["dry_run_results"] = results

                # Diagnostics: show why pools were skipped
                all_pools = Pool.objects.filter(is_active=True)
                if pool_ids:
                    all_pools = all_pools.filter(id__in=pool_ids)
                diagnostics = []
                for pool in all_pools.select_related("season"):
                    phase = _current_active_phase(pool)
                    lock_group = pool.get_phase_lock_time("GROUP")
                    lock_ko = pool.get_phase_lock_time("KNOCKOUT")
                    scheduled_count = Match.objects.filter(season=pool.season, status=Match.STATUS_SCHEDULED).count()
                    diagnostics.append(
                        {
                            "name": pool.name,
                            "pool_type": pool.pool_type,
                            "active_phase": phase or "—  (fase travada / sem jogos)",
                            "lock_group": lock_group,
                            "lock_ko": lock_ko,
                            "scheduled_matches": scheduled_count,
                            "skipped": phase is None or scheduled_count == 0,
                        }
                    )
                ctx["diagnostics"] = diagnostics

            elif action == "send":
                out = StringIO()
                kwargs = {"dry_run": False, "stdout": out}
                if pool_ids:
                    kwargs["pool_id"] = pool_ids
                call_command("notify_missing_bets", **kwargs)
                ctx["send_output"] = out.getvalue()

        return render(request, "admin/pool/notify_missing_bets.html", ctx)


@admin.register(PoolParticipant)
class PoolParticipantAdmin(admin.ModelAdmin):
    list_display = (
        "pool",
        "user",
        "is_active",
        "total_points",
        "group_points",
        "knockout_points",
        "bonus_points",
        "qualifier_bonus_points",
        "champion_hit",
        "top_scorer_hit",
    )
    list_filter = ("pool", "is_active")
    search_fields = ("user__username", "user__email", "pool__name")


@admin.register(PoolBet)
class PoolBetAdmin(admin.ModelAdmin):
    list_display = (
        "participant",
        "match",
        "home_score_pred",
        "away_score_pred",
        "winner_pred",
        "is_active",
        "updated_at",
    )
    list_filter = ("participant__pool",)
    search_fields = ("participant__user__username",)


@admin.register(PoolBetScore)
class PoolBetScoreAdmin(admin.ModelAdmin):
    list_display = (
        "bet",
        "points",
        "exact_score",
        "advancing_correct",
        "advancing_goals_correct",
        "diff_correct",
        "eliminated_goals_correct",
    )


@admin.register(PoolLockWindow)
class PoolLockWindowAdmin(admin.ModelAdmin):
    list_display = ("pool", "phase", "lock_at")
    list_filter = ("phase", "pool")


@admin.register(PoolParticipantStanding)
class PoolParticipantStandingAdmin(admin.ModelAdmin):
    list_display = (
        "participant",
        "group",
        "team",
        "position",
        "played",
        "won",
        "drawn",
        "lost",
        "goals_for",
        "goals_against",
        "goal_difference",
        "points",
    )
    list_filter = ("group__stage__season",)
    search_fields = ("participant__user__username", "team__name")


@admin.register(PoolParticipantThirdPlace)
class PoolParticipantThirdPlaceAdmin(admin.ModelAdmin):
    list_display = (
        "participant",
        "group",
        "team",
        "position_global",
        "points",
        "goal_difference",
        "goals_for",
        "score",
        "is_qualified",
    )
    list_filter = ("group__stage__season", "is_qualified")
    search_fields = ("participant__user__username", "team__name", "group__name")


@admin.register(PoolProjectionRecalc)
class PoolProjectionRecalcAdmin(admin.ModelAdmin):
    list_display = (
        "participant",
        "status",
        "requested_at",
        "last_started_at",
        "last_finished_at",
        "attempts",
    )
    list_filter = ("status", "participant__pool")
    search_fields = ("participant__user__username", "participant__pool__name")


@admin.register(PoolScoringConfig)
class PoolScoringConfigAdmin(admin.ModelAdmin):
    list_display = (
        "pool",
        "group_exact_score",
        "group_winner_and_winner_goals",
        "group_winner_and_diff",
        "group_winner_and_loser_goals",
        "group_winner_only",
        "knockout_exact_and_advancing",
        "knockout_advancing_only",
        "knockout_exact_wrong_advancing",
        "group_qualifier_points",
        "group_qualifier_position_bonus",
        "knockout_team_advancement_bonus",
    )
    search_fields = ("pool__name", "pool__slug")


@admin.register(PoolOfficialResult)
class PoolOfficialResultAdmin(admin.ModelAdmin):
    list_display = ("pool", "champion", "runner_up", "third_place", "top_scorer", "updated_at")
    search_fields = ("pool__name", "pool__slug", "champion__name", "top_scorer__name")
    filter_horizontal = ("top_scorers",)

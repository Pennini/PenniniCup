from django.contrib import admin

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
    list_display = ("name", "season", "entry_fee", "requires_payment", "is_active")
    search_fields = ("name", "slug")
    list_filter = ("is_active", "requires_payment", "season")


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
    list_display = ("bet", "points", "exact_score", "winner_or_draw", "winner_advancing", "one_team_score")


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
        "group_winner_or_draw_points",
        "group_exact_score_points",
        "group_one_team_score_points",
        "knockout_winner_advancing_points",
        "knockout_exact_score_points",
        "knockout_one_team_score_points",
    )
    search_fields = ("pool__name", "pool__slug")


@admin.register(PoolOfficialResult)
class PoolOfficialResultAdmin(admin.ModelAdmin):
    list_display = ("pool", "champion", "runner_up", "third_place", "top_scorer", "updated_at")
    search_fields = ("pool__name", "pool__slug", "champion__name", "top_scorer__name")

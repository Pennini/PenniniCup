from django.contrib import admin

from src.pool.models import Pool, PoolBet, PoolBetScore, PoolLockWindow, PoolParticipant, PoolParticipantStanding


@admin.register(Pool)
class PoolAdmin(admin.ModelAdmin):
    list_display = ("name", "season", "entry_fee", "requires_payment", "is_active")
    search_fields = ("name", "slug")
    list_filter = ("is_active", "requires_payment", "season")


@admin.register(PoolParticipant)
class PoolParticipantAdmin(admin.ModelAdmin):
    list_display = ("pool", "user", "is_active", "total_points", "group_points", "knockout_points")
    list_filter = ("pool", "is_active")
    search_fields = ("user__username", "user__email", "pool__name")


@admin.register(PoolBet)
class PoolBetAdmin(admin.ModelAdmin):
    list_display = ("participant", "match", "home_score_pred", "away_score_pred", "winner_pred", "updated_at")
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

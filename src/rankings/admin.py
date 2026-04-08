from django.contrib import admin

from src.rankings.models import RankingTieBreakOverride


@admin.register(RankingTieBreakOverride)
class RankingTieBreakOverrideAdmin(admin.ModelAdmin):
    list_display = (
        "pool",
        "participant",
        "manual_position",
        "updated_by",
        "updated_at",
    )
    list_filter = ("pool",)
    search_fields = (
        "pool__name",
        "participant__user__username",
        "participant__user__email",
    )

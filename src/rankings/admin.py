from django.contrib import admin, messages

from src.rankings.models import RankingTieBreakOverride
from src.rankings.services.history_backfill import backfill_pool_history


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


@admin.action(description="Reprocessar histórico de ranking")
def backfill_ranking_history_action(modeladmin, request, queryset):
    total_rounds = 0
    failed = []
    for pool in queryset:
        try:
            total_rounds += backfill_pool_history(pool)
        except Exception:  # noqa: BLE001 — reporta sem abortar os demais
            failed.append(pool.slug)
    if failed:
        messages.error(request, f"Falha ao reprocessar: {', '.join(failed)}")
    messages.success(request, f"Histórico reprocessado: {total_rounds} rodadas em {queryset.count()} bolão(ões).")


admin.site.add_action(backfill_ranking_history_action, "backfill_ranking_history_action")

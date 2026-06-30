from io import StringIO

from django import forms
from django.contrib import admin, messages
from django.core.management import call_command
from django.shortcuts import redirect, render
from django.urls import path

from src.football.models import Match
from src.pool.management.commands.notify_missing_bets import _current_active_phase, _get_participants_with_missing_bets
from src.pool.models import (
    Pool,
    PoolBet,
    PoolBetScore,
    PoolKnockoutPhaseScoring,
    PoolLockWindow,
    PoolOfficialResult,
    PoolParticipant,
    PoolParticipantStanding,
    PoolParticipantThirdPlace,
    PoolProjectionRecalc,
    PoolScoringConfig,
)
from src.rankings.admin import backfill_ranking_history_action


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
    actions = [backfill_ranking_history_action]

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
        "top_scorer_pred",
        "top_scorer_hit",
    )
    list_filter = ("pool", "is_active")
    search_fields = ("user__username", "user__email", "pool__name", "top_scorer_pred__name")
    list_select_related = ("pool", "user", "top_scorer_pred")


class PoolBetAdminForm(forms.ModelForm):
    class Meta:
        model = PoolBet
        fields = "__all__"

    def __init__(self, *args, allow_skip_lock=False, **kwargs):
        super().__init__(*args, **kwargs)
        # Bypassa a trava de janela/pagamento apenas quando o admin (superuser)
        # editou o palpite. Acesso ao form já é restrito a superuser (ver
        # has_*_permission), o flag é uma segunda barreira.
        if allow_skip_lock:
            self.instance._admin_skip_lock = True


@admin.register(PoolBet)
class PoolBetAdmin(admin.ModelAdmin):
    form = PoolBetAdminForm
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
    autocomplete_fields = ("participant", "match", "winner_pred")
    list_select_related = ("participant", "participant__user", "match", "winner_pred")

    # Alterar palpites de usuários é sensível: restrito a superuser ("o admin").
    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser

    def get_form(self, request, obj=None, **kwargs):
        form_class = super().get_form(request, obj, **kwargs)
        allow_skip_lock = request.user.is_superuser

        class _PoolBetAdminForm(form_class):
            def __init__(self, *args, **inner):
                inner.setdefault("allow_skip_lock", allow_skip_lock)
                super().__init__(*args, **inner)

        return _PoolBetAdminForm

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        # Atualiza projeção (standings/classificados) e recalcula a pontuação do
        # participante para refletir o palpite editado imediatamente.
        from src.pool.services.projection_queue import enqueue_projection_recalc
        from src.pool.services.ranking import recalculate_participant_scores

        enqueue_projection_recalc(obj.participant)
        recalculate_participant_scores(obj.participant)


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


def _requeue_failed_jobs(queryset):
    return queryset.filter(status=PoolProjectionRecalc.STATUS_FAILED).update(
        status=PoolProjectionRecalc.STATUS_PENDING,
        attempts=0,
    )


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
    change_list_template = "admin/pool/poolprojectionrecalc/change_list.html"
    actions = ["action_requeue_failed"]

    @admin.action(description="Reprocessar jobs FAILED selecionados")
    def action_requeue_failed(self, request, queryset):
        count = _requeue_failed_jobs(queryset)
        self.message_user(request, f"{count} job(s) recolocado(s) na fila.", messages.SUCCESS)

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "requeue-all-failed/",
                self.admin_site.admin_view(self.requeue_all_failed_view),
                name="pool_poolprojectionrecalc_requeue_all_failed",
            ),
        ]
        return custom + urls

    def requeue_all_failed_view(self, request):
        count = _requeue_failed_jobs(PoolProjectionRecalc.objects.all())
        self.message_user(request, f"{count} job(s) FAILED recolocado(s) na fila.", messages.SUCCESS)
        return redirect("..")


class PoolKnockoutPhaseScoringInline(admin.TabularInline):
    model = PoolKnockoutPhaseScoring
    extra = 0
    fields = (
        "phase_key",
        "exact",
        "advancing_goals",
        "diff",
        "loser_goals",
        "advancing_only",
        "exact_wrong_advancing",
    )


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
    inlines = [PoolKnockoutPhaseScoringInline]


@admin.register(PoolOfficialResult)
class PoolOfficialResultAdmin(admin.ModelAdmin):
    list_display = ("pool", "champion", "runner_up", "third_place", "top_scorer", "updated_at")
    search_fields = ("pool__name", "pool__slug", "champion__name", "top_scorer__name")
    filter_horizontal = ("top_scorers",)

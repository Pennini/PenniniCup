from django.contrib import admin, messages
from django.core.management import call_command
from django.http import HttpResponseRedirect
from django.urls import path, reverse

from src.football import models


@admin.register(models.Competition)
class CompetitionAdmin(admin.ModelAdmin):
    change_list_template = "admin/football/competition/change_list.html"
    list_display = ("name", "fifa_id", "gender")
    search_fields = ("name", "fifa_id")
    ordering = ("name",)

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "run-sync-cadastrais/",
                self.admin_site.admin_view(self.run_sync_cadastrais),
                name="football_competition_sync_cadastrais",
            ),
            path(
                "run-sync-frequentes/",
                self.admin_site.admin_view(self.run_sync_frequentes),
                name="football_competition_sync_frequentes",
            ),
        ]
        return custom_urls + urls

    def _run_sequence(self, request, commands: list[str], success_message: str):
        if request.method != "POST":
            messages.error(request, "Método inválido para executar sincronização.")
            return HttpResponseRedirect(reverse("admin:football_competition_changelist"))

        try:
            for command in commands:
                call_command(command)
        except Exception as exc:
            messages.error(request, f"Falha na sincronização: {exc}")
            return HttpResponseRedirect(reverse("admin:football_competition_changelist"))

        messages.success(request, success_message)
        return HttpResponseRedirect(reverse("admin:football_competition_changelist"))

    def run_sync_cadastrais(self, request):
        return self._run_sequence(
            request,
            ["sync_knockout", "sync_teams", "sync_groups", "sync_players"],
            "Sincronização cadastral executada com sucesso.",
        )

    def run_sync_frequentes(self, request):
        return self._run_sequence(
            request,
            ["sync_matches", "sync_standings"],
            "Sincronização frequente executada com sucesso.",
        )


@admin.register(models.Season)
class SeasonAdmin(admin.ModelAdmin):
    list_display = ("name", "year", "competition", "fifa_id", "start_date", "end_date")
    list_filter = ("competition", "year")
    search_fields = ("name", "fifa_id", "competition__name")
    ordering = ("-year",)
    autocomplete_fields = ("competition",)


@admin.register(models.Stage)
class StageAdmin(admin.ModelAdmin):
    list_display = ("name", "fifa_id", "season", "order", "sync_status")
    list_filter = ("season", "sync_status")
    search_fields = ("name", "fifa_id", "season__name")
    ordering = ("order", "name")
    autocomplete_fields = ("season",)


@admin.register(models.Group)
class GroupAdmin(admin.ModelAdmin):
    list_display = ("name", "fifa_id", "stage", "sync_status")
    list_filter = ("stage", "sync_status")
    search_fields = ("name", "fifa_id", "stage__name")
    ordering = ("name",)
    autocomplete_fields = ("stage",)


@admin.register(models.Team)
class TeamAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "fifa_id", "group", "confederation", "is_host", "sync_status")
    list_filter = ("confederation", "group", "is_host", "sync_status")
    search_fields = ("name", "code", "fifa_id")
    ordering = ("code",)
    autocomplete_fields = ("group",)


@admin.register(models.Player)
class PlayerAdmin(admin.ModelAdmin):
    list_display = ("name", "team", "position", "shirt_number", "fifa_id", "sync_status")
    list_filter = ("position", "sync_status")
    search_fields = ("name", "short_name", "fifa_id", "team__name")
    ordering = ("name",)
    autocomplete_fields = ("team",)


@admin.register(models.Stadium)
class StadiumAdmin(admin.ModelAdmin):
    list_display = ("name", "city", "country_code", "fifa_id", "sync_status")
    list_filter = ("country_code", "sync_status")
    search_fields = ("name", "city", "fifa_id")
    ordering = ("name",)


@admin.register(models.Match)
class MatchAdmin(admin.ModelAdmin):
    list_display = (
        "match_number",
        "season",
        "stage",
        "group",
        "home_team",
        "away_team",
        "match_date_utc",
        "status",
    )
    list_filter = ("season", "stage", "group", "status")
    search_fields = ("fifa_id", "home_placeholder", "away_placeholder")
    ordering = ("match_date_utc",)
    autocomplete_fields = ("season", "stage", "group", "stadium", "home_team", "away_team", "winner")
    list_select_related = ("season", "stage", "group", "stadium", "home_team", "away_team")
    date_hierarchy = "match_date_utc"


@admin.register(models.Standing)
class StandingAdmin(admin.ModelAdmin):
    list_display = ("season", "group", "team", "position", "points")
    list_filter = ("season", "group")
    search_fields = ("team__name", "group__name", "season__name")
    ordering = ("group", "position")
    autocomplete_fields = ("season", "group", "team")


@admin.register(models.AssignThird)
class AssignThirdAdmin(admin.ModelAdmin):
    list_display = ("season", "groups_key", "placeholder", "third_group")
    list_filter = ("season",)
    search_fields = ("groups_key", "placeholder", "third_group")
    ordering = ("season", "groups_key", "placeholder")
    autocomplete_fields = ("season",)

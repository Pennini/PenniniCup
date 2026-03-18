from django.contrib import admin

from src.football import models


@admin.register(models.Competition)
class CompetitionAdmin(admin.ModelAdmin):
    list_display = ("name", "fifa_id", "gender")
    search_fields = ("name", "fifa_id")
    ordering = ("name",)


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

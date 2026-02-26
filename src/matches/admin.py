from django.contrib import admin

from .models import GroupEntry, GroupStage, Knockout, KnockoutSlot, Match, Player, Team


class MatchAdmin(admin.ModelAdmin):
    list_display = ("home_team", "away_team", "start_time", "stage", "finished")
    list_filter = ("stage", "finished")
    search_fields = ("home_team__code", "away_team__code")


admin.site.register(Match, MatchAdmin)


class TeamAdmin(admin.ModelAdmin):
    list_display = ("code", "api_id", "api_fonte")
    search_fields = ("code",)


admin.site.register(Team, TeamAdmin)


class PlayerAdmin(admin.ModelAdmin):
    list_display = ("name", "team", "goals")
    search_fields = ("name", "team__code")


admin.site.register(Player, PlayerAdmin)


class GroupStageAdmin(admin.ModelAdmin):
    list_display = ("name",)
    search_fields = ("name",)


admin.site.register(GroupStage, GroupStageAdmin)


class GroupEntryAdmin(admin.ModelAdmin):
    list_display = ("group", "team", "points", "games", "wins", "draws", "losses", "position")
    search_fields = ("group__name", "team__code")
    list_filter = ("group",)


admin.site.register(GroupEntry, GroupEntryAdmin)


class KnockoutAdmin(admin.ModelAdmin):
    list_display = ("match", "updated_at")
    search_fields = ("match__home_team__code", "match__away_team__code")


admin.site.register(Knockout, KnockoutAdmin)


class KnockoutSlotAdmin(admin.ModelAdmin):
    list_display = ("knockout", "group", "position_in_group", "side")
    search_fields = (
        "knockout__match__home_team__code",
        "knockout__match__away_team__code",
        "group__name",
    )


admin.site.register(KnockoutSlot, KnockoutSlotAdmin)

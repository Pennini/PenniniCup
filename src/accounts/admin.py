from django.contrib import admin

from .models import InviteToken


@admin.register(InviteToken)
class InviteTokenAdmin(admin.ModelAdmin):
    list_display = ["bolao_name", "token", "created_by", "uses_count", "max_uses", "is_active", "created_at"]
    list_filter = ["is_active", "created_at"]
    search_fields = ["bolao_name", "token"]
    readonly_fields = ["token", "created_at", "uses_count"]

    fieldsets = (
        ("Informações do Bolão", {"fields": ("bolao_name", "token")}),
        ("Controle de Acesso", {"fields": ("created_by", "max_uses", "uses_count", "expires_at", "is_active")}),
        ("Datas", {"fields": ("created_at",)}),
    )

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import CustomUser, InviteToken


@admin.register(CustomUser)
class CustomUserAdmin(UserAdmin):
    """Admin customizado para CustomUser"""

    list_display = ["username", "email", "first_name", "last_name", "is_staff", "is_active"]
    list_filter = ["is_staff", "is_active", "date_joined"]
    search_fields = ["username", "email", "first_name", "last_name"]


@admin.register(InviteToken)
class InviteTokenAdmin(admin.ModelAdmin):
    list_display = ["token", "created_by", "uses_count", "max_uses", "is_active", "created_at"]
    list_filter = ["is_active", "created_at"]
    search_fields = ["token"]
    readonly_fields = ["token", "created_at", "uses_count"]

    fieldsets = (
        ("Informações do Bolão", {"fields": ("token",)}),
        ("Controle de Acesso", {"fields": ("created_by", "max_uses", "uses_count", "expires_at", "is_active")}),
        ("Datas", {"fields": ("created_at",)}),
    )

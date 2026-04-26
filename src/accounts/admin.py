from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import CustomUser, InviteToken, UserProfile


@admin.register(CustomUser)
class CustomUserAdmin(UserAdmin):
    """Admin customizado para CustomUser"""

    list_display = ["username", "email", "first_name", "last_name", "is_staff", "is_active"]
    list_filter = ["is_staff", "is_active", "date_joined"]
    search_fields = ["username", "email", "first_name", "last_name"]


@admin.register(InviteToken)
class InviteTokenAdmin(admin.ModelAdmin):
    list_display = ["token", "pool", "created_by", "uses_count", "max_uses", "is_active", "created_at"]
    list_filter = ["pool", "is_active", "created_at"]
    search_fields = ["token", "pool__name"]
    readonly_fields = ["token", "created_at", "uses_count"]

    fieldsets = (
        ("Informações do Bolão", {"fields": ("token", "pool")}),
        ("Controle de Acesso", {"fields": ("created_by", "max_uses", "uses_count", "expires_at", "is_active")}),
        ("Datas", {"fields": ("created_at",)}),
    )


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ["user", "email_verified", "favorite_team", "world_cup_team", "token_created_at"]
    list_filter = ["email_verified", "favorite_team", "world_cup_team"]
    search_fields = ["user__username", "user__email", "favorite_team", "world_cup_team__name"]
    readonly_fields = ["verification_token", "token_created_at"]

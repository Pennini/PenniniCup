from django.contrib import admin

from .models import Payment


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ["id", "user", "amount", "status", "payment_method", "created_at"]
    list_filter = ["status", "payment_method", "created_at"]
    search_fields = ["user__username", "user__email", "id"]
    readonly_fields = ["id", "created_at", "updated_at", "mp_payment_id"]

    fieldsets = (
        ("Informações do Pagamento", {"fields": ("id", "user", "amount", "status", "payment_method")}),
        ("Mercado Pago", {"fields": ("mp_payment_id",)}),
        ("Datas", {"fields": ("created_at", "updated_at")}),
    )

    def has_add_permission(self, request):
        # Impede a adição manual de pagamentos via admin
        return False

    # def has_delete_permission(self, request, obj=None):
    #     # Impede a exclusão manual de pagamentos via admin
    #     return False

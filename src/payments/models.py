# payments/models.py
from django.conf import settings
from django.db import models

User = settings.AUTH_USER_MODEL


class Payment(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    pool = models.ForeignKey("pool.Pool", on_delete=models.SET_NULL, null=True, blank=True, related_name="payments")

    mp_payment_id = models.CharField(max_length=100, blank=True, null=True)
    status = models.CharField(max_length=50)
    payment_method = models.CharField(max_length=50, blank=True)

    amount = models.DecimalField(max_digits=12, decimal_places=2)
    amount_received = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def is_paid(self):
        return self.status == "approved"

    def __str__(self):
        return f"{self.user} - {self.status}"


class WebhookEvent(models.Model):
    provider = models.CharField(max_length=32, default="mercadopago")
    idempotency_key = models.CharField(max_length=64, unique=True, db_index=True)
    event_type = models.CharField(max_length=64, blank=True)
    action = models.CharField(max_length=64, blank=True)
    external_id = models.CharField(max_length=128, blank=True)
    processed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-processed_at"]

    def __str__(self):
        return f"{self.provider}:{self.idempotency_key}"

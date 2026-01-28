# payments/models.py
from django.conf import settings
from django.db import models

User = settings.AUTH_USER_MODEL


class Payment(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    # bolao = models.ForeignKey("bolao.Bolao", on_delete=models.CASCADE)

    mp_payment_id = models.CharField(max_length=100, blank=True, null=True)
    status = models.CharField(max_length=50)
    payment_method = models.CharField(max_length=50, blank=True)

    amount = models.DecimalField(max_digits=8, decimal_places=2)
    amount_received = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def is_paid(self):
        return self.status == "approved"

    def __str__(self):
        return f"{self.user} - {self.status}"

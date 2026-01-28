from rest_framework import serializers

from .models import Payment


class PaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payment
        fields = [
            "id",
            "user",
            "mp_payment_id",
            "status",
            "payment_method",
            "amount",
            "amount_received",
            "created_at",
            "updated_at",
        ]

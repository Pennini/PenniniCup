# payments/services/mercadopago.py
from datetime import timedelta

import mercadopago
import requests
from django.conf import settings

MP_URL = "https://api.mercadopago.com/v1/payments"
sdk = mercadopago.SDK(settings.MERCADO_PAGO_TOKEN)


def create_pix_payment(payment, notification_url):
    payload = {
        "transaction_amount": float(payment.amount),
        "date_of_expiration": (payment.created_at + timedelta(hours=2)).isoformat(),
        "payment_method_id": "pix",
        "external_reference": str(payment.id),
        "description": "Entrada no bolão",  # {payment.bolao.name}",
        "notification_url": notification_url,
        "payer": {
            "email": payment.user.email,
        },
    }

    headers = {
        "Authorization": f"Bearer {settings.MERCADO_PAGO_TOKEN}",
        "Content-Type": "application/json",
        "X-Idempotency-Key": str(payment.id),
    }

    response = requests.post(MP_URL, json=payload, headers=headers)
    response.raise_for_status()

    return response.json()

import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

MP_URL = "https://api.mercadopago.com/v1/orders/"


def create_pix_payment(payment) -> dict | None:
    try:
        payload = {
            "type": "online",
            "external_reference": f"payment_{payment.id}",
            "total_amount": str(payment.amount),
            "payer": {"email": payment.user.email, "first_name": "APRO" if settings.DEBUG else payment.user.username},
            "transactions": {
                "payments": [{"amount": str(payment.amount), "payment_method": {"id": "pix", "type": "bank_transfer"}}]
            },
        }

        headers = {
            "Authorization": f"Bearer {settings.MERCADO_PAGO_ACCESS_TOKEN}",
            "Content-Type": "application/json",
            "X-Idempotency-Key": str(payment.id),
        }

        logger.info(f"Criando PIX (Orders) payment_id={payment.id} user={payment.user.email}")

        response = requests.post(MP_URL, json=payload, headers=headers, timeout=15)

        if response.status_code not in (200, 201):
            logger.error(f"Erro Mercado Pago: {response.status_code} - {response.text}")
            raise Exception(response.text)

        data = response.json()

        logger.info(f"PIX criado com sucesso | order_id={data.get('id')} status={data.get('status')}")

        return data

    except Exception as e:
        logger.exception(f"Erro inesperado ao criar pagamento PIX: {str(e)}")
        return None


def get_order_status(order_id: str) -> dict | None:
    headers = {
        "Authorization": f"Bearer {settings.MERCADO_PAGO_ACCESS_TOKEN}",
    }

    response = requests.get(f"{MP_URL}{order_id}", headers=headers, timeout=10)

    if response.status_code != 200:
        return None

    return response.json()

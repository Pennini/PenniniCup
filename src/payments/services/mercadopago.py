import logging
import uuid

import mercadopago
from django.conf import settings

logger = logging.getLogger(__name__)

_sdk = None


def _get_sdk() -> mercadopago.SDK:
    global _sdk
    if _sdk is None:
        _sdk = mercadopago.SDK(settings.MERCADO_PAGO_ACCESS_TOKEN)
    return _sdk


def create_pix_payment(payment) -> dict | None:
    """
    Cria um pagamento PIX usando a API /v1/payments (Checkout Bricks backend-safe)

    IMPORTANTE:
    - NÃO usar email de usuário de teste
    - NÃO usar first_name APRO
    """

    try:
        payload = {
            "transaction_amount": float(payment.amount),
            "description": "Inscrição no Bolão PenniniCup",
            "payment_method_id": "pix",
            "external_reference": f"payment_{payment.id}",
            "payer": {
                # Email REAL do usuário (não pode ser test_user)
                "email": payment.user.email,
            },
        }

        request_options = mercadopago.config.RequestOptions()
        # UUID garante unicidade mesmo em retentativas de um mesmo payment
        request_options.custom_headers = {"X-Idempotency-Key": f"payment-{payment.id}-{uuid.uuid4()}"}

        logger.info("Criando pagamento PIX | payment_id=%s | user_id=%s", payment.id, payment.user.id)

        response = _get_sdk().payment().create(payload, request_options)

        status = response.get("status")
        body = response.get("response", {})

        if status not in (200, 201):
            logger.error("Erro Mercado Pago | status=%s | body=%s", status, body)
            raise Exception(body.get("message", "Erro desconhecido no Mercado Pago"))

        logger.info("PIX criado com sucesso | mp_payment_id=%s | status=%s", body.get("id"), body.get("status"))

        return body

    except Exception:
        logger.exception("Erro ao criar pagamento PIX")
        return None


def get_payment_status(payment_id: str) -> dict | None:
    try:
        payment_response = _get_sdk().payment().get(payment_id)

        if payment_response.get("status") != 200:
            logger.error("Erro ao buscar status do pagamento: status=%s", payment_response.get("status"))
            return None

        return payment_response.get("response", {})

    except Exception:
        logger.exception("Erro ao buscar status do pagamento: payment_id=%s", payment_id)
        return None

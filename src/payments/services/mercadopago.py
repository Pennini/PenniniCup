import logging
import uuid

import mercadopago
from django.conf import settings

logger = logging.getLogger(__name__)

sdk = mercadopago.SDK(settings.MERCADO_PAGO_ACCESS_TOKEN)


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

        logger.info(f"Criando pagamento PIX | payment_id={payment.id} | user={payment.user.email}")

        response = sdk.payment().create(payload, request_options)

        status = response.get("status")
        body = response.get("response", {})

        if status not in (200, 201):
            logger.error(f"Erro Mercado Pago | status={status} | body={body}")
            raise Exception(body.get("message", "Erro desconhecido no Mercado Pago"))

        logger.info(f"PIX criado com sucesso | mp_payment_id={body.get('id')} | status={body.get('status')}")

        return body

    except Exception as e:
        logger.exception(f"Erro ao criar pagamento PIX: {str(e)}")
        return None


def get_payment_status(payment_id: str) -> dict | None:
    try:
        payment_response = sdk.payment().get(payment_id)

        if payment_response.get("status") != 200:
            logger.error(f"Erro ao buscar status do pagamento: {payment_response.get('status')}")
            return None

        return payment_response.get("response", {})

    except Exception as e:
        logger.exception(f"Erro ao buscar status do pagamento {payment_id}: {str(e)}")
        return None

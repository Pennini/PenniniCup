# payments/services/mercadopago.py
import logging
from datetime import timedelta

import mercadopago
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

logger = logging.getLogger(__name__)

MP_URL = "https://api.mercadopago.com/v1/payments"

# Validação das configurações
if not settings.MERCADO_PAGO_ACCESS_TOKEN:
    raise ImproperlyConfigured(
        "MERCADO_PAGO_ACCESS_TOKEN não configurado. "
        "Adicione a variável de ambiente PENNINIBET_MERCADO_PAGO_ACCESS_TOKEN"
    )

sdk = mercadopago.SDK(settings.MERCADO_PAGO_ACCESS_TOKEN)


def create_pix_payment(payment, notification_url: str) -> dict:
    """
    Cria um pagamento PIX no Mercado Pago

    Args:
        payment: Instância do modelo Payment
        notification_url: URL para receber notificações de webhook

    Returns:
        Dict com os dados da resposta do Mercado Pago

    Raises:
        Exception: Se houver erro na criação do pagamento
    """
    try:
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

        # Configuração de idempotência para evitar pagamentos duplicados
        request_options = mercadopago.config.RequestOptions()
        headers = {
            "X-Idempotency-Key": str(payment.id),
        }
        request_options.custom_headers = headers

        logger.info(
            f"Criando pagamento PIX para payment_id={payment.id}, user={payment.user.email}, amount={payment.amount}"
        )

        payment_response = sdk.payment().create(payload, request_options)

        # Verificar se houve erro na resposta
        if payment_response.get("status") not in [200, 201]:
            error_message = payment_response.get("response", {}).get("message", "Erro desconhecido")
            status_code = payment_response.get("status")

            # Mensagens específicas para erros comuns
            if status_code == 401 or status_code == 403:
                error_message = (
                    "Token do Mercado Pago inválido ou sem permissões. "
                    "Verifique MERCADO_PAGO_ACCESS_TOKEN no arquivo .env"
                )

            logger.error(f"Erro ao criar pagamento PIX: status={status_code}, message={error_message}")
            raise Exception(f"Erro do Mercado Pago: {error_message}")

        payment_data = payment_response["response"]

        logger.info(
            f"Pagamento PIX criado com sucesso: payment_id={payment.id}, "
            f"mp_payment_id={payment_data.get('id')}, status={payment_data.get('status')}"
        )

        return payment_data
    except Exception as e:
        logger.exception(f"Erro inesperado ao criar pagamento PIX: {str(e)}")


def get_payment_status(mp_payment_id: str) -> dict | None:
    """
    Consulta o status de um pagamento no Mercado Pago

    Args:
        mp_payment_id: ID do pagamento no Mercado Pago

    Returns:
        Dict com os dados do pagamento ou None se não encontrado
    """
    try:
        logger.info(f"Consultando status do pagamento mp_payment_id={mp_payment_id}")

        payment_response = sdk.payment().get(mp_payment_id)

        if payment_response.get("status") != 200:
            logger.warning(
                f"Pagamento não encontrado: mp_payment_id={mp_payment_id}, status={payment_response.get('status')}"
            )
            return None

        return payment_response["response"]

    except Exception as e:
        logger.exception(f"Erro ao consultar pagamento {mp_payment_id}: {str(e)}")
        return None

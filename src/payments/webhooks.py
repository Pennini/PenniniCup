import hashlib
import hmac
import json
import logging

from django.conf import settings
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .models import Payment
from .services.mercadopago import get_order_status

logger = logging.getLogger(__name__)


def verify_webhook_signature(request) -> bool:
    """
    Verifica a assinatura do webhook do Mercado Pago para garantir autenticidade

    Documentação: https://www.mercadopago.com.br/developers/pt/docs/your-integrations/notifications/webhooks
    """
    if not settings.MERCADO_PAGO_WEBHOOK_SECRET:
        logger.warning("MERCADO_PAGO_WEBHOOK_SECRET não configurado - webhook sem validação")
        return True  # Em desenvolvimento, permite sem validação

    try:
        # Obtém os headers necessários
        x_signature = request.headers.get("x-signature")
        x_request_id = request.headers.get("x-request-id")

        if not x_signature or not x_request_id:
            logger.warning("Headers de assinatura ausentes no webhook")
            return False

        # Extrai ts e hash da assinatura
        # Formato: ts=1234567890,v1=hash_value
        parts = {}
        for part in x_signature.split(","):
            key, value = part.split("=", 1)
            parts[key.strip()] = value.strip()

        ts = parts.get("ts")
        received_hash = parts.get("v1")

        if not ts or not received_hash:
            logger.warning("Formato de assinatura inválido")
            return False

        # Reconstrói a string que deve ser validada
        # Formato: id:<request_id>;request-id:<request_id>;ts:<timestamp>;
        data_id = request.GET.get("data.id", "")
        manifest = f"id:{data_id};request-id:{x_request_id};ts:{ts};"

        # Calcula o HMAC
        secret_bytes = settings.MERCADO_PAGO_WEBHOOK_SECRET.encode()
        manifest_bytes = manifest.encode()
        expected_hash = hmac.new(secret_bytes, manifest_bytes, hashlib.sha256).hexdigest()

        # Compara de forma segura
        is_valid = hmac.compare_digest(expected_hash, received_hash)

        if not is_valid:
            logger.warning(f"Assinatura do webhook inválida. Expected: {expected_hash}, Received: {received_hash}")

        return is_valid

    except Exception as e:
        logger.exception(f"Erro ao verificar assinatura do webhook: {str(e)}")
        return False


@csrf_exempt
@require_POST
def mercado_pago_webhook(request):
    """
    Webhook para receber notificações do Mercado Pago

    Tipos de notificação:
    - payment: Atualização de status de pagamento
    - merchant_order: Atualização de pedido
    """
    try:
        # # Verifica a assinatura do webhook
        # if not verify_webhook_signature(request):
        #     logger.error("Webhook com assinatura inválida - rejeitado")
        #     return HttpResponse("Unauthorized", status=401)

        # Parse do body
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            logger.error("Body do webhook não é JSON válido")
            return HttpResponse("Invalid JSON", status=400)

        # Log da notificação recebida
        logger.info(f"Webhook recebido: type={data.get('type')}, action={data.get('action')}")

        # Processa apenas notificações de pagamento
        if data.get("type") != "payment":
            logger.info(f"Tipo de notificação ignorado: {data.get('type')}")
            return HttpResponse(status=200)

        # Obtém o ID do pagamento
        mp_payment_id = data.get("data", {}).get("id")

        if not mp_payment_id:
            logger.error("Webhook sem payment ID")
            return HttpResponse("Missing payment ID", status=400)

        # Busca informações atualizadas diretamente da API do Mercado Pago
        # Isso é mais seguro do que confiar apenas nos dados do webhook
        payment_data = get_order_status(str(mp_payment_id))

        if not payment_data:
            logger.error(f"Pagamento não encontrado na API: mp_payment_id={mp_payment_id}")
            return HttpResponse("Payment not found", status=404)

        # Atualiza o pagamento no banco de dados
        payment = Payment.objects.filter(mp_payment_id=str(mp_payment_id)).first()

        if not payment:
            logger.warning(f"Pagamento não encontrado no banco: mp_payment_id={mp_payment_id}")
            return HttpResponse("Payment not found in database", status=404)

        # Atualiza status e informações
        old_status = payment.status
        payment.status = payment_data.get("status", "unknown")
        payment.payment_method = payment_data.get("payment_method_id", "")

        # Atualiza valor recebido se pagamento aprovado
        if payment.status == "approved":
            payment.amount_received = payment_data.get("transaction_amount")

        payment.save()

        logger.info(
            f"Pagamento atualizado: payment_id={payment.id}, "
            f"mp_payment_id={mp_payment_id}, "
            f"status: {old_status} -> {payment.status}"
        )

        return HttpResponse(status=200)

    except Exception as e:
        logger.exception(f"Erro ao processar webhook: {str(e)}")
        return HttpResponse("Internal server error", status=500)

import hashlib
import hmac
import json
import logging

from django.conf import settings
from django.db import IntegrityError, transaction
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django_ratelimit.decorators import ratelimit

from .models import Payment, WebhookEvent
from .services.mercadopago import get_payment_status

logger = logging.getLogger(__name__)


def _build_idempotency_key(request, data: dict) -> str:
    request_id = request.headers.get("x-request-id", "")
    event_type = data.get("type", "")
    action = data.get("action", "")
    external_id = str(data.get("data", {}).get("id", ""))
    fingerprint = f"{request_id}|{event_type}|{action}|{external_id}"
    if not any((request_id, event_type, action, external_id)):
        fingerprint = request.body.decode("utf-8", errors="ignore")
    return hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()


def verify_webhook_signature(request) -> bool:
    """
    Verifica a assinatura do webhook do Mercado Pago para garantir autenticidade

    Documentação: https://www.mercadopago.com.br/developers/pt/docs/your-integrations/notifications/webhooks
    """
    if not settings.MERCADO_PAGO_WEBHOOK_SECRET:
        if settings.DEBUG:
            logger.warning("MERCADO_PAGO_WEBHOOK_SECRET não configurado em DEBUG")
            return True
        logger.error("MERCADO_PAGO_WEBHOOK_SECRET ausente em produção")
        return False

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
            logger.warning("Assinatura do webhook inválida. request_id=%s", x_request_id)

        return is_valid

    except Exception:
        logger.exception("Erro ao verificar assinatura do webhook")
        return False


@csrf_exempt
@require_POST
@ratelimit(key="ip", rate="60/m", method="POST", block=False)
def mercado_pago_webhook(request):
    """
    Webhook para receber notificações do Mercado Pago

    Tipos de notificação:
    - payment: Atualização de status de pagamento
    - merchant_order: Atualização de pedido
    """
    try:
        if getattr(request, "limited", False):
            logger.warning("Webhook rate limited por IP")
            return HttpResponse("Too Many Requests", status=429)

        # Verifica a assinatura do webhook
        if not verify_webhook_signature(request):
            logger.error("Webhook com assinatura inválida - rejeitado")
            return HttpResponse("Unauthorized", status=401)

        # Parse do body
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            logger.error("Body do webhook não é JSON válido")
            return HttpResponse("Invalid JSON", status=400)

        # Log da notificação recebida
        logger.info("Webhook recebido: type=%s action=%s", data.get("type"), data.get("action"))

        # Processa apenas notificações de pagamento
        if data.get("type") != "payment":
            logger.info("Tipo de notificação ignorado: type=%s", data.get("type"))
            return HttpResponse(status=200)

        # Obtém o ID do pagamento
        mp_payment_id = data.get("data", {}).get("id")

        if not mp_payment_id:
            logger.error("Webhook sem payment ID")
            return HttpResponse("Missing payment ID", status=400)

        # Busca informações atualizadas diretamente da API do Mercado Pago
        # Isso é mais seguro do que confiar apenas nos dados do webhook
        payment_data = get_payment_status(str(mp_payment_id))

        if not payment_data:
            logger.error("Pagamento não encontrado na API: mp_payment_id=%s", mp_payment_id)
            return HttpResponse("Payment not found", status=404)

        event_key = _build_idempotency_key(request, data)

        # Atualiza o pagamento no banco de dados com lock para evitar corrida em reentregas.
        with transaction.atomic():
            try:
                WebhookEvent.objects.create(
                    provider="mercadopago",
                    idempotency_key=event_key,
                    event_type=str(data.get("type", "")),
                    action=str(data.get("action", "")),
                    external_id=str(mp_payment_id),
                )
            except IntegrityError:
                logger.info("Webhook duplicado ignorado por idempotency_key: %s", event_key)
                return HttpResponse(status=200)

            payment = Payment.objects.select_for_update().filter(mp_payment_id=str(mp_payment_id)).first()
            if not payment:
                return HttpResponse("Payment not found in database", status=404)

            old_status = payment.status
            new_status = payment_data.get("status", "unknown")

            # Estado terminal: uma vez aprovado, não deve regredir por eventos fora de ordem.
            if old_status == "approved":
                if new_status != "approved":
                    logger.info(
                        (
                            "Webhook fora de ordem ignorado: payment_id=%s mp_payment_id=%s "
                            "status_atual=%s status_recebido=%s"
                        ),
                        payment.id,
                        mp_payment_id,
                        old_status,
                        new_status,
                    )
                return HttpResponse(status=200)

            payment.status = new_status
            payment.payment_method = payment_data.get("payment_method_id", "")

            if payment.status == "approved":
                payment.amount_received = payment_data.get("transaction_amount")

            payment.save()

        logger.info(
            "Pagamento atualizado: payment_id=%s mp_payment_id=%s status:%s->%s",
            payment.id,
            mp_payment_id,
            old_status,
            payment.status,
        )

        return HttpResponse(status=200)

    except Exception:
        logger.exception("Erro ao processar webhook")
        return HttpResponse("Internal server error", status=500)

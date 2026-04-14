import logging
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from src.pool.models import Pool

from .models import Payment
from .services.mercadopago import create_pix_payment, get_payment_status

logger = logging.getLogger(__name__)


@login_required
@require_http_methods(["POST"])
def create_subscription_payment(request):
    """
    Cria um pagamento de inscrição e redireciona para a página de pagamento PIX.
    Chamado quando o usuário clica em "Pagar inscrição".
    """
    try:
        # Valor da inscrição deve vir no POST.
        raw_amount = (request.POST.get("amount") or "").strip()
        pool_id = request.POST.get("pool_id")
        pool = None

        if pool_id:
            pool = get_object_or_404(Pool, id=pool_id, is_active=True)

        if not raw_amount:
            logger.error("Valor inválido: amount ausente")
            return render(request, "payments/payment_failed.html", {"error": "Valor de inscrição inválido."})

        try:
            normalized_amount = raw_amount.replace(" ", "")
            if "," in normalized_amount:
                # Formato pt-BR: remove separador de milhar e troca vírgula por ponto decimal.
                normalized_amount = normalized_amount.replace(".", "").replace(",", ".")

            amount = Decimal(normalized_amount)
            if amount <= 0:
                raise ValueError("Valor deve ser positivo")
        except (InvalidOperation, ValueError, TypeError):
            logger.error("Valor inválido: amount=%s", raw_amount)
            return render(request, "payments/payment_failed.html", {"error": "Valor de inscrição inválido."})

        try:
            with transaction.atomic():
                payment = Payment.objects.create(
                    user=request.user,
                    pool=pool,
                    amount=amount,
                    status="pending",
                    payment_method="pix",
                )

                logger.info(
                    "Pagamento criado: payment_id=%s user_id=%s amount=%s",
                    payment.id,
                    request.user.id,
                    amount,
                )

                mp_payment_data = create_pix_payment(payment)
                if not mp_payment_data:
                    raise Exception("Falha ao criar pagamento no Mercado Pago")

                payment.mp_payment_id = str(mp_payment_data.get("id"))
                payment.status = mp_payment_data.get("status", "pending")
                payment.save()

            logger.info(
                "Pagamento PIX criado no MP: payment_id=%s mp_payment_id=%s", payment.id, payment.mp_payment_id
            )

            return redirect("payments:pix-payment", payment_id=payment.id)

        except Exception:
            logger.exception("Erro ao criar pagamento no MP")
            return render(
                request,
                "payments/payment_failed.html",
                {"error": "Erro ao processar pagamento. Tente novamente mais tarde."},
            )

    except Exception:
        logger.exception("Erro inesperado ao criar pagamento")
        return render(request, "payments/payment_failed.html", {"error": "Erro interno. Tente novamente."})


@login_required
@require_http_methods(["GET"])
def pix_payment_view(request, payment_id):
    """View para exibir página de pagamento PIX"""
    payment = get_object_or_404(Payment, id=payment_id, user=request.user)

    # Se já foi pago, redireciona
    if payment.is_paid():
        return redirect("payments:payment-success", payment_id=payment.id)

    # Busca os dados do pagamento no Mercado Pago
    mp_data = None
    if payment.mp_payment_id:
        mp_data = get_payment_status(payment.mp_payment_id)
        if not mp_data:
            logger.error("Não foi possível buscar dados do pagamento MP: payment_id=%s", payment.id)
    if not mp_data:
        messages.warning(request, "Não foi possível confirmar o status do PIX agora. Tente novamente em instantes.")
        return redirect("payments:payment-pending", payment_id=payment.id)

    context = {
        "payment": payment,
        "mp_data": mp_data,
        "public_key": settings.PIX_KEY,
        "amount": float(payment.amount),
        "payer_email": request.user.email,
        "debug": settings.DEBUG,
    }

    logger.debug(
        "Exibindo página PIX: payment_id=%s mp_payment_id=%s transaction_amount=%s",
        payment.id,
        mp_data.get("id"),
        mp_data.get("transaction_amount"),
    )

    return render(request, "payments/pix_payment.html", context)


@login_required
@require_http_methods(["GET"])
def payment_success_view(request, payment_id):
    """View para exibir página de pagamento bem-sucedido"""
    payment = get_object_or_404(Payment, id=payment_id, user=request.user)

    context = {
        "payment": payment,
    }

    return render(request, "payments/payment_success.html", context)


@login_required
@require_http_methods(["GET"])
def payment_pending_view(request, payment_id):
    """View para exibir página de pagamento pendente"""
    payment = get_object_or_404(Payment, id=payment_id, user=request.user)

    context = {
        "payment": payment,
    }

    return render(request, "payments/payment_pending.html", context)

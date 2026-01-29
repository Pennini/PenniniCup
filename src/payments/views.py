import logging

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

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
        # Valor da inscrição (pode vir do POST ou ser fixo)
        amount = request.POST.get("amount", "50.00")  # Valor padrão: R$ 50,00

        try:
            amount = float(amount)
            if amount <= 0:
                raise ValueError("Valor deve ser positivo")
        except (ValueError, TypeError):
            logger.error(f"Valor inválido: {amount}")
            return render(request, "payments/payment_failed.html", {"error": "Valor de inscrição inválido."})

        # Cria o pagamento no banco de dados
        payment = Payment.objects.create(user=request.user, amount=amount, status="pending", payment_method="pix")

        logger.info(f"Pagamento criado: id={payment.id}, user={request.user.email}, amount={amount}")

        # Cria o pagamento no Mercado Pago
        try:
            mp_payment_data = create_pix_payment(payment)

            # Verifica se a criação foi bem-sucedida
            if not mp_payment_data:
                raise Exception("Falha ao criar pagamento no Mercado Pago")

            # Atualiza o pagamento com os dados do MP
            payment.mp_payment_id = str(mp_payment_data.get("id"))
            payment.status = mp_payment_data.get("status", "pending")
            payment.save()

            logger.info(f"Pagamento PIX criado no MP: payment_id={payment.id}, mp_payment_id={payment.mp_payment_id}")

            # Redireciona para a página de pagamento
            return redirect("payments:pix-payment", payment_id=payment.id)

        except Exception as e:
            # Remove o pagamento se falhou no MP
            payment.delete()
            logger.error(f"Erro ao criar pagamento no MP: {str(e)}")
            return render(
                request,
                "payments/payment_failed.html",
                {"error": "Erro ao processar pagamento. Tente novamente mais tarde."},
            )

    except Exception as e:
        logger.exception(f"Erro inesperado ao criar pagamento: {str(e)}")
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
            logger.error(f"Não foi possível buscar dados do pagamento MP: {payment.mp_payment_id}")

    context = {
        "payment": payment,
        "mp_data": mp_data,
        "public_key": settings.PIX_KEY,
        "amount": float(payment.amount),
        "payer_email": request.user.email,
        "debug": settings.DEBUG,
    }

    logger.info(f"""
        Exibindo página PIX com esses dados: {mp_data["id"]} |
        Amount {mp_data["transaction_amount"]} |
        url_sandbox={mp_data["point_of_interaction"]["transaction_data"]["ticket_url"]}
    """)

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

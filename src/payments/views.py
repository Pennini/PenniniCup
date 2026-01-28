import logging

import mercadopago
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods
from rest_framework import status
from rest_framework.generics import ListAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Payment
from .serializers import PaymentSerializer
from .services.mercadopago import MercadoPagoError, create_pix_payment

logger = logging.getLogger(__name__)

sdk = mercadopago.SDK(settings.MERCADO_PAGO_ACCESS_TOKEN)


class PaymentListView(ListAPIView):
    """Lista todos os pagamentos (com autenticação)"""

    serializer_class = PaymentSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        # Usuários veem apenas seus próprios pagamentos
        # Staff pode ver todos
        if self.request.user.is_staff:
            return Payment.objects.all()
        return Payment.objects.filter(user=self.request.user)


class CreatePaymentIntentView(APIView):
    """Cria uma intenção de pagamento PIX"""

    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        try:
            amount = request.data.get("amount")

            if not amount:
                return Response({"error": "Amount is required."}, status=status.HTTP_400_BAD_REQUEST)

            # Valida o valor
            try:
                amount = float(amount)
                if amount <= 0:
                    raise ValueError("Amount must be positive")
            except (ValueError, TypeError):
                return Response({"error": "Invalid amount."}, status=status.HTTP_400_BAD_REQUEST)

            # Cria o pagamento no banco de dados
            payment = Payment.objects.create(user=request.user, amount=amount, status="pending", payment_method="pix")

            # Gera URL de notificação (webhook)
            notification_url = request.build_absolute_uri(reverse("payments:webhook"))

            # Cria o pagamento no Mercado Pago
            try:
                mp_payment_data = create_pix_payment(payment, notification_url)

                # Atualiza o pagamento com os dados do MP
                payment.mp_payment_id = str(mp_payment_data.get("id"))
                payment.status = mp_payment_data.get("status", "pending")
                payment.save()

                # Retorna dados para o frontend
                response_data = {
                    "payment_id": payment.id,
                    "mp_payment_id": payment.mp_payment_id,
                    "status": payment.status,
                    "qr_code": mp_payment_data.get("point_of_interaction", {})
                    .get("transaction_data", {})
                    .get("qr_code"),
                    "qr_code_base64": mp_payment_data.get("point_of_interaction", {})
                    .get("transaction_data", {})
                    .get("qr_code_base64"),
                    "ticket_url": mp_payment_data.get("point_of_interaction", {})
                    .get("transaction_data", {})
                    .get("ticket_url"),
                }

                logger.info(f"Pagamento criado com sucesso: payment_id={payment.id}, user={request.user.email}")

                return Response(response_data, status=status.HTTP_201_CREATED)

            except MercadoPagoError as e:
                # Remove o pagamento se falhou no MP
                payment.delete()
                logger.error(f"Erro ao criar pagamento no MP: {str(e)}")
                return Response(
                    {"error": "Erro ao processar pagamento. Tente novamente."}, status=status.HTTP_502_BAD_GATEWAY
                )

        except Exception as e:
            logger.exception(f"Erro inesperado ao criar pagamento: {str(e)}")
            return Response({"error": "Erro interno do servidor."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


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

        # Gera URL de notificação (webhook)
        notification_url = request.build_absolute_uri(reverse("payments:webhook"))

        # Cria o pagamento no Mercado Pago
        try:
            mp_payment_data = create_pix_payment(payment, notification_url)

            # Atualiza o pagamento com os dados do MP
            payment.mp_payment_id = str(mp_payment_data.get("id"))
            payment.status = mp_payment_data.get("status", "pending")
            payment.save()

            logger.info(f"Pagamento PIX criado no MP: payment_id={payment.id}, mp_payment_id={payment.mp_payment_id}")

            # Redireciona para a página de pagamento
            return redirect("payments:pix-payment", payment_id=payment.id)

        except MercadoPagoError as e:
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

    context = {
        "payment": payment,
        "public_key": settings.MERCADO_PAGO_PUBLIC_KEY,
        "amount": float(payment.amount),
        "payer_email": request.user.email,
    }

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

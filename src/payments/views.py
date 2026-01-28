import uuid

from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from .models import Payment
from .services.mercadopago import create_pix_payment


@login_required
def start_payment(request):
    if request.method != "POST":
        return redirect("payments-start")

    user = request.user

    payment = Payment.objects.create(user=user, amount=50.00, status="pending", mp_payment_id=str(uuid.uuid4()))

    url_auth = request.build_absolute_uri("/payments/webhook/")
    mp_response = create_pix_payment(payment, url_auth)

    payment.mp_payment_id = mp_response["id"]
    payment.save()

    pix_data = mp_response["point_of_interaction"]["transaction_data"]

    return render(
        request,
        "payments/pix_payment.html",
        {
            "payment": payment,
            "qr_code": pix_data["qr_code"],
            "qr_code_base64": pix_data["qr_code_base64"],
        },
    )


@login_required
def payment_status(request, payment_id):
    payment = Payment.objects.get(id=payment_id, user=request.user)

    if payment.status == "approved":
        return render(request, "payments/payment_success.html")

    if payment.status == "rejected":
        return render(request, "payments/payment_error.html")

    return render(request, "payments/payment_pending.html", {"payment": payment})

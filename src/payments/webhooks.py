import json

from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt

from .models import Payment


@csrf_exempt
def mercado_pago_webhook(request):
    data = json.loads(request.body)

    mp_payment_id = data.get("data", {}).get("id")
    status = data.get("action")

    if not mp_payment_id:
        return HttpResponse(status=400)

    payment = Payment.objects.filter(mp_payment_id=mp_payment_id).first()
    if payment:
        payment.status = status
        payment.save()

    return HttpResponse(status=200)

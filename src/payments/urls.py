from django.urls import path

from . import views, webhooks

app_name = "payments"

urlpatterns = [
    # API endpoints
    path("api/payment-list/", views.PaymentListView.as_view(), name="payment-list"),
    path("api/create-payment/", views.CreatePaymentIntentView.as_view(), name="create-payment"),
    # Webhook
    path("webhook/mercadopago/", webhooks.mercado_pago_webhook, name="webhook"),
    # Views HTML
    path("create-subscription/", views.create_subscription_payment, name="create-subscription"),
    path("pix/<int:payment_id>/", views.pix_payment_view, name="pix-payment"),
    path("success/<int:payment_id>/", views.payment_success_view, name="payment-success"),
    path("pending/<int:payment_id>/", views.payment_pending_view, name="payment-pending"),
]

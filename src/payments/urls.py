from django.urls import path

from . import views

app_name = "payments"

urlpatterns = [
    path("start/", views.start_payment, name="start-payment"),
    path("status/<int:payment_id>/", views.payment_status, name="payment-status"),
]

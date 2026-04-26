import hashlib
import hmac
import json
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from src.football.models import Competition, Season
from src.payments.models import Payment, WebhookEvent
from src.payments.services.mercadopago import create_pix_payment, get_payment_status
from src.pool.models import Pool

User = get_user_model()


class PaymentsBaseTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="pay-user", email="pay@example.com", password="123456Aa!")
        self.other_user = User.objects.create_user(
            username="other-user", email="other@example.com", password="123456Aa!"
        )
        competition = Competition.objects.create(fifa_id=5100, name="Copa Payment")
        season = Season.objects.create(
            fifa_id=5100,
            competition=competition,
            name="Temporada Payment",
            year=2026,
            start_date="2026-06-01",
            end_date="2026-07-30",
        )
        self.pool = Pool.objects.create(name="Pool Payment", slug="pool-payment", season=season, created_by=self.user)

    @staticmethod
    def build_signature_headers(*, data_id: str, request_id: str = "req-abc-123", ts: str = "1710000000"):
        manifest = f"id:{data_id};request-id:{request_id};ts:{ts};"
        expected_hash = hmac.new(
            key=b"secret123",
            msg=manifest.encode(),
            digestmod=hashlib.sha256,
        ).hexdigest()
        return {
            "HTTP_X_SIGNATURE": f"ts={ts},v1={expected_hash}",
            "HTTP_X_REQUEST_ID": request_id,
            "CONTENT_TYPE": "application/json",
        }


class PaymentModelTest(PaymentsBaseTestCase):
    def test_is_paid_returns_true_only_for_approved(self):
        approved = Payment.objects.create(
            user=self.user,
            pool=self.pool,
            status="approved",
            payment_method="pix",
            amount=Decimal("100.00"),
        )
        pending = Payment.objects.create(
            user=self.user,
            pool=self.pool,
            status="pending",
            payment_method="pix",
            amount=Decimal("100.00"),
        )
        self.assertTrue(approved.is_paid())
        self.assertFalse(pending.is_paid())


class MercadoPagoServiceTest(PaymentsBaseTestCase):
    @patch("src.payments.services.mercadopago.uuid.uuid4", return_value="abc-uuid")
    @patch("src.payments.services.mercadopago.sdk")
    def test_create_pix_payment_success(self, sdk_mock, _uuid_mock):
        payment = Payment.objects.create(
            user=self.user,
            pool=self.pool,
            status="pending",
            payment_method="pix",
            amount=Decimal("123.45"),
        )
        sdk_mock.payment.return_value.create.return_value = {
            "status": 201,
            "response": {"id": "mp-1", "status": "pending"},
        }

        response = create_pix_payment(payment)

        self.assertEqual(response["id"], "mp-1")
        sdk_mock.payment.return_value.create.assert_called_once()

    @patch("src.payments.services.mercadopago.sdk")
    def test_create_pix_payment_failure_returns_none(self, sdk_mock):
        payment = Payment.objects.create(
            user=self.user,
            pool=self.pool,
            status="pending",
            payment_method="pix",
            amount=Decimal("123.45"),
        )
        sdk_mock.payment.return_value.create.return_value = {
            "status": 500,
            "response": {"message": "error"},
        }

        response = create_pix_payment(payment)

        self.assertIsNone(response)

    @patch("src.payments.services.mercadopago.sdk")
    def test_get_payment_status_non_200_returns_none(self, sdk_mock):
        sdk_mock.payment.return_value.get.return_value = {"status": 404, "response": {}}
        self.assertIsNone(get_payment_status("mp-404"))


class PaymentViewsTest(PaymentsBaseTestCase):
    def test_create_subscription_invalid_amount_returns_error_page(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("payments:create-subscription"),
            data={"amount": "invalid", "pool_id": self.pool.id},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Payment.objects.count(), 0)

    @patch("src.payments.views.create_pix_payment")
    def test_create_subscription_success_creates_payment_and_redirects(self, create_pix_payment_mock):
        self.client.force_login(self.user)
        create_pix_payment_mock.return_value = {"id": "9988", "status": "pending"}

        response = self.client.post(
            reverse("payments:create-subscription"),
            data={"amount": "1.234,56", "pool_id": self.pool.id},
        )

        self.assertEqual(response.status_code, 302)
        payment = Payment.objects.get(user=self.user)
        self.assertEqual(payment.amount, Decimal("1234.56"))
        self.assertIn(f"/payments/pix/{payment.id}/", response.url)

    @patch("src.payments.views.create_pix_payment")
    def test_create_subscription_mp_failure_rolls_back_payment(self, create_pix_payment_mock):
        self.client.force_login(self.user)
        create_pix_payment_mock.return_value = None

        response = self.client.post(
            reverse("payments:create-subscription"),
            data={"amount": "100,00", "pool_id": self.pool.id},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Payment.objects.count(), 0)

    @patch("src.payments.views.get_payment_status", return_value=None)
    def test_pix_payment_view_redirects_to_pending_when_mp_data_is_none(self, _mp_mock):
        self.client.force_login(self.user)
        payment = Payment.objects.create(
            user=self.user,
            pool=self.pool,
            mp_payment_id="mp-x",
            status="pending",
            payment_method="pix",
            amount=Decimal("100.00"),
        )

        response = self.client.get(reverse("payments:pix-payment", kwargs={"payment_id": payment.id}))

        self.assertEqual(response.status_code, 302)
        self.assertIn(f"/payments/pending/{payment.id}/", response.url)

    def test_pix_payment_view_returns_404_when_already_paid(self):
        self.client.force_login(self.user)
        payment = Payment.objects.create(
            user=self.user,
            pool=self.pool,
            mp_payment_id="mp-ok",
            status="approved",
            payment_method="pix",
            amount=Decimal("100.00"),
        )

        response = self.client.get(reverse("payments:pix-payment", kwargs={"payment_id": payment.id}))
        self.assertEqual(response.status_code, 404)

    def test_payment_status_view_returns_redirect_to_success_when_paid(self):
        self.client.force_login(self.user)
        payment = Payment.objects.create(
            user=self.user,
            pool=self.pool,
            mp_payment_id="mp-ok",
            status="approved",
            payment_method="pix",
            amount=Decimal("100.00"),
        )

        response = self.client.get(reverse("payments:payment-status", kwargs={"payment_id": payment.id}))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "approved")
        self.assertTrue(response.json()["is_paid"])
        self.assertEqual(
            response.json()["redirect_url"],
            reverse("payments:payment-success", kwargs={"payment_id": payment.id}),
        )

    def test_payment_views_enforce_user_ownership(self):
        self.client.force_login(self.user)
        payment = Payment.objects.create(
            user=self.other_user,
            pool=self.pool,
            mp_payment_id="mp-other",
            status="pending",
            payment_method="pix",
            amount=Decimal("100.00"),
        )

        pix = self.client.get(reverse("payments:pix-payment", kwargs={"payment_id": payment.id}))
        success = self.client.get(reverse("payments:payment-success", kwargs={"payment_id": payment.id}))
        pending = self.client.get(reverse("payments:payment-pending", kwargs={"payment_id": payment.id}))

        self.assertEqual(pix.status_code, 404)
        self.assertEqual(success.status_code, 404)
        self.assertEqual(pending.status_code, 404)


class MercadoPagoWebhookTest(PaymentsBaseTestCase):
    def setUp(self):
        super().setUp()
        self.payment = Payment.objects.create(
            user=self.user,
            pool=self.pool,
            mp_payment_id="12345",
            status="pending",
            payment_method="pix",
            amount=Decimal("100.00"),
        )
        self.url = f"{reverse('payments:webhook')}?data.id=12345"
        self.payload = {
            "type": "payment",
            "action": "payment.updated",
            "data": {"id": "12345"},
        }
        self.raw_body = json.dumps(self.payload).encode("utf-8")

    @patch("src.payments.webhooks.settings.MERCADO_PAGO_WEBHOOK_SECRET", "secret123")
    def test_webhook_invalid_signature_returns_401(self):
        headers = {
            "HTTP_X_SIGNATURE": "ts=1710000000,v1=invalid",
            "HTTP_X_REQUEST_ID": "req-abc-123",
            "CONTENT_TYPE": "application/json",
        }
        response = self.client.post(self.url, data=self.raw_body, content_type="application/json", **headers)
        self.assertEqual(response.status_code, 401)

    @patch("src.payments.webhooks.settings.MERCADO_PAGO_WEBHOOK_SECRET", "secret123")
    def test_webhook_missing_signature_headers_returns_401(self):
        response = self.client.post(self.url, data=self.raw_body, content_type="application/json")
        self.assertEqual(response.status_code, 401)

    @patch("src.payments.webhooks.settings.MERCADO_PAGO_WEBHOOK_SECRET", "secret123")
    def test_webhook_invalid_json_returns_400(self):
        headers = self.build_signature_headers(data_id="12345")
        response = self.client.post(self.url, data=b"{", content_type="application/json", **headers)
        self.assertEqual(response.status_code, 400)

    @patch("src.payments.webhooks.settings.MERCADO_PAGO_WEBHOOK_SECRET", "secret123")
    def test_webhook_missing_payment_id_returns_400(self):
        headers = self.build_signature_headers(data_id="")
        payload = {"type": "payment", "action": "payment.updated", "data": {}}
        response = self.client.post(
            f"{reverse('payments:webhook')}?data.id=",
            data=json.dumps(payload).encode("utf-8"),
            content_type="application/json",
            **headers,
        )
        self.assertEqual(response.status_code, 400)

    @patch("src.payments.webhooks.get_payment_status")
    @patch("src.payments.webhooks.settings.MERCADO_PAGO_WEBHOOK_SECRET", "secret123")
    def test_duplicate_webhook_event_is_ignored(self, payment_status_mock):
        payment_status_mock.return_value = {
            "status": "approved",
            "payment_method_id": "pix",
            "transaction_amount": "100.00",
        }
        headers = self.build_signature_headers(data_id="12345")

        first = self.client.post(self.url, data=self.raw_body, content_type="application/json", **headers)
        second = self.client.post(self.url, data=self.raw_body, content_type="application/json", **headers)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(WebhookEvent.objects.count(), 1)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, "approved")

    @patch("src.payments.webhooks.get_payment_status")
    @patch("src.payments.webhooks.settings.MERCADO_PAGO_WEBHOOK_SECRET", "secret123")
    def test_webhook_non_payment_type_is_ignored(self, payment_status_mock):
        headers = self.build_signature_headers(data_id="12345")
        payload = {"type": "merchant_order", "action": "updated", "data": {"id": "12345"}}
        response = self.client.post(
            self.url,
            data=json.dumps(payload).encode("utf-8"),
            content_type="application/json",
            **headers,
        )

        self.assertEqual(response.status_code, 200)
        payment_status_mock.assert_not_called()

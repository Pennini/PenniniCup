from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse


class HealthCheckViewTest(TestCase):
    def test_health_endpoint_returns_200_when_checks_pass(self):
        response = self.client.get(reverse("health"))

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["checks"]["database"]["status"], "ok")
        self.assertEqual(data["checks"]["migrations"]["status"], "ok")
        self.assertIn(data["checks"]["redis"]["status"], ["ok", "skipped"])
        self.assertIn("X-Request-UUID", response.headers)

    @patch("src.config.health._check_database", side_effect=RuntimeError("db down"))
    def test_health_endpoint_returns_503_when_database_fails(self, _db_mock):
        response = self.client.get(reverse("health"))

        self.assertEqual(response.status_code, 503)
        data = response.json()
        self.assertEqual(data["status"], "degraded")
        self.assertEqual(data["checks"]["database"]["status"], "error")
        self.assertIn("db down", data["checks"]["database"]["detail"])

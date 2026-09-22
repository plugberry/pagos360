from odoo.exceptions import ValidationError
from odoo.tests.common import tagged

from .common import Pagos360TransactionCase


@tagged("post_install", "-at_install")
class TestEnsureWebhookKeepsAWorkingRegistration(Pagos360TransactionCase):
    """Ensuring the webhook must never leave the provider without one.

    Dado un proveedor con un webhook ya registrado bajo una URL vieja, cuando se vuelve a
    ejecutar "Ensure Webhook" y el alta del nuevo falla (Pagos360 caído, API key vencida),
    entonces el registro viejo sigue vivo: peor es recibir notificaciones a una URL que
    rechazamos que no recibir ninguna.

    Bug: `_webhook_is_set` borra el registro viejo antes de que exista el nuevo, y la
    migración 19.0.2.2.0 se come la excepción — la base queda sin webhook, en silencio.

    Se demuestra en rojo: hoy se emite el DELETE igual; pasa cuando el alta vaya primero.
    """

    def _fake_api(self, fail_on_create, calls):
        """Registered webhook on the same path with a stale token, and a POST that fails."""

        def _request(endpoint, data=None, method="POST"):
            calls.append((endpoint, method))
            if (endpoint, method) == ("/webhook", "GET"):
                return {
                    "data": [
                        {
                            "id": 77,
                            "url": "https://example.com/payment/pagos360/webhook?token=stale",
                            "events": [],
                        }
                    ]
                }
            if (endpoint, method) == ("/event-type?limit=50", "GET"):
                return {"data": [{"id": 1, "name": "payment_request.paid"}]}
            if (endpoint, method) == ("/webhook", "POST"):
                if fail_on_create:
                    raise ValidationError("Pagos360 is down")
                return {"id": 123}
            return {}

        return _request

    def test_stale_registration_survives_a_failed_re_registration(self):
        calls = []
        with self._patch_api(side_effect=self._fake_api(fail_on_create=True, calls=calls)):
            with self.assertRaises(ValidationError):
                self.provider.ensure_webhook()
        deletes = [call for call in calls if call[1] == "DELETE"]
        self.assertFalse(deletes, "the old webhook must not be dropped before the new one is registered")

    def test_stale_registration_is_dropped_once_the_new_one_is_registered(self):
        calls = []
        with self._patch_api(side_effect=self._fake_api(fail_on_create=False, calls=calls)):
            self.provider.ensure_webhook()
        methods = [method for _endpoint, method in calls]
        self.assertIn("DELETE", methods, "the stale registration is dropped after a successful one")
        self.assertLess(
            methods.index("POST"),
            methods.index("DELETE"),
            "the new registration is created before dropping the old one",
        )

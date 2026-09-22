import json

from odoo.tests.common import HttpCase, tagged

from .common import WEBHOOK_TOKEN, Pagos360Common

REFERENCE = "TEST/BUG/WEBHOOK"
ENTITY_ID = 999701


@tagged("post_install", "-at_install")
class TestWebhookErrorHandling(Pagos360Common, HttpCase):
    """The webhook must always acknowledge, whatever the entity looks like.

    Dado una entidad que Pagos360 devuelve con una forma inesperada (sin `id` o sin `state`),
    cuando llega la notificación, entonces se responde 200 y se loguea: un 500 hace que
    Pagos360 reintente la misma notificación una y otra vez.

    Bug: el controlador solo captura `ValidationError`; un KeyError de `simulate_webhook`
    sale como error 500.

    Se demuestra en rojo: hoy responde 500; pasa cuando el except cubra cualquier excepción.
    """

    def _post(self, entity):
        payload = {
            "entity_name": "payment_request",
            "entity_id": ENTITY_ID,
            "type": "paid",
            "payload": {"id": ENTITY_ID, "external_reference": REFERENCE},
        }
        with self._patch_api(return_value={"data": [entity]}):
            return self.url_open(
                "/payment/pagos360/webhook?token=%s" % WEBHOOK_TOKEN,
                data=json.dumps(payload),
                headers={"Content-Type": "application/json"},
            )

    def test_entity_without_state_is_acknowledged(self):
        tx = self._make_tx(REFERENCE)
        response = self._post({"id": ENTITY_ID, "external_reference": REFERENCE})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(tx.state, "draft")

    def test_entity_without_id_is_acknowledged(self):
        tx = self._make_tx(REFERENCE + "/NOID")
        response = self._post({"state": "paid", "external_reference": REFERENCE + "/NOID"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(tx.state, "draft")

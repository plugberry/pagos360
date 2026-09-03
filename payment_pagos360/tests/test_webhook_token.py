import json
from unittest.mock import call, patch

from odoo.tests.common import HttpCase, tagged


@tagged("post_install", "-at_install")
class TestWebhookToken(HttpCase):
    """Covers task 72382: the webhook had no way to reject a request that only guessed
    the fixed URL and a valid external_reference."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.provider = cls.env.ref("payment_pagos360.payment_provider_pagos360")
        cls.provider.write({"state": "test", "pagos360_webhook_token": "the-real-token"})
        cls.payment_method = cls.env.ref("payment_pagos360.payment_method_pagos360")
        cls.partner = cls.env["res.partner"].create({"name": "Test Buyer"})

    def _make_tx(self, reference):
        return self.env["payment.transaction"].create(
            {
                "provider_id": self.provider.id,
                "payment_method_id": self.payment_method.id,
                "operation": "online_redirect",
                "reference": reference,
                "amount": 500,
                "currency_id": self.env.company.currency_id.id,
                "partner_id": self.partner.id,
            }
        )

    def _post(self, reference, token):
        url = "/payment/pagos360/webhook"
        if token is not None:
            url += "?token=%s" % token
        payload = {
            "entity_name": "payment_request",
            "entity_id": 1,
            "type": "paid",
            "payload": {"id": 1, "request_result_id": 1, "external_reference": reference},
        }
        return self.url_open(url, data=json.dumps(payload), headers={"Content-Type": "application/json"})

    def test_ensure_webhook_generates_a_token(self):
        self.provider.pagos360_webhook_token = False
        with patch.object(type(self.provider), "_webhook_is_set", return_value=True):
            self.provider.ensure_webhook()
        self.assertTrue(self.provider.pagos360_webhook_token)

    def test_webhook_is_set_drops_stale_registrations_on_the_same_path(self):
        def fake_request(endpoint, data=None, method="POST"):
            if (endpoint, method) == ("/webhook", "GET"):
                return {
                    "data": [
                        {"id": 1, "url": "https://x/payment/pagos360/webhook", "events": []},
                        {"id": 2, "url": "https://x/payment/other/webhook", "events": []},
                    ]
                }
            return {}

        with patch.object(type(self.provider), "_pagos360_make_request", side_effect=fake_request) as make_request:
            self.assertFalse(self.provider._webhook_is_set("https://x/payment/pagos360/webhook?token=new"))
        # Same path, stale URL: dropped. Different path: left alone.
        self.assertIn(call("/webhook/1", method="DELETE"), make_request.call_args_list)
        self.assertNotIn(call("/webhook/2", method="DELETE"), make_request.call_args_list)

    def test_notification_rejected_without_a_matching_token(self):
        for token in (None, "wrong-token"):
            with self.subTest(token=token):
                tx = self._make_tx("REF-%s" % token)
                # assertLogs captures the expected rejection warning instead of letting it
                # propagate — runbot's modified-modules check flags any WARNING/ERROR line.
                with self.assertLogs("odoo.addons.payment_pagos360.controllers.main", level="WARNING"):
                    response = self._post(tx.reference, token)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(tx.state, "draft")

    def test_notification_with_matching_token_is_processed(self):
        tx = self._make_tx("REF-ok")
        # The controller reads the entity back from the API; keep the test offline.
        entity = {"data": [{"id": 1, "state": "paid", "external_reference": tx.reference}]}
        with patch.object(type(self.provider), "_pagos360_make_request", return_value=entity):
            response = self._post(tx.reference, self.provider.pagos360_webhook_token)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(tx.state, "done")

import json
from unittest.mock import patch

from odoo.tests import HttpCase, tagged


@tagged("post_install", "-at_install")
class TestWebhookForgery(HttpCase):
    """The webhook must not trust the state the notification claims: it reads the entity
    back from the Pagos360 API and processes that. A forged 'paid' notification for a
    charge nobody paid must leave the transaction untouched."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.provider = cls.env.ref("payment_pagos360.payment_provider_pagos360")
        cls.provider.write({"active": True, "is_live": False})
        cls.payment_method = cls.env.ref("payment_pagos360.payment_method_pagos360")
        cls.partner = cls.env["res.partner"].create({"name": "Test Buyer"})

    def _make_pending_tx(self, reference, amount):
        tx = self.env["payment.transaction"].create(
            {
                "provider_id": self.provider.id,
                "payment_method_id": self.payment_method.id,
                "operation": "online_redirect",
                "reference": reference,
                "amount": amount,
                "currency_id": self.env.company.currency_id.id,
                "partner_id": self.partner.id,
            }
        )
        tx.with_context(payment_safe_write=True)._set_pending()
        return tx

    def _post(self, body):
        response = self.url_open(
            "/payment/pagos360/webhook",
            data=json.dumps(body),
            headers={"Content-Type": "application/json"},
        )
        # In 20 the webhook only queues the payment data; apply it as the processing cron does.
        for payment_data in self.env["payment.data"].sudo().search([("errored", "=", False)]):
            payment_data.transaction_id.with_context(payment_safe_write=True)._process(payment_data.payload)
            payment_data.unlink()
        return response

    def test_forged_paid_notification_does_not_mark_tx_paid(self):
        reference = "TEST/2026/00001"
        tx = self._make_pending_tx(reference, 50000.0)
        forged = {
            "entity_name": "payment_request",
            "entity_id": "999001",
            "type": "paid",
            "payload": {
                "id": "999001",
                "external_reference": reference,
                "request_result": [{"amount": 50000.0, "paid_at": "2026-10-23 10:00:00"}],
            },
        }
        # The API says the real state is still pending: the notification's "paid" is a lie.
        api_pending = {"data": [{"id": "999001", "external_reference": reference, "state": "pending"}]}
        with patch.object(type(self.provider), "_pagos360_make_request", return_value=api_pending):
            response = self._post(forged)
        self.assertEqual(response.status_code, 200)
        tx.invalidate_recordset()
        self.assertEqual(tx.state, "pending", "a forged notification must not move the state")

    def test_webhook_processes_real_state_from_api(self):
        reference = "TEST/2026/00002"
        tx = self._make_pending_tx(reference, 50000.0)
        notification = {
            "entity_name": "payment_request",
            "entity_id": "999002",
            "payload": {"id": "999002", "external_reference": reference},
        }
        # The API confirms the charge is really paid.
        api_paid = {
            "data": [
                {
                    "id": "999002",
                    "external_reference": reference,
                    "state": "paid",
                    "request_result": [{"amount": 50000.0, "paid_at": "2026-10-23 10:00:00"}],
                }
            ]
        }
        with patch.object(type(self.provider), "_pagos360_make_request", return_value=api_paid):
            response = self._post(notification)
        self.assertEqual(response.status_code, 200)
        tx.invalidate_recordset()
        self.assertEqual(tx.state, "done", "a paid state confirmed by the API must be processed")

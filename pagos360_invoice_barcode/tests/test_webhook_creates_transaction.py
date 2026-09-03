import json
from unittest.mock import patch

from odoo.tests.common import HttpCase, tagged

TOKEN = "the-real-token"
ENTITY_ID = 999401
AMOUNT = 500.0


@tagged("post_install", "-at_install")
class TestWebhookCreatesTransaction(HttpCase):
    """Covers task 72382, point 2, for the barcode flow: `_search_by_reference` creates the
    transaction from the notification's own claim, so it must see the real state too — not
    just `payment_pagos360`, which is what the other test in that module checks."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.provider = cls.env.ref("payment_pagos360.payment_provider_pagos360")
        cls.provider.write({"state": "test", "pagos360_webhook_token": TOKEN})
        cls.partner = cls.env["res.partner"].create({"name": "Test Buyer"})
        cls.invoice = cls.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "partner_id": cls.partner.id,
                "invoice_line_ids": [
                    (0, 0, {"product_id": cls.env.ref("product.product_product_16").id, "price_unit": AMOUNT})
                ],
                "pagos360_barcode_amount": AMOUNT,
            }
        )
        # action_post triggers a real barcode request; the invoice doesn't need one for this test.
        with patch.object(type(cls.provider), "_pagos360_make_request", return_value={}):
            cls.invoice.action_post()
        cls.reference = f"inv-{cls.provider.id}-{cls.invoice.id}"

    def _post(self, entity_state):
        entity = {"id": ENTITY_ID, "state": entity_state, "external_reference": self.reference}
        # What Pagos360 actually posts: claims "paid" regardless of `entity_state`, the real
        # state the API holds. A fix-less controller would trust this and create the tx anyway.
        payload = {
            "entity_name": "payment_request",
            "entity_id": ENTITY_ID,
            "type": "paid",
            "payload": {"id": ENTITY_ID, "request_result_id": ENTITY_ID, "external_reference": self.reference},
        }
        with patch.object(type(self.provider), "_pagos360_make_request", return_value={"data": [entity]}):
            return self.url_open(
                "/payment/pagos360/webhook?token=%s" % TOKEN,
                data=json.dumps(payload),
                headers={"Content-Type": "application/json"},
            )

    def test_transaction_not_created_when_the_api_disagrees_with_the_claimed_paid(self):
        response = self._post("pending")
        self.assertEqual(response.status_code, 200)
        tx = self.env["payment.transaction"].search([("reference", "=", self.reference)])
        self.assertFalse(tx)

    def test_transaction_created_when_the_api_confirms_paid(self):
        response = self._post("paid")
        self.assertEqual(response.status_code, 200)
        tx = self.env["payment.transaction"].search([("reference", "=", self.reference)])
        self.assertTrue(tx)
        self.assertEqual(tx.invoice_ids, self.invoice)

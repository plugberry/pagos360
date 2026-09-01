from unittest.mock import patch

from odoo.tests.common import TransactionCase, tagged

REFERENCE = "TEST/2026/00003"
AMOUNT = 500.0
ENTITY_ID = 999301


@tagged("post_install", "-at_install")
class TestWebhookStateReconfirmation(TransactionCase):
    """Covers task 72382, point 2: payment_request notifications are reconfirmed against the API."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.provider = cls.env.ref("payment_pagos360.payment_provider_pagos360")
        cls.provider.write({"state": "test"})
        cls.payment_method = cls.env.ref("payment_pagos360.payment_method_pagos360")
        cls.partner = cls.env["res.partner"].create({"name": "Test Buyer"})

    def _make_tx(self, reference=REFERENCE):
        return self.env["payment.transaction"].create(
            {
                "provider_id": self.provider.id,
                "payment_method_id": self.payment_method.id,
                "operation": "online_redirect",
                "reference": reference,
                "amount": AMOUNT,
                "currency_id": self.env.company.currency_id.id,
                "partner_id": self.partner.id,
            }
        )

    def _webhook_data(self, claimed_type, entity_name="payment_request", from_webhook=True):
        return {
            "entity_name": entity_name,
            "entity_id": ENTITY_ID,
            "type": claimed_type,
            "payload": {"id": ENTITY_ID, "request_result_id": 1, "external_reference": REFERENCE},
            "from_webhook": from_webhook,
        }

    def _process(self, tx, data, api_response):
        with patch.object(type(self.provider), "_pagos360_make_request", return_value=api_response):
            self.env["payment.transaction"].sudo()._process("pagos360", data)

    def test_webhook_claiming_paid_is_ignored_if_the_api_disagrees(self):
        tx = self._make_tx()
        self._process(tx, self._webhook_data("paid"), {"data": [{"state": "pending"}]})
        self.assertEqual(tx.state, "pending")

    def test_webhook_matching_the_api_state_is_processed(self):
        tx = self._make_tx()
        self._process(tx, self._webhook_data("paid"), {"data": [{"state": "paid"}]})
        self.assertEqual(tx.state, "done")

    def test_notification_ignored_when_the_api_is_unreachable(self):
        tx = self._make_tx()
        with (
            patch.object(type(self.provider), "_pagos360_make_request", side_effect=Exception("boom")),
            self.assertLogs("odoo.addons.payment_pagos360.models.payment_transaction", level="WARNING"),
        ):
            self.env["payment.transaction"].sudo()._process("pagos360", self._webhook_data("paid"))
        self.assertEqual(tx.state, "draft")

    def test_other_entity_types_keep_trusting_the_webhook(self):
        token = self.env["payment.token"].create(
            {
                "provider_id": self.provider.id,
                "payment_method_id": self.payment_method.id,
                "partner_id": self.partner.id,
                "provider_ref": "63897",
                "pagos360_adhesion_type": "card_adhesion",
                "payment_details": "VISA **** - 5976",
            }
        )
        tx = self.env["payment.transaction"].create(
            {
                "provider_id": self.provider.id,
                "payment_method_id": self.payment_method.id,
                "operation": "online_token",
                "reference": REFERENCE,
                "amount": AMOUNT,
                "currency_id": self.env.company.currency_id.id,
                "partner_id": self.partner.id,
                "token_id": token.id,
            }
        )
        data = self._webhook_data("paid", entity_name="card_adhesion")
        self._process(tx, data, {"data": [{"state": "pending"}]})
        self.assertEqual(tx.state, "done")

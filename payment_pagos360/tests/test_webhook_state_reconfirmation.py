import json
from unittest.mock import patch

from odoo.exceptions import ValidationError
from odoo.tests.common import HttpCase, tagged

REFERENCE = "TEST/2026/00003"
AMOUNT = 500.0
ENTITY_ID = 999301
TOKEN = "the-real-token"


@tagged("post_install", "-at_install")
class TestWebhookStateReconfirmation(HttpCase):
    """Covers task 72382, point 2: a notification is only a hint that something changed, so
    the entity is read back from Pagos360 and that is what gets processed."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.provider = cls.env.ref("payment_pagos360.payment_provider_pagos360")
        cls.provider.write({"state": "test", "pagos360_webhook_token": TOKEN})
        cls.payment_method = cls.env.ref("payment_pagos360.payment_method_pagos360")
        cls.partner = cls.env["res.partner"].create({"name": "Test Buyer"})

    def _make_tx(self, reference=REFERENCE, **vals):
        return self.env["payment.transaction"].create(
            {
                "provider_id": self.provider.id,
                "payment_method_id": self.payment_method.id,
                "operation": "online_redirect",
                "reference": reference,
                "amount": AMOUNT,
                "currency_id": self.env.company.currency_id.id,
                "partner_id": self.partner.id,
                **vals,
            }
        )

    def _post(self, claimed_type, entity_name="payment_request"):
        """POST a notification claiming `claimed_type`, the way Pagos360 fires it."""
        payload = {
            "entity_name": entity_name,
            "entity_id": ENTITY_ID,
            "type": claimed_type,
            "payload": {"id": ENTITY_ID, "request_result_id": 1, "external_reference": REFERENCE},
        }
        return self.url_open(
            "/payment/pagos360/webhook?token=%s" % TOKEN,
            data=json.dumps(payload),
            headers={"Content-Type": "application/json"},
        )

    def _api_entity(self, state, **extra):
        return {"data": [dict({"id": ENTITY_ID, "state": state, "external_reference": REFERENCE}, **extra)]}

    # --- the API decides the state, in both directions ---------------------------------

    def test_notification_claiming_paid_is_ignored_when_the_api_says_otherwise(self):
        tx = self._make_tx()
        with patch.object(type(self.provider), "_pagos360_make_request", return_value=self._api_entity("pending")):
            response = self._post("paid")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(tx.state, "pending")

    def test_notification_claiming_pending_is_paid_when_the_api_says_so(self):
        tx = self._make_tx()
        with patch.object(type(self.provider), "_pagos360_make_request", return_value=self._api_entity("paid")):
            response = self._post("pending")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(tx.state, "done")

    def test_every_entity_type_is_read_back_not_just_payment_request(self):
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
        tx = self._make_tx(operation="online_token", token_id=token.id)
        with patch.object(type(self.provider), "_pagos360_make_request", return_value=self._api_entity("pending")):
            response = self._post("paid", entity_name="card_adhesion")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(tx.state, "pending")

    # --- what happens when we can't read the entity back --------------------------------

    def test_notification_ignored_when_the_api_is_unreachable(self):
        tx = self._make_tx()
        with (
            patch.object(
                type(self.provider), "_pagos360_make_request", side_effect=ValidationError("Pagos360 is down")
            ),
            self.assertLogs("odoo.addons.payment_pagos360.controllers.main", level="ERROR"),
        ):
            response = self._post("paid")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(tx.state, "draft")

    def test_notification_ignored_when_the_entity_is_unknown_to_the_api(self):
        tx = self._make_tx()
        with (
            patch.object(type(self.provider), "_pagos360_make_request", return_value={"data": []}),
            self.assertLogs("odoo.addons.payment_pagos360.controllers.main", level="WARNING"),
        ):
            response = self._post("paid")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(tx.state, "draft")

    # --- the payment date comes from the same response ----------------------------------

    def test_effective_payment_date_is_read_from_the_entity(self):
        tx = self._make_tx()
        entity = self._api_entity("paid", request_result=[{"amount": AMOUNT, "paid_at": "2026-08-27 10:15:00"}])
        with patch.object(type(self.provider), "_pagos360_make_request", return_value=entity) as make_request:
            self._post("paid")
        self.assertEqual(tx.state, "done")
        self.assertEqual(str(tx.pagos360_effective_payment_date), "2026-08-27")
        # State and payment date come out of the same response.
        self.assertEqual(make_request.call_count, 1)

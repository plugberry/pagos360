from unittest.mock import patch

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestSimulateNotification(TransactionCase):
    """In test mode the buttons process a locally built notification and post-process it right away,
    so a test base can reach what happens when a debit or a coupon is paid, rejected or reverted."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.provider = cls.env.ref("payment_pagos360.payment_provider_pagos360")
        cls.provider.write(
            {
                "active": True,
                "is_live": False,
                "pagos360_test_api_key": "test-key",
                "pagos360_api_key": "live-key",
                "pagos360_form_url": "https://example.com/adhesion",
            }
        )
        cls.payment_method = cls.env.ref("payment_pagos360.payment_method_pagos360")
        cls.partner = cls.env["res.partner"].create({"name": "Test Buyer"})

    def _make_tx(self, adhesion_type=False, state="pending"):
        values = {}
        if adhesion_type:
            token = self.env["payment.token"].create(
                {
                    "provider_id": self.provider.id,
                    "payment_method_id": self.payment_method.id,
                    "partner_id": self.partner.id,
                    "provider_ref": "63897",
                    "payment_details": "Test token",
                    "pagos360_adhesion_type": adhesion_type,
                }
            )
            values = {"operation": "online_token", "token_id": token.id, "provider_reference": "999601"}
        tx = self.env["payment.transaction"].create(
            {
                "provider_id": self.provider.id,
                "payment_method_id": self.payment_method.id,
                "operation": "online_redirect",
                "amount": 500.0,
                "currency_id": self.env.company.currency_id.id,
                "partner_id": self.partner.id,
                **values,
            }
        )
        safe_tx = tx.with_context(payment_safe_write=True)
        {"draft": lambda: None, "pending": safe_tx._set_pending, "done": safe_tx._set_done}[state]()
        return tx

    def _simulate(self, tx, status):
        # Only a rejection falls back to the API, for the effective payment date; keep it offline.
        with patch.object(type(self.provider), "_pagos360_make_request", return_value={}) as make_request:
            action = getattr(tx, f"action_pagos360_simulate_{status}")()
        return action, make_request

    def test_simulate_paid(self):
        for adhesion_type, state in (("adhesion", "pending"), ("card_adhesion", "pending"), (False, "draft")):
            with self.subTest(adhesion_type=adhesion_type, state=state):
                tx = self._make_tx(adhesion_type, state)
                action, make_request = self._simulate(tx, "paid")
                make_request.assert_not_called()
                self.assertEqual(tx.state, "done")
                self.assertFalse(tx.state_message, "the amount check must pass")
                self.assertEqual(tx.pagos360_effective_payment_date, fields.Date.today())
                self.assertTrue(tx.is_post_processed, "the post-processing runs without waiting for the cron")
                self.assertTrue(tx.payment_id)
                self.assertTrue(action["params"]["sticky"])

    def test_simulate_rejected(self):
        tx = self._make_tx("adhesion")
        self._simulate(tx, "rejected")
        self.assertEqual(tx.state, "cancel")
        self.assertFalse(tx.payment_id)

    def test_simulate_reverted(self):
        tx = self._make_tx("adhesion")
        self._simulate(tx, "paid")
        payment = tx.payment_id
        self._simulate(tx, "reverted")
        self.assertEqual((tx.state, payment.state), ("cancel", "canceled"))

    def test_refused_outside_test_mode_or_state(self):
        tx = self._make_tx("adhesion")
        with self.assertRaises(UserError):
            self._simulate(tx, "reverted")  # not paid yet
        self.provider.is_live = True
        with self.assertRaises(UserError):
            self._simulate(tx, "paid")
        self.assertEqual(tx.state, "pending")

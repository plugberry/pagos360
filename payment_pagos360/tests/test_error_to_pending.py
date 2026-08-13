from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged
from odoo.tools import mute_logger

REFERENCE = "TEST/2026/00002"
AMOUNT = 86223.0
ENTITY_ID = 999101


@tagged("post_install", "-at_install")
class TestErrorToPending(TransactionCase):
    """Covers task 72144: a transaction left in `error` (e.g. a webhook that could not be
    processed) while still pending in Pagos360 stayed stuck, because `_set_pending` only
    accepts `draft` as source state. The check in now brings it back to `pending`."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.provider = cls.env.ref("payment_pagos360.payment_provider_pagos360")
        cls.provider.write({"state": "test"})
        cls.payment_method = cls.env.ref("payment_pagos360.payment_method_pagos360")
        cls.currency = cls.env.company.currency_id
        cls.partner = cls.env["res.partner"].create({"name": "Test Buyer"})

    def _make_tx(self, state=None, reference=REFERENCE):
        tx = self.env["payment.transaction"].create(
            {
                "provider_id": self.provider.id,
                "payment_method_id": self.payment_method.id,
                "operation": "online_redirect",
                "reference": reference,
                "amount": AMOUNT,
                "currency_id": self.currency.id,
                "partner_id": self.partner.id,
                "provider_reference": ENTITY_ID,
            }
        )
        if state == "error":
            tx._set_error("PAGOS360: previous notification could not be processed")
            self.assertEqual(tx.state, "error")
        return tx

    def _pagos360_data(self, tx, state):
        """One entity as Pagos360 returns it, without amount (see ticket 124521)."""
        return {"id": ENTITY_ID, "state": state, "external_reference": tx.reference}

    def _check_in(self, tx, state):
        """Run the `Check in (PAGOS360)` server action against a mocked API."""
        with (
            patch.object(
                type(self.provider), "_pagos360_make_request", return_value={"data": [self._pagos360_data(tx, state)]}
            ),
            # The action commits per transaction, which a test cursor refuses.
            patch.object(self.env.cr, "commit", lambda: None),
        ):
            try:
                # The action always ends dumping what it read as a UserError. Not wrapped in
                # assertRaises: that rolls back to a savepoint and undoes what we assert on.
                tx.get_pagos360_info()
            except UserError:
                pass
            else:
                self.fail("get_pagos360_info should end raising its readable result")

    def _process(self, tx, state):
        payload = tx.simulate_webhook("payment_request", self._pagos360_data(tx, state))
        with patch.object(type(self.provider), "_pagos360_make_request", return_value={}):
            self.env["payment.transaction"].sudo()._process("pagos360", payload)

    # --- the fix -----------------------------------------------------------------------

    def test_check_in_moves_error_to_pending(self):
        tx = self._make_tx(state="error")
        self._check_in(tx, "pending")
        self.assertEqual(tx.state, "pending")

    def test_pending_like_states_move_error_to_pending(self):
        for state in ["pending", "in_process", "transfer_created", "link_pagos_created", "debin_created"]:
            with self.subTest(state=state):
                tx = self._make_tx(state="error", reference=f"{REFERENCE}/{state}")
                self._process(tx, state)
                self.assertEqual(tx.state, "pending")

    # --- no regressions ----------------------------------------------------------------

    def test_check_in_still_moves_draft_to_pending(self):
        tx = self._make_tx()
        self._check_in(tx, "pending")
        self.assertEqual(tx.state, "pending")

    def test_check_in_still_moves_error_to_done_when_paid(self):
        tx = self._make_tx(state="error")
        self._check_in(tx, "paid")
        self.assertEqual(tx.state, "done")

    @mute_logger("odoo.addons.payment.models.payment_transaction")
    def test_done_transaction_is_not_moved_back_to_pending(self):
        tx = self._make_tx()
        tx._set_done()
        self._process(tx, "pending")
        self.assertEqual(tx.state, "done")

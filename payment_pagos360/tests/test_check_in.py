from unittest.mock import patch

from dateutil.relativedelta import relativedelta
from odoo import fields
from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged
from odoo.tools import mute_logger

# The reference comes from the sale order name, which may carry a trailing space.
REFERENCE = "Flia. TEST , TEST , Nombre "
REQUEST_ID = 109019359


@tagged("post_install", "-at_install")
class TestCheckIn(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.provider = cls.env.ref("payment_pagos360.payment_provider_pagos360")
        cls.provider.write({"state": "test"})
        cls.payment_method = cls.env.ref("payment_pagos360.payment_method_pagos360")
        cls.partner = cls.env["res.partner"].create({"name": "Test Buyer"})

    def _make_tx(self, provider_reference=False):
        return self.env["payment.transaction"].create(
            {
                "provider_id": self.provider.id,
                "payment_method_id": self.payment_method.id,
                "operation": "online_redirect",
                "reference": REFERENCE,
                "amount": 100.0,
                "currency_id": self.env.company.currency_id.id,
                "partner_id": self.partner.id,
                "provider_reference": provider_reference,
            }
        )

    def _payment_request(self, reference, request_id=REQUEST_ID):
        return {
            "id": request_id,
            "state": "paid",
            "external_reference": reference,
            "request_result": [{"paid_at": "2026-02-23T16:18:44-03:00", "amount": 100.0}],
        }

    def _check_in(self, tx, api_data):
        """Run the check in against a fake API; return the endpoints called and the result text."""
        calls = []

        def fake_request(provider, endpoint, data=None, method="POST"):
            calls.append(endpoint)
            return api_data

        with (
            patch.object(type(self.provider), "_pagos360_make_request", autospec=True, side_effect=fake_request),
            # The check in commits after each transaction, which the test cursor forbids.
            patch.object(self.env.cr, "commit", lambda: None),
        ):
            # The check in always reports its result through a UserError. Odoo's assertRaises
            # would roll back the transaction updates, so catch it by hand.
            try:
                tx.get_pagos360_info()
            except UserError as error:
                return calls, str(error)
        self.fail("get_pagos360_info() did not report its result")

    def test_reference_lookup_is_stripped_and_dated(self):
        tx = self._make_tx()
        calls, _result = self._check_in(tx, {"data": [self._payment_request(REFERENCE.strip())]})
        self.assertEqual(len(calls), 1)
        self.assertIn("external_reference=%s&" % REFERENCE.strip(), calls[0])
        self.assertIn("created_at_gte=", calls[0])
        self.assertIn("created_at_lte=", calls[0])
        self.assertEqual(tx.state, "done")

    def test_trailing_space_matches_stored_reference(self):
        tx = self._make_tx()
        api_data = {
            "data": [
                self._payment_request(REFERENCE.strip() + " -1", request_id=1),
                self._payment_request(REFERENCE.strip()),
            ]
        }
        self._check_in(tx, api_data)
        self.assertEqual(tx.state, "done")
        self.assertEqual(tx.provider_reference, str(REQUEST_ID))
        self.assertEqual(str(tx.pagos360_effective_payment_date), "2026-02-23")

    # simulate_webhook logs a warning on empty data, which runbot would count as a failure.
    @mute_logger("odoo.addons.payment_pagos360.models.payment_transaction")
    def test_missing_request_is_reported(self):
        tx = self._make_tx()
        _calls, result = self._check_in(tx, {"data": []})
        self.assertEqual(tx.state, "draft")
        self.assertIn("No payment request found", result)

    def test_lookup_by_provider_reference(self):
        tx = self._make_tx(provider_reference=str(REQUEST_ID))
        calls, _result = self._check_in(tx, {"data": [self._payment_request(REFERENCE.strip())]})
        self.assertIn("id=%s" % REQUEST_ID, calls[0])
        self.assertNotIn("created_at", calls[0])
        self.assertEqual(tx.state, "done")

    def test_explicit_date_range(self):
        from_date = fields.Date.today() - relativedelta(months=6)
        to_date = fields.Date.today()
        expected = "created_at_gte=%s&created_at_lte=%s" % (
            from_date.strftime("%d-%m-%Y"),
            to_date.strftime("%d-%m-%Y"),
        )
        calls = []

        def fake_request(provider, endpoint, data=None, method="POST"):
            calls.append(endpoint)
            return {"data": [self._payment_request(REFERENCE.strip())]}

        with patch.object(type(self.provider), "_pagos360_make_request", autospec=True, side_effect=fake_request):
            tx = self._make_tx()
            for provider_reference in (False, str(REQUEST_ID)):
                tx.provider_reference = provider_reference
                result = tx._pagos360_get_payment_request(from_date=from_date, to_date=to_date)
                self.assertEqual(result["id"], REQUEST_ID)
                self.assertTrue(calls[-1].endswith(expected))

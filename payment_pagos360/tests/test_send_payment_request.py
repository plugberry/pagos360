from unittest.mock import patch

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase, tagged

REFERENCE = "TEST/TOKEN/CHARGE"
ENTITY_ID = 999501


@tagged("post_install", "-at_install")
class TestSendPaymentRequest(TransactionCase):
    """Charging a token handles each adhesion type once, and refuses a token without one."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.provider = cls.env.ref("payment_pagos360.payment_provider_pagos360")
        cls.provider.write({"state": "test"})
        cls.payment_method = cls.env.ref("payment_pagos360.payment_method_pagos360")
        cls.partner = cls.env["res.partner"].create({"name": "Test Buyer"})

    def _make_tx(self, reference, adhesion_type):
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
        return self.env["payment.transaction"].create(
            {
                "provider_id": self.provider.id,
                "payment_method_id": self.payment_method.id,
                "operation": "online_token",
                "reference": reference,
                "amount": 500.0,
                "currency_id": self.env.company.currency_id.id,
                "partner_id": self.partner.id,
                "token_id": token.id,
            }
        )

    def _send(self, tx):
        response = {"id": ENTITY_ID, "state": "pending", "external_reference": tx.reference}
        with (
            patch.object(type(self.provider), "_pagos360_make_request", return_value=response),
            patch.object(type(tx), "get_debit_due_date", return_value=fields.Date.to_string(fields.Date.today())),
            # The method commits on purpose, which a test cursor refuses.
            patch.object(self.env.cr, "commit"),
            patch.object(type(tx), "_process", return_value=None) as process,
        ):
            tx._send_payment_request()
        return process

    def test_token_without_adhesion_type_is_refused(self):
        tx = self._make_tx(REFERENCE, adhesion_type=False)
        with self.assertRaises(ValidationError):
            self._send(tx)

    def test_each_adhesion_type_is_processed_once(self):
        for adhesion_type in ["card_adhesion", "adhesion"]:
            with self.subTest(adhesion_type=adhesion_type):
                tx = self._make_tx(f"{REFERENCE}/{adhesion_type}", adhesion_type)
                process = self._send(tx)
                self.assertEqual(process.call_count, 1)
                self.assertEqual(process.call_args.args[1]["entity_name"], adhesion_type)

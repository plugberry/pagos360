from unittest.mock import patch

from odoo.tests.common import TransactionCase, tagged

CBU = "3220001801000020816200"


@tagged("post_install", "-at_install")
class TestTokenValues(TransactionCase):
    """The token of a CBU adhesion keeps only the last digits of the CBU in its payment details."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.provider = cls.env.ref("payment_pagos360.payment_provider_pagos360")
        cls.provider.write({"active": True, "is_live": False})
        cls.partner = cls.env["res.partner"].create({"name": "Test Buyer"})

    def test_cbu_payment_details_show_last_digits(self):
        tx = self.env["payment.transaction"].create(
            {
                "provider_id": self.provider.id,
                "payment_method_id": self.env.ref("payment_pagos360.payment_method_pagos360").id,
                "operation": "validation",
                "reference": "TEST/CBU/ADHESION",
                "amount": 0.0,
                "currency_id": self.env.company.currency_id.id,
                "partner_id": self.partner.id,
            }
        )
        adhesion = {"bank": "BANCO INDUSTRIAL S.A.", "cbu_number": CBU, "external_reference": tx.reference}
        with patch.object(type(self.provider), "_pagos360_make_request", return_value=adhesion):
            values = tx._extract_token_values({"entity_id": 63897, "entity_name": "adhesion"})
        self.assertEqual(values["payment_details"], "Debito automático en CBU: BANCO INDUSTRIAL S.A. ****16200")
        self.assertNotIn(CBU, values["payment_details"])

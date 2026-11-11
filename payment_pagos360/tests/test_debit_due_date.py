from unittest.mock import patch

from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestDebitDueDate(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.provider = cls.env.ref("payment_pagos360.payment_provider_pagos360")
        cls.provider.write({"active": True, "is_live": False, "pagos360_debit_execution_days": 5})
        cls.tx = cls.env["payment.transaction"].create(
            {
                "provider_id": cls.provider.id,
                "payment_method_id": cls.env.ref("payment_pagos360.payment_method_pagos360").id,
                "operation": "offline",
                "amount": 1000.0,
                "currency_id": cls.env.company.currency_id.id,
                "partner_id": cls.env["res.partner"].create({"name": "Test Buyer"}).id,
            }
        )

    def test_debit_due_date_uses_the_execution_days(self):
        """Also when the debit follows the invoice due date and there is no invoice due in the future."""
        for use_invoice_due in (False, True):
            with self.subTest(use_invoice_due=use_invoice_due):
                self.provider.pagos360_debit_use_invoice_due = use_invoice_due
                with patch.object(
                    type(self.tx), "_pagos360_next_business_day", return_value="2026-12-04T00:00:00-03:00"
                ) as next_business_day:
                    self.tx.get_debit_due_date()
                self.assertEqual(next_business_day.call_args.kwargs["days"], 5)

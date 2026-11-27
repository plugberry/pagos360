from unittest.mock import patch

from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestRenderingValues(TransactionCase):
    """A redirect payment with a method Pagos360 does not handle is refused with a clear error."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.provider = cls.env.ref("payment_pagos360.payment_provider_pagos360")
        cls.provider.write({"active": True, "is_live": False})
        cls.partner = cls.env["res.partner"].create({"name": "Test Buyer"})

    def test_unsupported_payment_method_is_refused(self):
        method = self.env.ref("payment_pagos360.payment_method_pagos360").copy({"code": "pagos360_other"})
        tx = self.env["payment.transaction"].create(
            {
                "provider_id": self.provider.id,
                "payment_method_id": method.id,
                "operation": "online_redirect",
                "reference": "TEST/UNSUPPORTED/METHOD",
                "amount": 500.0,
                "currency_id": self.env.company.currency_id.id,
                "partner_id": self.partner.id,
            }
        )
        with (
            patch.object(type(self.provider), "_pagos360_make_request", return_value={"id": 1}),
            patch.object(type(tx), "_pagos360_prepare_preference_request_payload", return_value={}),
            self.assertRaises(ValidationError),
        ):
            # The core flow calls it with payment_safe_write, as in _get_processing_values.
            tx.with_context(payment_safe_write=True)._get_specific_rendering_values({})

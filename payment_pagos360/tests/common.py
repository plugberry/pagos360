from unittest.mock import patch

from odoo.tests.common import TransactionCase

WEBHOOK_TOKEN = "the-real-token"


class Pagos360Common:
    """Shared setup for the bug-reproduction suites.

    Every API call is mocked: these tests never reach Pagos360.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.provider = cls.env.ref("payment_pagos360.payment_provider_pagos360")
        values = {"state": "test"}
        # The webhook secret arrives with the webhook authentication fix, still under review.
        if "pagos360_webhook_token" in cls.env["payment.provider"]._fields:
            values["pagos360_webhook_token"] = WEBHOOK_TOKEN
        cls.provider.write(values)
        cls.payment_method = cls.env.ref("payment_pagos360.payment_method_pagos360")
        cls.currency = cls.env.company.currency_id
        cls.partner = cls.env["res.partner"].create({"name": "Test Buyer"})

    def _make_tx(self, reference, amount=500.0, **vals):
        return self.env["payment.transaction"].create(
            {
                "provider_id": self.provider.id,
                "payment_method_id": self.payment_method.id,
                "operation": "online_redirect",
                "reference": reference,
                "amount": amount,
                "currency_id": self.currency.id,
                "partner_id": self.partner.id,
                **vals,
            }
        )

    def _make_token(self, adhesion_type="adhesion", **vals):
        return self.env["payment.token"].create(
            {
                "provider_id": self.provider.id,
                "payment_method_id": self.payment_method.id,
                "partner_id": self.partner.id,
                "provider_ref": "63897",
                "payment_details": "Test token",
                "pagos360_adhesion_type": adhesion_type,
                **vals,
            }
        )

    def _patch_api(self, **kwargs):
        """Patch the single point where the module talks to Pagos360."""
        return patch.object(type(self.provider), "_pagos360_make_request", **kwargs)


class Pagos360TransactionCase(Pagos360Common, TransactionCase):
    pass

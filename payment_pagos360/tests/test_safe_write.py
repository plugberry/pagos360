from unittest.mock import patch

from odoo.addons.payment import utils as payment_utils
from odoo.tests.common import HttpCase, tagged

REQUEST_ID = "109019359"
DEBIT_ID = "121048091"


@tagged("post_install", "-at_install")
class TestSafeWrite(HttpCase):
    """In 20 a direct write on a transaction raises: these paths must go through `_record`."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.provider = cls.env.ref("payment_pagos360.payment_provider_pagos360")
        cls.provider.write({"active": True, "is_live": False})
        cls.partner = cls.env["res.partner"].create({"name": "Test Buyer"})
        cls.currency = cls.env.company.currency_id

    def _make_tx(self, method_xmlid, **values):
        return self.env["payment.transaction"].create(
            {
                "provider_id": self.provider.id,
                "payment_method_id": self.env.ref(method_xmlid).id,
                "operation": "online_redirect",
                "amount": 100.0,
                "currency_id": self.currency.id,
                "partner_id": self.partner.id,
                **values,
            }
        )

    def _run_processing(self):
        """Apply the payment data queued by `_record`, as the processing cron does."""
        for payment_data in self.env["payment.data"].sudo().search([("errored", "=", False)]):
            payment_data.transaction_id.with_context(payment_safe_write=True)._process(payment_data.payload)
            payment_data.unlink()

    def _open_coupon(self, tx, route):
        request = {
            "id": REQUEST_ID,
            "state": "pending",
            "external_reference": tx.reference,
            "pdf_url": "https://example.com/coupon.pdf",
            "barcode": "1234",
        }
        access_token = payment_utils.generate_access_token(tx.partner_id.id, tx.amount, tx.currency_id.id, env=self.env)
        with patch.object(
            type(self.provider), "_pagos360_make_request", autospec=True, return_value={"data": [request]}
        ):
            response = self.url_open(
                f"/payment/pagos360/{route}?tx_id={tx.id}&access_token={access_token}", allow_redirects=False
            )
            self._run_processing()
        return response

    def test_pagofacil_coupon(self):
        tx = self._make_tx("payment_pagos360.payment_method_pagofacil")
        response = self._open_coupon(tx, "pagofacil")
        self.assertIn(response.status_code, (302, 303))
        self.assertEqual(response.headers["Location"], "https://example.com/coupon.pdf")
        self.assertEqual(tx.state, "pending")
        self.assertEqual(tx.provider_reference, REQUEST_ID)

    def test_rapipago_coupon(self):
        tx = self._make_tx("payment_pagos360.payment_method_rapipago")
        response = self._open_coupon(tx, "rapipago")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(tx.state, "pending")
        self.assertEqual(tx.provider_reference, REQUEST_ID)

    def test_cancel_debit(self):
        token = self.env["payment.token"].create(
            {
                "provider_id": self.provider.id,
                "payment_method_id": self.env.ref("payment_pagos360.payment_method_pagos360").id,
                "partner_id": self.partner.id,
                "provider_ref": "63897",
                "pagos360_adhesion_type": "adhesion",
                "payment_details": "CBU ****",
            }
        )
        tx = self._make_tx(
            "payment_pagos360.payment_method_pagos360",
            operation="offline",
            token_id=token.id,
            provider_reference=DEBIT_ID,
        )
        tx.with_context(payment_safe_write=True)._set_pending()
        debit = {"id": DEBIT_ID, "state": "pending", "external_reference": tx.reference}

        def fake_request(provider, endpoint, data=None, method="POST"):
            if method == "PUT":
                debit["state"] = "canceled"
                return dict(debit)
            if endpoint.startswith("/debit-request?"):
                return {"data": [dict(debit)]}
            return dict(debit)

        with patch.object(type(self.provider), "_pagos360_make_request", autospec=True, side_effect=fake_request):
            tx.pagos360_cancel_transactions()
            self._run_processing()
        self.assertEqual(tx.state, "cancel")

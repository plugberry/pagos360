from unittest.mock import patch

from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestDebitReference(TransactionCase):
    """Paid debits must resolve to the debit itself, not to its adhesion, and be dated
    with the paid_at of the payload."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.provider = cls.env.ref("payment_pagos360.payment_provider_pagos360")
        cls.provider.write({"state": "test"})
        cls.payment_method = cls.env.ref("payment_pagos360.payment_method_pagos360")
        cls.currency = cls.env.company.currency_id
        cls.partner = cls.env["res.partner"].create({"name": "Test Buyer"})

    def _make_token(self, adhesion_type):
        return self.env["payment.token"].create(
            {
                "provider_id": self.provider.id,
                "payment_method_id": self.payment_method.id,
                "partner_id": self.partner.id,
                "provider_ref": "63897",
                "pagos360_adhesion_type": adhesion_type,
                "payment_details": "VISA **** - 5976",
            }
        )

    def _make_tx(self, token, amount=245402.33):
        return self.env["payment.transaction"].create(
            {
                "provider_id": self.provider.id,
                "payment_method_id": self.payment_method.id,
                "operation": "offline",
                "amount": amount,
                "currency_id": self.currency.id,
                "partner_id": self.partner.id,
                "token_id": token.id,
            }
        )

    # --- paid card debit resolves and dates the payment from the payload ------------

    def test_paid_card_debit_sets_done_and_effective_date_from_payload(self):
        tx = self._make_tx(self._make_token("card_adhesion"))
        tx._set_pending()
        payload = {
            "entity_name": "card_debit_request",
            "entity_id": "121048091",
            "type": "paid",
            "payload": {
                "id": "121048091",
                "state": "paid",
                "external_reference": tx.reference,
                "amount": tx.amount,
                "request_result": [{"paid_at": "2026-07-15T12:33:10-03:00"}],
            },
        }
        # The paid_at must come from the payload; no API round-trip needed.
        with patch.object(type(self.provider), "_pagos360_make_request", side_effect=AssertionError("API hit")):
            tx._apply_updates(payload)
        self.assertEqual(tx.state, "done")
        self.assertEqual(str(tx.pagos360_effective_payment_date), "2026-07-15")

    # --- a debit webhook carrying the adhesion's reference resolves to the debit ----

    def test_debit_webhook_with_adhesion_reference_matches_the_debit(self):
        # Older notifications wrote the debit id on the adhesion, so both shapes must resolve.
        # Subtests share the transaction, so each one uses its own debit id.
        for debit_id, adhesion_provider_reference in [(127431533, False), (127431534, "127431534")]:
            with self.subTest(adhesion_provider_reference=adhesion_provider_reference):
                token = self._make_token("adhesion")
                adhesion_tx = self.env["payment.transaction"].create(
                    {
                        "provider_id": self.provider.id,
                        "payment_method_id": self.payment_method.id,
                        "operation": "validation",
                        "amount": 0.0,
                        "currency_id": self.currency.id,
                        "partner_id": self.partner.id,
                        "token_id": token.id,
                        "provider_reference": adhesion_provider_reference,
                        "state": "done",
                    }
                )
                debit_tx = self._make_tx(token)
                debit_tx.write({"provider_reference": str(debit_id)})
                debit_tx._set_pending()
                # Shape of a real notification: debits have no external_reference of their own,
                # so Pagos360 sends the adhesion's one.
                payment_data = {
                    "entity_name": "debit_request",
                    "type": "paid",
                    "entity_id": debit_id,
                    "payload": {
                        "id": debit_id,
                        "request_result_id": 81467816,
                        "external_reference": adhesion_tx.reference,
                    },
                }
                Transaction = self.env["payment.transaction"]
                self.assertEqual(Transaction._search_by_reference("pagos360", payment_data), debit_tx)
                with patch.object(type(self.provider), "_pagos360_make_request", return_value={}):
                    Transaction._process("pagos360", payment_data)
                self.assertEqual(debit_tx.state, "done")
                self.assertEqual(adhesion_tx.state, "done")
                self.assertEqual(adhesion_tx.provider_reference, adhesion_provider_reference)

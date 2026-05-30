from unittest.mock import patch

from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestAdhesion(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.provider = cls.env.ref("payment_pagos360.payment_provider_pagos360")
        cls.provider.write({"state": "test"})
        cls.partner = cls.env["res.partner"].create({"name": "Test Buyer"})
        cls.payment_method = cls.env.ref("payment_pagos360.payment_method_pagos360")
        cls.currency = cls.env.company.currency_id

    # --- _get_specific_create_values --------------------------------------------------

    def _build_values(self, operation, amount=100.0, tokenize=False):
        return {
            "provider_id": self.provider.id,
            "payment_method_id": self.payment_method.id,
            "operation": operation,
            "amount": amount,
            "currency_id": self.currency.id,
            "partner_id": self.partner.id,
            "tokenize": tokenize,
        }

    def test_create_values_flips_redirect_when_tokenize(self):
        """Online payment + tokenize=True (subscription, or 'save my details') → adhesion flow."""
        res = self.env["payment.transaction"]._get_specific_create_values(
            "pagos360", self._build_values("online_redirect", amount=250.0, tokenize=True)
        )
        self.assertEqual(res["operation"], "validation")
        self.assertTrue(res["tokenize"])
        self.assertEqual(res["amount"], 0.0)
        self.assertEqual(res["pagos360_child_amount"], 250.0)

    def test_create_values_noop_when_not_tokenize(self):
        """One-shot payment (tokenize falsy) stays a plain payment-request."""
        res = self.env["payment.transaction"]._get_specific_create_values(
            "pagos360", self._build_values("online_redirect", amount=250.0, tokenize=False)
        )
        self.assertNotIn("operation", res)
        self.assertNotIn("pagos360_child_amount", res)

    def test_create_values_noop_for_token_operation(self):
        res = self.env["payment.transaction"]._get_specific_create_values(
            "pagos360", self._build_values("online_token", amount=250.0, tokenize=True)
        )
        self.assertNotIn("operation", res)

    def test_create_values_noop_for_other_provider_code(self):
        res = self.env["payment.transaction"]._get_specific_create_values(
            "stripe", self._build_values("online_redirect", amount=250.0, tokenize=True)
        )
        self.assertNotIn("operation", res)

    # --- _is_tokenization_required ----------------------------------------------------

    def test_tokenization_not_required_when_adhesion_off(self):
        """Flag off short-circuits to False even if super (subscription) would require it."""
        self.provider.pagos360_adhesion_on_subscription = False
        with patch(
            "odoo.addons.payment.models.payment_provider.PaymentProvider._is_tokenization_required",
            return_value=True,
        ):
            self.assertFalse(self.provider._is_tokenization_required())

    def test_tokenization_delegates_to_super_when_adhesion_on(self):
        """Flag on delegates to super (subscription modules can still force it)."""
        self.provider.pagos360_adhesion_on_subscription = True
        with patch(
            "odoo.addons.payment.models.payment_provider.PaymentProvider._is_tokenization_required",
            return_value=True,
        ):
            self.assertTrue(self.provider._is_tokenization_required())

    def test_tokenization_multi_record_is_safe(self):
        """The multi-record call from _get_compatible_providers must not raise."""
        others = self.env["payment.provider"].search([("code", "!=", "pagos360")], limit=1)
        providers = self.provider | others
        # Should delegate to super (len(self) != 1) without touching pagos360 fields.
        self.assertFalse(providers._is_tokenization_required())

    # --- _pagos360_spawn_child_charge -------------------------------------------------

    def _make_validation_tx(self, child_amount=500.0, with_token=True):
        token = self.env.ref("payment_pagos360.pagos360_tests_token") if with_token else False
        tx = self.env["payment.transaction"].create(
            {
                "provider_id": self.provider.id,
                "payment_method_id": self.payment_method.id,
                "operation": "validation",
                "amount": 0.0,
                "currency_id": self.currency.id,
                "partner_id": self.partner.id,
                "pagos360_child_amount": child_amount,
                "tokenize": False,
                "token_id": token.id if token else False,
            }
        )
        tx._set_done()
        return tx

    def test_spawn_creates_child_when_conditions_met(self):
        tx = self._make_validation_tx(child_amount=750.0)
        with patch.object(
            type(self.env["payment.transaction"]), "_charge_with_token", return_value=None
        ) as mock_charge:
            tx._pagos360_spawn_child_charge()
        children = tx.child_transaction_ids
        self.assertEqual(len(children), 1)
        self.assertEqual(children.operation, "online_token")
        self.assertEqual(children.amount, 750.0)
        self.assertEqual(children.token_id, tx.token_id)
        self.assertEqual(children.source_transaction_id, tx)
        mock_charge.assert_called_once()

    def test_spawn_noop_without_token(self):
        tx = self._make_validation_tx(with_token=False)
        tx._pagos360_spawn_child_charge()
        self.assertFalse(tx.child_transaction_ids)

    def test_spawn_noop_when_child_amount_zero(self):
        """A portal adhesion (no child amount) only creates the token, never a cobro."""
        tx = self._make_validation_tx(child_amount=0.0)
        tx._pagos360_spawn_child_charge()
        self.assertFalse(tx.child_transaction_ids)

    def test_spawn_is_idempotent(self):
        tx = self._make_validation_tx(child_amount=500.0)
        with patch.object(type(self.env["payment.transaction"]), "_charge_with_token", return_value=None):
            tx._pagos360_spawn_child_charge()
            tx._pagos360_spawn_child_charge()
        self.assertEqual(len(tx.child_transaction_ids), 1)

    def test_spawn_noop_on_child_transaction(self):
        parent = self._make_validation_tx(child_amount=500.0)
        with patch.object(type(self.env["payment.transaction"]), "_charge_with_token", return_value=None):
            parent._pagos360_spawn_child_charge()
        child = parent.child_transaction_ids
        # Calling spawn on the child should not create grandchildren.
        child._pagos360_spawn_child_charge()
        self.assertFalse(child.child_transaction_ids)

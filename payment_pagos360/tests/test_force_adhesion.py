from unittest.mock import patch

from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestForceAdhesion(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.provider = cls.env.ref("payment_pagos360.payment_provider_pagos360")
        cls.provider.write({"state": "test"})
        cls.partner = cls.env["res.partner"].create({"name": "Test Buyer"})
        cls.payment_method = cls.env.ref("payment_pagos360.payment_method_pagos360")
        cls.currency = cls.env.company.currency_id

    # --- _get_specific_create_values --------------------------------------------------

    def _build_values(self, operation, amount=100.0):
        return {
            "provider_id": self.provider.id,
            "payment_method_id": self.payment_method.id,
            "operation": operation,
            "amount": amount,
            "currency_id": self.currency.id,
            "partner_id": self.partner.id,
        }

    def test_create_values_flips_redirect_when_force_adhesion_on(self):
        self.provider.pagos360_force_adhesion = True
        res = self.env["payment.transaction"]._get_specific_create_values(
            "pagos360", self._build_values("online_redirect", amount=250.0)
        )
        self.assertEqual(res["operation"], "validation")
        self.assertTrue(res["tokenize"])
        self.assertEqual(res["amount"], 0.0)
        self.assertEqual(res["pagos360_child_amount"], 250.0)

    def test_create_values_noop_when_force_adhesion_off(self):
        self.provider.pagos360_force_adhesion = False
        res = self.env["payment.transaction"]._get_specific_create_values(
            "pagos360", self._build_values("online_redirect", amount=250.0)
        )
        self.assertNotIn("operation", res)
        self.assertNotIn("pagos360_child_amount", res)

    def test_create_values_noop_for_token_operation(self):
        self.provider.pagos360_force_adhesion = True
        res = self.env["payment.transaction"]._get_specific_create_values(
            "pagos360", self._build_values("online_token", amount=250.0)
        )
        self.assertNotIn("operation", res)

    def test_create_values_noop_for_other_provider_code(self):
        self.provider.pagos360_force_adhesion = True
        res = self.env["payment.transaction"]._get_specific_create_values(
            "stripe", self._build_values("online_redirect", amount=250.0)
        )
        self.assertNotIn("operation", res)

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
        self.provider.pagos360_force_adhesion = True
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

    def test_spawn_noop_when_flag_off(self):
        self.provider.pagos360_force_adhesion = False
        tx = self._make_validation_tx()
        with patch.object(
            type(self.env["payment.transaction"]), "_charge_with_token", return_value=None
        ) as mock_charge:
            tx._pagos360_spawn_child_charge()
        self.assertFalse(tx.child_transaction_ids)
        mock_charge.assert_not_called()

    def test_spawn_noop_without_token(self):
        self.provider.pagos360_force_adhesion = True
        tx = self._make_validation_tx(with_token=False)
        tx._pagos360_spawn_child_charge()
        self.assertFalse(tx.child_transaction_ids)

    def test_spawn_noop_when_child_amount_zero(self):
        self.provider.pagos360_force_adhesion = True
        tx = self._make_validation_tx(child_amount=0.0)
        tx._pagos360_spawn_child_charge()
        self.assertFalse(tx.child_transaction_ids)

    def test_spawn_is_idempotent(self):
        self.provider.pagos360_force_adhesion = True
        tx = self._make_validation_tx(child_amount=500.0)
        with patch.object(type(self.env["payment.transaction"]), "_charge_with_token", return_value=None):
            tx._pagos360_spawn_child_charge()
            tx._pagos360_spawn_child_charge()
        self.assertEqual(len(tx.child_transaction_ids), 1)

    def test_spawn_noop_on_child_transaction(self):
        self.provider.pagos360_force_adhesion = True
        parent = self._make_validation_tx(child_amount=500.0)
        with patch.object(type(self.env["payment.transaction"]), "_charge_with_token", return_value=None):
            parent._pagos360_spawn_child_charge()
        child = parent.child_transaction_ids
        # Calling spawn on the child should not create grandchildren.
        child._pagos360_spawn_child_charge()
        self.assertFalse(child.child_transaction_ids)

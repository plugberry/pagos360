from unittest.mock import patch

from odoo.exceptions import ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install")
class TestChildTransaction(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.provider = cls.env["payment.provider"].create(
            {
                "name": "Pagos360 Test",
                "code": "pagos360",
                "state": "test",
                "pagos360_test_api_key": "test-key",
                "pagos360_form_url": "https://pagos360.example.com/form",
            }
        )
        cls.partner = cls.env["res.partner"].create({"name": "Cliente Test"})
        cls.payment_method = cls.env.ref("payment.payment_method_unknown")

    def _create_tx(self, operation, tokenize, amount=1500.0, provider=None, currency=None):
        provider = provider or self.provider
        values = {
            "provider_id": provider.id,
            "payment_method_id": self.payment_method.id,
            "partner_id": self.partner.id,
            "amount": amount,
            "currency_id": (currency or self.env.company.currency_id).id,
            "operation": operation,
            "tokenize": tokenize,
        }
        return self.env["payment.transaction"].create(values)

    # -- Escenario 1: checkout con tokenización, provider con form de adhesión --

    def test_tokenize_with_form_url_converts_to_validation(self):
        tx = self._create_tx("online_redirect", True, amount=1500.0)
        self.assertEqual(tx.operation, "validation")
        self.assertEqual(tx.amount, 0.0)
        self.assertEqual(tx.pagos360_child_amount, 1500.0)
        self.assertEqual(tx.currency_id, self.provider._get_validation_currency())

    # -- La hija debe cobrar en la moneda original del checkout, no en la de validación --

    def test_child_keeps_original_checkout_currency(self):
        other_currency = (
            self.env["res.currency"]
            .with_context(active_test=False)
            .search([("id", "!=", self.env.company.currency_id.id)], limit=1)
        )
        other_currency.active = True
        tx = self._create_tx("online_redirect", True, amount=1500.0, currency=other_currency)
        self.assertEqual(tx.pagos360_child_currency_id, other_currency)
        self.assertNotEqual(tx.currency_id, other_currency)
        with patch.object(type(tx), "_send_payment_request", autospec=True):
            self._sign_validation_tx(tx)
            child = tx.child_transaction_ids.filtered(lambda c: c.operation == "online_token")
            self.assertEqual(child.currency_id, other_currency)

    # -- Escenario 2: checkout sin tokenización --

    def test_no_tokenize_keeps_operation_unchanged(self):
        tx = self._create_tx("online_redirect", False, amount=1500.0)
        self.assertEqual(tx.operation, "online_redirect")
        self.assertEqual(tx.amount, 1500.0)
        self.assertFalse(tx.pagos360_child_amount)

    # -- Escenario 3: provider sin form de adhesión configurado --

    def test_tokenize_without_form_url_keeps_operation_unchanged(self):
        provider = self.provider.copy({"pagos360_form_url": False})
        tx = self._create_tx("online_redirect", True, amount=1500.0, provider=provider)
        self.assertEqual(tx.operation, "online_redirect")
        self.assertEqual(tx.amount, 1500.0)

    def _sign_validation_tx(self, tx):
        with patch.object(type(tx), "_pagos360_tokenize_from_feedback_data", autospec=True) as mock_tokenize:

            def _fake_tokenize(self, notification_data):
                token = self.env["payment.token"].create(
                    {
                        "provider_id": self.provider_id.id,
                        "partner_id": self.partner_id.id,
                        "provider_ref": "adh-1",
                        "payment_details": "test",
                        "payment_method_id": self.payment_method_id.id,
                        "pagos360_adhesion_type": "adhesion",
                        "pagos360_bank": "Test Bank",
                        "pagos360_cbu_number": "0000003100000000000001",
                    }
                )
                self.write({"token_id": token.id, "tokenize": False})

            mock_tokenize.side_effect = _fake_tokenize
            tx._process_notification_data({"entity_name": "adhesion", "entity_id": "999", "type": "signed"})

    # -- Escenario 4: adhesión firmada, spawn exitoso de la hija --

    def test_signed_adhesion_spawns_child_charge(self):
        tx = self._create_tx("online_redirect", True, amount=1500.0)
        with patch.object(type(tx), "_send_payment_request", autospec=True) as mock_send:
            self._sign_validation_tx(tx)
            child = tx.child_transaction_ids.filtered(lambda c: c.operation == "online_token")
            self.assertEqual(len(child), 1)
            self.assertEqual(child.amount, 1500.0)
            self.assertEqual(child.source_transaction_id, tx)
            self.assertEqual(child.token_id, tx.token_id)
            mock_send.assert_called_once()

    # -- Escenario 5: doble notificación de firma (idempotencia) --

    def test_double_signed_notification_is_idempotent(self):
        tx = self._create_tx("online_redirect", True, amount=1500.0)
        with patch.object(type(tx), "_send_payment_request", autospec=True):
            self._sign_validation_tx(tx)
            tx._pagos360_spawn_child_charge()
            children = tx.child_transaction_ids.filtered(lambda c: c.operation == "online_token")
            self.assertEqual(len(children), 1)

    # -- Escenario 6: pagos360_child_amount vacío --

    def test_empty_child_amount_skips_spawn(self):
        tx = self._create_tx("online_redirect", True, amount=1500.0)
        tx.pagos360_child_amount = 0.0
        self._sign_validation_tx(tx)
        self.assertFalse(tx.child_transaction_ids)
        self.assertEqual(tx.state, "done")

    # -- Escenario 7: falla el cobro de la hija --

    def test_child_charge_failure_does_not_affect_parent(self):
        tx = self._create_tx("online_redirect", True, amount=1500.0)
        with patch.object(type(tx), "_send_payment_request", autospec=True, side_effect=Exception("boom")):
            self._sign_validation_tx(tx)
        self.assertEqual(tx.state, "done")
        child = tx.child_transaction_ids.filtered(lambda c: c.operation == "online_token")
        self.assertEqual(len(child), 1)
        self.assertEqual(child.state, "error")

    # -- Token archivado antes del spawn --

    def test_spawn_raises_when_token_archived(self):
        tx = self._create_tx("online_redirect", True, amount=1500.0)
        token = self.env["payment.token"].create(
            {
                "provider_id": self.provider.id,
                "partner_id": self.partner.id,
                "provider_ref": "adh-2",
                "payment_details": "test",
                "payment_method_id": self.payment_method.id,
                "pagos360_adhesion_type": "adhesion",
            }
        )
        tx.write({"token_id": token.id, "tokenize": False, "state": "done"})
        token.with_context(is_notification=True).write({"active": False})
        with self.assertRaises(ValidationError):
            tx._pagos360_spawn_child_charge()

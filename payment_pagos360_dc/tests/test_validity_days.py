from datetime import date, timedelta
from unittest.mock import patch

from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.addons.payment_pagos360.models.payment_transaction import (
    PaymentTransaction as Pagos360Transaction,
)
from odoo.tests import tagged


def _fake_next_business_day(self, due_date, days=3):
    """Stub del validador de días hábiles de Pagos360: sólo suma días de calendario.

    La lógica real de días hábiles vive del lado de la API de Pagos360, fuera de este
    módulo; estos tests validan la composición de get_debit_due_date(), no ese algoritmo.
    """
    return (due_date + timedelta(days=days)).isoformat()


@tagged("post_install", "-at_install")
class TestValidityDays(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.provider = cls.env["payment.provider"].create(
            {
                "name": "Pagos360 Test",
                "code": "pagos360",
                "state": "test",
                "pagos360_test_api_key": "test-key",
            }
        )
        cls.payment_method = cls.env.ref("payment.payment_method_unknown")

    def _create_transaction(self, invoices=None):
        values = {
            "provider_id": self.provider.id,
            "payment_method_id": self.payment_method.id,
            "partner_id": self.partner_a.id,
            "amount": 1000.0,
            "currency_id": self.company_data["currency"].id,
        }
        if invoices:
            values["invoice_ids"] = [(6, 0, invoices.ids)]
        return self.env["payment.transaction"].create(values)

    def _create_posted_invoice(self, invoice_date_due):
        invoice = self.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "partner_id": self.partner_a.id,
                "invoice_date": date.today(),
                "invoice_line_ids": [(0, 0, {"name": "test line", "price_unit": 1000.0, "quantity": 1})],
            }
        )
        invoice.action_post()
        # `invoice_date_due` es compute+store (depende de `needed_terms`, que depende del
        # payment term del partner) — action_post() la recalcula y pisa cualquier valor
        # seteado antes. Se fuerza después para no depender del payment term de `partner_a`.
        invoice.invoice_date_due = invoice_date_due
        return invoice

    # -- Escenario: cupón de efectivo usa pagos360_coupon_validity_days --

    def test_coupon_due_values_uses_coupon_validity_days(self):
        self.provider.pagos360_coupon_validity_days = 21
        tx = self._create_transaction()
        due, total = tx.get_coupon_due_values()
        self.assertEqual(due.date(), date.today() + timedelta(days=21))
        self.assertEqual(total, tx.amount)

    # -- Escenario 4 (adaptado): toggle inactivo --

    @patch.object(Pagos360Transaction, "_pagos360_next_business_day", _fake_next_business_day)
    def test_toggle_off_uses_debit_execution_days(self):
        self.provider.pagos360_debit_use_invoice_due = False
        self.provider.pagos360_debit_execution_days = 5
        tx = self._create_transaction()
        result = date.fromisoformat(tx.get_debit_due_date()[:10])
        self.assertEqual(result, date.today() + timedelta(days=5))

    # -- Escenario 1 (adaptado): toggle activo, invoice_due posterior al piso de 3 días hábiles --

    @patch.object(Pagos360Transaction, "_pagos360_next_business_day", _fake_next_business_day)
    def test_toggle_on_invoice_due_after_floor(self):
        self.provider.pagos360_debit_use_invoice_due = True
        invoice_due = date.today() + timedelta(days=20)
        invoice = self._create_posted_invoice(invoice_due)
        tx = self._create_transaction(invoices=invoice)
        result = date.fromisoformat(tx.get_debit_due_date()[:10])
        self.assertEqual(result, invoice_due)

    # -- Escenario 2 (adaptado): toggle activo, invoice_due dentro del piso --

    @patch.object(Pagos360Transaction, "_pagos360_next_business_day", _fake_next_business_day)
    def test_toggle_on_invoice_due_within_floor(self):
        self.provider.pagos360_debit_use_invoice_due = True
        invoice_due = date.today() + timedelta(days=1)
        invoice = self._create_posted_invoice(invoice_due)
        tx = self._create_transaction(invoices=invoice)
        result = date.fromisoformat(tx.get_debit_due_date()[:10])
        self.assertEqual(result, date.today() + timedelta(days=3))

    # -- Escenario 3 (adaptado): toggle activo, sin facturas elegibles --

    @patch.object(Pagos360Transaction, "_pagos360_next_business_day", _fake_next_business_day)
    def test_toggle_on_without_eligible_invoices(self):
        self.provider.pagos360_debit_use_invoice_due = True
        tx = self._create_transaction()
        result = date.fromisoformat(tx.get_debit_due_date()[:10])
        self.assertEqual(result, date.today() + timedelta(days=3))

    # -- Escenario 5 (adaptado): toggle activo, factura con vencimiento pasado o igual a hoy --

    @patch.object(Pagos360Transaction, "_pagos360_next_business_day", _fake_next_business_day)
    def test_toggle_on_invoice_due_in_the_past(self):
        self.provider.pagos360_debit_use_invoice_due = True
        invoice = self._create_posted_invoice(date.today())
        tx = self._create_transaction(invoices=invoice)
        result = date.fromisoformat(tx.get_debit_due_date()[:10])
        self.assertEqual(result, date.today() + timedelta(days=3))

    # -- Escenario 6 (adaptado): toggle activo, múltiples facturas futuras --

    @patch.object(Pagos360Transaction, "_pagos360_next_business_day", _fake_next_business_day)
    def test_toggle_on_multiple_future_invoices(self):
        self.provider.pagos360_debit_use_invoice_due = True
        invoice_1 = self._create_posted_invoice(date.today() + timedelta(days=10))
        invoice_2 = self._create_posted_invoice(date.today() + timedelta(days=25))
        tx = self._create_transaction(invoices=invoice_1 | invoice_2)
        result = date.fromisoformat(tx.get_debit_due_date()[:10])
        self.assertEqual(result, date.today() + timedelta(days=10))

    # -- pagos360_cut_days: constraint de rango 1-28 --

    def test_cut_days_constraint_rejects_invalid_day(self):
        with self.assertRaises(Exception):
            self.provider.pagos360_cut_days = "10,29"

    # -- pagos360_debit_execution_days: constraint de mínimo 3 --

    def test_debit_execution_days_constraint_rejects_below_minimum(self):
        with self.assertRaises(Exception):
            self.provider.pagos360_debit_execution_days = 2

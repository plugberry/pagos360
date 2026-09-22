from odoo.tests.common import tagged

from .common import Pagos360TransactionCase

REFERENCE = "TEST/BUG/DUEDATE"


@tagged("post_install", "-at_install")
class TestDebitExecutionDays(Pagos360TransactionCase):
    """The configured execution days must be honoured in both branches.

    Dado un proveedor configurado con 7 días de ejecución del débito y la opción de debitar
    al vencimiento de la factura activa, cuando la transacción no tiene facturas con
    vencimiento futuro, entonces el piso se calcula con los 7 días configurados.

    Bug: esa rama pide `days=3` fijo, así que la configuración del cliente se ignora sin
    ningún aviso.

    Se demuestra en rojo: hoy se pide 3; pasa cuando use `pagos360_debit_execution_days`.
    """

    def _capture_next_business_day(self, tx):
        captured = {}

        def _request(endpoint, data=None, method="POST"):
            if endpoint == "validator/next-business-day":
                captured.update(data["next_business_day"])
                return "2026-01-01 00:00:00"
            return {}

        with self._patch_api(side_effect=_request):
            tx.get_debit_due_date()
        return captured

    def test_configured_days_are_used_when_debiting_on_the_invoice_due_date(self):
        self.provider.write({"pagos360_debit_execution_days": 7, "pagos360_debit_use_invoice_due": True})
        tx = self._make_tx(REFERENCE)  # no invoices, so the floor is what gets requested
        self.assertEqual(self._capture_next_business_day(tx).get("days"), 7)

    def test_configured_days_are_used_without_the_invoice_due_option(self):
        self.provider.write({"pagos360_debit_execution_days": 7, "pagos360_debit_use_invoice_due": False})
        tx = self._make_tx(REFERENCE + "/OFF")
        self.assertEqual(self._capture_next_business_day(tx).get("days"), 7)

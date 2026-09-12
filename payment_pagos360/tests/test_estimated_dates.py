from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestPagos360EstimatedDates(TransactionCase):
    """Fechas estimadas de cobro/acreditación (tarea #69090).

    Los payloads reproducen la forma real de las respuestas de Pagos360 relevada en el
    Paso 0 (§1.3): cada intento de cobro se lista en ``request_result`` y tanto los
    ``rejected`` como los ``collected`` traen ``available_at``/``paid_at``.
    """

    def _tx(self):
        # Registro en memoria (NewId): permite escribir/leer los campos Date sin crear
        # una transacción completa. El cómputo no depende de otros campos del registro.
        return self.env["payment.transaction"].new({})

    def test_collected_result_skips_rejected(self):
        """Debe elegir el result `collected`, nunca el `rejected` (que igual trae fechas)."""
        payload = {
            "request_result": [
                {"type": "rejected_debit_request_result", "available_at": "2026-07-24T10:15:43-03:00"},
                {
                    "type": "collected_debit_request_result",
                    "available_at": "2026-07-24T00:00:00-03:00",
                    "paid_at": "2026-07-22T00:00:00-03:00",
                },
            ]
        }
        result = self.env["payment.transaction"]._pagos360_get_collected_result(payload)
        self.assertEqual(result.get("type"), "collected_debit_request_result")

    def test_no_collected_result(self):
        """Si sólo hay results rechazados, no hay result collected."""
        payload = {"request_result": [{"type": "rejected_debit_request_result", "available_at": "2026-07-24"}]}
        self.assertFalse(self.env["payment.transaction"]._pagos360_get_collected_result(payload))

    def test_cbu_paid(self):
        """CBU pagado: settlement = available_at del collected; cobro = paid_at real (US3)."""
        payload = {
            "first_due_date": "2026-07-22T00:00:00-03:00",
            "request_result": [
                {
                    "type": "collected_debit_request_result",
                    "available_at": "2026-07-24T00:00:00-03:00",
                    "paid_at": "2026-07-22T00:00:00-03:00",
                }
            ],
        }
        tx = self._tx()
        tx._pagos360_compute_estimated_dates("debit_request", payload)
        self.assertEqual(str(tx.pagos360_estimated_settlement_date), "2026-07-24")
        self.assertEqual(str(tx.pagos360_estimated_charge_date), "2026-07-22")

    def test_cbu_scheduled_before_payment(self):
        """CBU recién creado (sin cobrar): cobro = first_due_date que enviamos; sin settlement."""
        payload = {"first_due_date": "2026-07-22T00:00:00-03:00"}
        tx = self._tx()
        tx._pagos360_compute_estimated_dates("debit_request", payload)
        self.assertEqual(str(tx.pagos360_estimated_charge_date), "2026-07-22")
        self.assertFalse(tx.pagos360_estimated_settlement_date)

    def test_tc_before_payment_leaves_charge_empty(self):
        """TC sin cobrar: Pagos360 no expone el día -> cobro VACÍO (no inventar cut_days)."""
        payload = {"month": 7, "year": 2026}
        tx = self._tx()
        tx._pagos360_compute_estimated_dates("card_debit_request", payload)
        self.assertFalse(tx.pagos360_estimated_charge_date)
        self.assertFalse(tx.pagos360_estimated_settlement_date)

    def test_tc_paid(self):
        """TC pagado: settlement = available_at; cobro = paid_at real (único dato de cobro)."""
        payload = {
            "month": 7,
            "year": 2026,
            "request_result": [
                {
                    "type": "collected_card_debit_request_result",
                    "available_at": "2026-08-04T00:00:00-03:00",
                    "paid_at": "2026-07-22T11:40:30-03:00",
                }
            ],
        }
        tx = self._tx()
        tx._pagos360_compute_estimated_dates("card_debit_request", payload)
        self.assertEqual(str(tx.pagos360_estimated_settlement_date), "2026-08-04")
        self.assertEqual(str(tx.pagos360_estimated_charge_date), "2026-07-22")

    def test_coupon_paid_first_due_date_is_not_charge(self):
        """Cupón: settlement = available_at; cobro = paid_at real; NUNCA el first_due_date
        (que en el cupón es el vencimiento, no una fecha de cobro)."""
        payload = {
            "first_due_date": "2026-07-26T00:00:00-03:00",
            "request_result": [
                {
                    "type": "collected_payment_request_result",
                    "available_at": "2026-07-30T00:00:00-03:00",
                    "paid_at": "2026-07-11T11:46:13-03:00",
                }
            ],
        }
        tx = self._tx()
        tx._pagos360_compute_estimated_dates("payment_request", payload)
        self.assertEqual(str(tx.pagos360_estimated_settlement_date), "2026-07-30")
        self.assertEqual(str(tx.pagos360_estimated_charge_date), "2026-07-11")
        self.assertNotEqual(str(tx.pagos360_estimated_charge_date), "2026-07-26")

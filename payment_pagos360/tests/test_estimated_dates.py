from odoo import fields
from odoo.tests import TransactionCase, tagged

# Intentos con la forma real de Pagos360 (Paso 0, §1.3): tanto el rechazado como el
# cobrado traen available_at/paid_at; sólo el "collected" refleja el cobro real.
REJECTED = {"type": "rejected_debit_request_result", "available_at": "2026-07-24T10:15:43-03:00"}
COLLECTED = {
    "type": "collected_debit_request_result",
    "available_at": "2026-07-24T00:00:00-03:00",
    "paid_at": "2026-07-22T00:00:00-03:00",
}


@tagged("post_install", "-at_install")
class TestPagos360EstimatedDates(TransactionCase):
    """Fechas estimadas de cobro/acreditación (#69090)."""

    # (descripción, entity_name, payload, cobro esperado, acreditación esperada)
    CASES = [
        (
            "CBU pagado: toma el collected, no el rejected",
            "debit_request",
            {"first_due_date": "2026-07-22T00:00:00-03:00", "request_result": [REJECTED, COLLECTED]},
            "2026-07-22",
            "2026-07-24",
        ),
        (
            "CBU sin cobrar: cobro = first_due_date que enviamos",
            "debit_request",
            {"first_due_date": "2026-07-22T00:00:00-03:00"},
            "2026-07-22",
            False,
        ),
        (
            "TC sin cobrar: la API no expone el día -> vacío (no inventar cut_days)",
            "card_debit_request",
            {"month": 7, "year": 2026},
            False,
            False,
        ),
        (
            "TC pagado: cobro = paid_at real",
            "card_debit_request",
            {
                "request_result": [
                    {
                        "type": "collected_card_debit_request_result",
                        "available_at": "2026-08-04T00:00:00-03:00",
                        "paid_at": "2026-07-22T11:40:30-03:00",
                    }
                ]
            },
            "2026-07-22",
            "2026-08-04",
        ),
        (
            "Cupón: cobro = paid_at real, nunca el first_due_date (vencimiento)",
            "payment_request",
            {
                "first_due_date": "2026-07-26T00:00:00-03:00",
                "request_result": [
                    {
                        "type": "collected_payment_request_result",
                        "available_at": "2026-07-30T00:00:00-03:00",
                        "paid_at": "2026-07-11T11:46:13-03:00",
                    }
                ],
            },
            "2026-07-11",
            "2026-07-30",
        ),
        (
            "Sólo rechazado: se ignoran su available_at/paid_at",
            "payment_request",
            {
                "request_result": [
                    {"type": "rejected_payment_request_result", "available_at": "2026-07-24", "paid_at": "2026-07-22"}
                ]
            },
            False,
            False,
        ),
    ]

    def test_estimated_dates(self):
        for desc, entity_name, payload, charge, settlement in self.CASES:
            with self.subTest(desc):
                # NewId: escribir/leer los campos Date sin crear una transacción completa.
                tx = self.env["payment.transaction"].new({})
                tx._pagos360_compute_estimated_dates(entity_name, payload)
                self.assertEqual(tx.pagos360_estimated_charge_date, fields.Date.to_date(charge) if charge else False)
                self.assertEqual(
                    tx.pagos360_estimated_settlement_date, fields.Date.to_date(settlement) if settlement else False
                )

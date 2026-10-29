from odoo import fields
from odoo.tests import TransactionCase, tagged

# Every attempt is listed in request_result, and rejected ones also carry dates
REJECTED = {
    "type": "rejected_debit_request_result",
    "available_at": "2026-07-24T10:15:43-03:00",
    "paid_at": "2026-07-15T00:00:00-03:00",
}
COLLECTED = {
    "type": "collected_debit_request_result",
    "available_at": "2026-07-24T00:00:00-03:00",
    "paid_at": "2026-07-22T00:00:00-03:00",
}


@tagged("post_install", "-at_install")
class TestPagos360Dates(TransactionCase):
    def test_paid_at_prefers_collected_result(self):
        Transaction = self.env["payment.transaction"]
        for data in ([REJECTED, COLLECTED], {"request_result": [REJECTED, COLLECTED]}):
            self.assertEqual(Transaction._pagos360_extract_paid_at(data), COLLECTED["paid_at"])

    def test_estimated_dates(self):
        # (case, entity_name, payload, expected charge, expected settlement)
        cases = [
            (
                "CBU scheduled: charge is the execution date we sent",
                "debit_request",
                {"first_due_date": "2026-07-20T00:00:00-03:00"},
                "2026-07-20",
                False,
            ),
            (
                "CBU collected: the real charge replaces the scheduled one",
                "debit_request",
                {"first_due_date": "2026-07-20T00:00:00-03:00", "request_result": [REJECTED, COLLECTED]},
                "2026-07-22",
                "2026-07-24",
            ),
            (
                "CBU on send: the debit response is processed as adhesion",
                "adhesion",
                {"first_due_date": "2026-07-20T00:00:00-03:00"},
                "2026-07-20",
                False,
            ),
            (
                "Card scheduled: Pagos360 does not inform the day, so it stays empty",
                "card_debit_request",
                {"month": 7, "year": 2026},
                False,
                False,
            ),
            (
                "Coupon: first_due_date is the coupon expiry, not a charge date",
                "payment_request",
                {"first_due_date": "2026-07-26T00:00:00-03:00"},
                False,
                False,
            ),
            (
                "Only rejected attempts: their dates are ignored",
                "debit_request",
                {"request_result": [REJECTED]},
                False,
                False,
            ),
        ]
        for case, entity_name, payload, charge, settlement in cases:
            with self.subTest(case):
                tx = self.env["payment.transaction"].new({})
                tx._pagos360_set_estimated_dates(entity_name, payload)
                self.assertEqual(tx.pagos360_estimated_charge_date, fields.Date.to_date(charge) or False)
                self.assertEqual(tx.pagos360_estimated_settlement_date, fields.Date.to_date(settlement) or False)

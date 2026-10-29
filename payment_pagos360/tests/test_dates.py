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

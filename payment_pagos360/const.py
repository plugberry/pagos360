API_URL = "https://api.pagos360.com"
API_TEST_URL = "https://api.sandbox.pagos360.com"

HANDLED_WEBHOOK_EVENTS = {
    'payment_request.expired': 30,
    'payment_request.paid': 31,
    'payment_request.refunded': 42,
    'payment_request.rejected': 48,

    'adhesion.canceled': 2,
    'adhesion.signed': 25,

    'card_adhesion.signed': 38,
    'card_adhesion.canceled': 37,

    'debit_request.canceled' : 26,
    'debit_request.paid' : 27,
    'debit_request.rejected' : 28,
    'debit_request.canceled' : 26,
}

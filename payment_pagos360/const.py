API_URL = "https://api.pagos360.com"
API_TEST_URL = "https://api.sandbox.pagos360.com"

DEFAULT_PAYMENT_METHODS_CODES = [
    # Primary payment methods.
    'pagos360',
    # Brand payment methods.
    'visa',
    'mastercard',
    'ceconsud',
    'naranja',
    'nativa',
    'tarjeta_mercadopago',
]

EVENT_TYPES = [
    "adhesion.canceled",
    "adhesion.signed",
    "card_adhesion.canceled",
    "card_adhesion.signed",
    "card_debit_request.canceled",
    "card_debit_request.paid",
    "card_debit_request.refunded",
    "card_debit_request.rejected",
    "card_debit_request.reverted",
    "card_debit_request.waived",
    "debit_request.canceled",
    "debit_request.paid",
    "debit_request.refunded",
    "debit_request.rejected",
    "debit_request.reverted",
    "debit_request.waived",
    "payment_request.paid",
    "payment_request.refunded",
    "payment_request.rejected",
    "payment_request.reverted",
    "payment_request.transfer_canceled",
    "payment_request.transfer_created",
    "payment_request.transfer_rejected",
    "payment_request.waived",
    "payment_request.banelco_pmc_created",
    "payment_request.debin_created",
    "payment_request.expired",
    "payment_request.link_pagos_created",
]

API_URL = "https://api.pagos360.com"
CARD_DEBIT_DAYS_DAYS = 2
API_TEST_URL = "https://api.sandbox.pagos360.com"

# Only the Pagos360 primary method is activated on enable. Card brands are NOT listed here:
# the provider has no "card" payment method, so listing brands never activated anything, and
# brand handling now lives in the pagos360.card.brand catalog used for coupon exclusions.
DEFAULT_PAYMENT_METHODS_CODES = ["pagos360"]

# Reference amount used only to enumerate the merchant's available brands/installments
# through the "channel-installments" helper endpoint, which is amount-dependent.
AVAILABLE_METHODS_REFERENCE_AMOUNT = 10000

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

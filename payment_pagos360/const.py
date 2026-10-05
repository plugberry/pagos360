API_URL = "https://api.pagos360.com"
CARD_DEBIT_DAYS_DAYS = 2
DEBIT_ENTITY_BY_ADHESION_TYPE = {"card_adhesion": "card_debit_request", "adhesion": "debit_request"}
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

# Payment methods that end up in a cash coupon (PagoFacil/Rapipago). Pagos360 does not issue
# the coupon above its cash limit: the request is created but comes back without `pdf_url`.
CASH_PAYMENT_METHOD_CODES = [
    "pagofacil",
    "rapipago",
]

# Daily collection control. Pagos360 reports closed days in Argentina's time.
CONTROL_TIMEZONE = "America/Argentina/Buenos_Aires"
# Days after the execution date until a debit counts as due: CBU debits are reported two days after payment.
CONTROL_DEBIT_MARGIN_DAYS = 3
# Days an adhesion has to be signed, counted from its creation.
CONTROL_ADHESION_DAYS = 7
# Days after the due date of the second and last individual check.
CONTROL_RECHECK_DAYS = 7
# Per row kind: states the control checks, and states that already reflect the row.
CONTROL_STATES = {
    "collection": (("draft", "pending", "error"), ("done",)),
    "chargeback": (("done",), ("cancel",)),
    "signed_adhesion": (("draft", "pending"), ("done",)),
}

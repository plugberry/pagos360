import logging

from odoo import _, api, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


class PaymentTransaction(models.Model):
    _inherit = "payment.transaction"

    def _pagos360_get_provider_invoice_from_reference(self, reference):
        """Extract provider and invoice information from a payment reference.
        :param str reference: The payment reference in format 'inv-{provider_id}-{invoice_id}'
        :return: Tuple of (provider, invoice) or (False, False) if invalid
        :rtype: tuple
        """
        reference_parts = reference.split("-")
        if len(reference_parts) == 3 and reference_parts[0] == "inv":
            try:
                payment_provider_id = self.env["payment.provider"].browse(int(reference_parts[1])).exists()
                account_move_id = self.env["account.move"].browse(int(reference_parts[2])).exists()
                return payment_provider_id, account_move_id
            except (ValueError, TypeError):
                _logger.warning("Invalid reference format: %s", reference)
                return False, False
        return False, False

    def _pagos360_get_barcode_paid_amount(self, provider, invoice, payment_data):
        """Amount collected for a paid barcode request: `request_result` of the payment request.

        The notice carries no amount, the invoice may already be settled when it arrives, and
        `first_total` is what was requested, not what was paid.
        """
        payload = payment_data.setdefault("payload", {})
        request_result = payload.get("request_result")
        if not request_result:
            response = provider._pagos360_make_request(
                "/payment-request?id=%s" % payment_data.get("entity_id"), method="GET"
            )
            request_data = response.get("data", response) if isinstance(response, dict) else {}
            if isinstance(request_data, list):
                request_data = next(
                    (
                        data
                        for data in request_data
                        if data.get("external_reference") == payload.get("external_reference")
                    ),
                    request_data[0] if request_data else {},
                )
            request_result = request_data.get("request_result")
        if isinstance(request_result, dict):
            request_result = [request_result]
        # Keep it on the payload so the amount check and the paid_at lookup do not hit the API again.
        payload["request_result"] = request_result or []
        amount = sum(result.get("amount") or 0.0 for result in (request_result or []) if isinstance(result, dict))
        if invoice.currency_id.compare_amounts(amount, 0.0) <= 0:
            message = _(
                "Pagos360 reported this invoice as paid (payment request %s) but did not return"
                " the amount collected, so no payment was registered. Check the payment request"
                " in Pagos360 and register the payment manually.",
                payment_data.get("entity_id"),
            )
            invoice.message_post(body=message)
            raise ValidationError("PAGOS360: " + message)
        return amount

    @api.model
    def _search_by_reference(self, provider_code, payment_data):
        """Override of payment to search the transaction based on Pagos360 invoice barcode data.

        This method extends the search to handle invoice barcode payments where a transaction
        may not exist yet when a payment notification is received from Pagos360.

        :param str provider_code: The code of the provider that handled the transaction
        :param dict payment_data: The notification data sent by the provider
        :return: The transaction if found
        :rtype: recordset of `payment.transaction`
        """
        tx = super()._search_by_reference(provider_code, payment_data)
        if tx or provider_code != "pagos360":
            return tx

        # Handle invoice barcode payments
        payload = payment_data.get("payload", {})
        external_reference = payload.get("external_reference")
        payment_status = payment_data.get("type")
        entity_name = payment_data.get("entity_name")

        # Only create transaction for invoice barcode payments that are paid
        if (
            external_reference
            and payment_status == "paid"
            and entity_name == "payment_request"
            and all(provider_invoice := self._pagos360_get_provider_invoice_from_reference(external_reference))
        ):
            provider, invoice = provider_invoice
            tx = self.search([("provider_id", "=", provider.id), ("reference", "=", external_reference)], limit=1)
            if not tx:
                payment_method_id = (
                    self.env["payment.method"]
                    ._get_compatible_payment_methods(provider.ids, invoice.partner_id.id)
                    .filtered(lambda x: x.code == "pagofacil")
                )
                amount = self._pagos360_get_barcode_paid_amount(provider, invoice, payment_data)
                tx_vals = {
                    "reference": external_reference,
                    "provider_reference": payment_data.get("entity_id"),
                    "amount": amount,
                    "currency_id": invoice.currency_id.id,
                    "partner_id": invoice.partner_id.commercial_partner_id.id,
                    "provider_id": provider.id,
                    "payment_method_id": payment_method_id.id,
                    "company_id": invoice.company_id.id,
                    "invoice_ids": [(6, 0, [invoice.id])],
                    "operation": "online_redirect",
                }
                tx = self.create(tx_vals)
                _logger.info("Created transaction with reference %s from invoice barcode payment", external_reference)
        return tx

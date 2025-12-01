import logging

from odoo import api, models

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
                tx_vals = {
                    "reference": external_reference,
                    "provider_reference": payment_data.get("entity_id"),
                    "amount": abs(invoice.pagos360_barcode_amount),
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

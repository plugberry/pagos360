import logging

from odoo import _, models
from odoo.exceptions import ValidationError


_logger = logging.getLogger(__name__)


class PaymentTransaction(models.Model):
    _inherit = 'payment.transaction'

    def _pagos360_get_provider_invoice_from_reference(self, reference):
        reference = reference.split('-')
        if len(reference) == 3 and reference[0] == 'inv':
            payment_provider_id = self.env['payment.provider'].browse(int(reference[1])).exists()
            account_move_id = self.env['account.move'].browse(int(reference[2])).exists()
            return payment_provider_id, account_move_id
        return False, False

    def _get_tx_from_notification_data(self, provider_code, notification_data):
            """ Override of payment to find the transaction based on Pagos360 data.
            :param str provider_code: The code of the provider that handled the transaction
            :param dict notification_data: The notification data sent by the provider
            :return: The transaction if found
            :rtype: recordset of `payment.transaction`
            :raise: ValidationError if the data match no transaction
            """
            external_reference = notification_data.get('payload', {}).get('external_reference')
            payment_status = notification_data.get('type')
            if provider_code == 'pagos360' and external_reference and payment_status == 'paid' and all(provider_invoice:=self._pagos360_get_provider_invoice_from_reference(external_reference)):
                provider, invoice = provider_invoice
                tx = self.search([('provider_id', '=', provider.id), ('reference', '=', external_reference)], limit=1)
                if not tx:
                    payment_method_id = self.env['payment.method']._get_compatible_payment_methods(provider.ids, invoice.partner_id.id).filtered(lambda x: x.code=='pagofacil')
                    tx_vals = {
                        "reference": external_reference,
                        "provider_reference": notification_data.get('entity_id'),
                        "amount": abs(invoice.amount_residual),
                        "currency_id": invoice.currency_id.id,
                        "partner_id": invoice.partner_id.commercial_partner_id.id,
                        "provider_id": provider.id,
                        "payment_method_id": payment_method_id.id,
                        "company_id": invoice.company_id.id,
                        "invoice_ids": [(6, 0, [invoice.id])],
                        "operation": "online_redirect",
                    }
                    tx = self.env['payment.transaction'].create(tx_vals)
            return super()._get_tx_from_notification_data(provider_code, notification_data)

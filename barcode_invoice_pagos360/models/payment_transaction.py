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

    def _pagos360_get_barcode_paid_amount(self, provider, invoice, notification_data):
        """Amount collected for a paid barcode request: `request_result` of the payment request.

        The notice carries no amount, the invoice may already be settled when it arrives, and
        `first_total` is what was requested, not what was paid.
        """
        payload = notification_data.setdefault('payload', {})
        request_result = payload.get('request_result')
        if not request_result:
            response = provider._pagos360_make_request(
                '/payment-request?id=%s' % notification_data.get('entity_id'), method='GET')
            request_data = response.get('data', response) if isinstance(response, dict) else {}
            if isinstance(request_data, list):
                request_data = next(
                    (data for data in request_data
                     if data.get('external_reference') == payload.get('external_reference')),
                    request_data[0] if request_data else {})
            request_result = request_data.get('request_result')
            # Keep it on the payload so the paid_at lookup does not hit the API again.
            payload['request_result'] = request_result
        if isinstance(request_result, dict):
            request_result = [request_result]
        amount = sum(
            result.get('amount') or 0.0
            for result in (request_result or []) if isinstance(result, dict))
        if invoice.currency_id.compare_amounts(amount, 0.0) <= 0:
            message = _(
                "Pagos360 reported this invoice as paid (payment request %s) but did not return"
                " the amount collected, so no payment was registered. Check the payment request"
                " in Pagos360 and register the payment manually.",
                notification_data.get('entity_id'))
            invoice.message_post(body=message)
            raise ValidationError("PAGOS360: " + message)
        return amount

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
                    amount = self._pagos360_get_barcode_paid_amount(provider, invoice, notification_data)
                    tx_vals = {
                        "reference": external_reference,
                        "provider_reference": notification_data.get('entity_id'),
                        "amount": amount,
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

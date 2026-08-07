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

    def _pagos360_barcode_fetch_payment_request(self, provider, notification_data):
        """Complete the notification payload with the payment request from Pagos360.

        Webhook notifications carry ids and the external reference only, so both the
        collected amount (needed here) and `paid_at` (needed downstream, in
        payment_pagos360) are missing. The entity is fetched once and merged into the
        payload so every consumer reads it from the same place instead of fetching again.
        """
        payload = notification_data.get('payload', {})
        entity_id = notification_data.get('entity_id')
        if not entity_id or payload.get('request_result'):
            return payload
        try:
            response = provider._pagos360_make_request(
                '/payment-request?id=%s' % entity_id, method='GET'
            )
        except Exception as e:
            _logger.warning(
                "PAGOS360: could not fetch payment request %s: %s", entity_id, e
            )
            return payload
        for data in response.get('data', []):
            if data.get('external_reference') == payload.get('external_reference'):
                payload.update(data)
                break
        return payload

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
                    # The invoice residual is only a fallback. Pagos360 reports counter
                    # payments days later, and by then the invoice may already have been
                    # settled by another payment: its residual would be 0 and the payment
                    # would be posted for no money at all.
                    payload = self._pagos360_barcode_fetch_payment_request(provider, notification_data)
                    amount = sum(
                        result.get('amount', 0.0) for result in payload.get('request_result') or []
                    ) or abs(invoice.amount_residual)
                    if not amount:
                        _logger.warning(
                            "PAGOS360: no amount available for paid payment request %s (invoice %s), "
                            "skipping transaction creation",
                            external_reference, invoice.name,
                        )
                    else:
                        payment_method_id = self.env['payment.method']._get_compatible_payment_methods(provider.ids, invoice.partner_id.id).filtered(lambda x: x.code=='pagofacil')
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

import logging
import pprint

from werkzeug import urls
from datetime import timedelta

from odoo import _, models, fields
from odoo.exceptions import ValidationError

from ..controllers.main import Pagos360Controller


_logger = logging.getLogger(__name__)


class PaymentTransaction(models.Model):
    _inherit = 'payment.transaction'

    def _get_specific_rendering_values(self, processing_values):
        """ Override of `payment` to return Pagos360-specific rendering values.

        Note: self.ensure_one() from `_get_rendering_values`.

        :param dict processing_values: The generic and specific processing values of the transaction
        :return: The dict of provider-specific processing values.
        :rtype: dict
        """
        res = super()._get_specific_rendering_values(processing_values)
        if self.provider_code != 'pagos360':
            return res

        # Initiate the payment and retrieve the payment link data.
        payload = self._pagos360_prepare_preference_request_payload()
        _logger.info("Sending '/payment-request' request for link creation:\n%s", pprint.pformat(payload))

        payment_data = self.provider_id._pagos360_make_request('/payment-request', data=payload)
        checkout_url = payment_data['checkout_url']

        return {'api_url': checkout_url,}

    def _pagos360_prepare_preference_request_payload(self):
        """ Create the payload for the payment request based on the transaction values.

        :return: The request payload
        :rtype: dict
        """
        base_url = self.provider_id.get_base_url()
        redirect_url = urls.url_join(base_url, Pagos360Controller._return_url)
        webhook_url = urls.url_join(base_url, Pagos360Controller._webhook_url)

        first_due_date, first_total = self.get_first_due_values()
        # second_due_date, second_total = self.get_second_due_values()

        return {
            'payment_request':{
                'description': self.reference,
                'external_reference': self.reference,   # No requerido
                'payer_name': self.partner_name,
                'payer_email': self.partner_email,      # No requerido
                'first_due_date': (first_due_date).strftime('%d-%m-%Y'),
                'first_total': first_total,
                # 'second_due_date': (second_due_date).strftime('%d-%m-%Y'),   # No requerido
                # 'second_total': second_total,            # No requerido
                'back_url_success': redirect_url,       # No requerido
                'back_url_pending': redirect_url,       # No requerido
                'back_url_rejected': redirect_url,      # No requerido
            }
        }

    def get_first_due_values(self):
        first_due_date = fields.Datetime.now() + timedelta(days=self.provider_id.validity_days)
        first_total = self.amount
        return first_due_date, first_total

    def get_second_due_values(self):
        second_due_date = fields.Datetime.now() + timedelta(days=self.provider_id.second_validity_days)
        second_total = self.amount * (1 + self.provider_id.second_due_fees / 100.0)
        return second_due_date, second_total

    def _get_tx_from_notification_data(self, provider_code, notification_data):
        """ Override of payment to find the transaction based on Pagos360 data.

        :param str provider_code: The code of the provider that handled the transaction
        :param dict notification_data: The notification data sent by the provider
        :return: The transaction if found
        :rtype: recordset of `payment.transaction`
        :raise: ValidationError if the data match no transaction
        """
        tx = super()._get_tx_from_notification_data(provider_code, notification_data)
        if provider_code != 'pagos360' or len(tx) == 1:
            return tx

        payload = notification_data.get('payload')
        tx = self.search(
            [('reference', '=', payload.get('external_reference')), ('provider_code', '=', 'pagos360')]
        )
        if not tx:
            raise ValidationError("Pagos360: " + _(
                "No transaction found matching reference %s.", notification_data.get('ref')
            ))
        return tx

    def _process_notification_data(self, notification_data):
        """ Override of payment to process the transaction based on Pagos360 data.

        Note: self.ensure_one()

        :param dict notification_data: The notification data sent by the provider
        :return: None
        """
        super()._process_notification_data(notification_data)
        if self.provider_code != 'pagos360':
            return

        entity_id = notification_data.get('entity_id')
        if not entity_id:
            raise ValidationError("Pagos360: " + _("Received data with missing entity id."))
        self.provider_reference = entity_id

        payment_status = notification_data.get('type')

        if payment_status == 'pending':
            self._set_pending()
        elif payment_status == 'authorized':
            self._set_authorized()
        elif payment_status == 'paid':
            self._set_done()
        elif payment_status in ['expired', 'canceled', 'failed']:
            self._set_canceled("Pagos360: " + _("Canceled payment with status: %s", payment_status))
        else:
            _logger.info(
                "received data with invalid payment status (%s) for transaction with reference %s",
                payment_status, self.reference
            )
            self._set_error(
                "Pagos360: " + _("Received data with invalid payment status: %s", payment_status)
            )

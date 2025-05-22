import logging
import pprint

from werkzeug import urls
from datetime import timedelta
from dateutil.relativedelta import relativedelta

from odoo import _, models, fields
from odoo.exceptions import ValidationError, UserError
from odoo.addons.payment import utils as payment_utils
from odoo.tools.safe_eval import safe_eval

from ..controllers.main import Pagos360Controller

_logger = logging.getLogger(__name__)


class PaymentTransaction(models.Model):
    _inherit = 'payment.transaction'

    pagos360_adhesion_type = fields.Selection(related='token_id.pagos360_adhesion_type', store=True)

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
        if self.operation == 'validation':
            return {'api_url': "%s&pReference=%s" % (self.provider_id.pagos360_form_url, self.reference)}

        # Initiate the payment and retrieve the payment link data.
        payload = self._pagos360_prepare_preference_request_payload()
        _logger.info("Sending '/payment-request' request for link creation:\n%s", pprint.pformat(payload))

        payment_data = self.provider_id._pagos360_make_request('/payment-request', data=payload)
        if self.payment_method_code == 'pagos360':
            api_url = payment_data['checkout_url']
        elif self.payment_method_code == 'pagofacil':
            access_token = payment_utils.generate_access_token(self.partner_id.id, self.amount, self.currency_id.id)
            api_url = '/payment/pagos360/pagofacil?tx_id=%s&access_token=%s' % (self.id, access_token)
        elif self.payment_method_code == 'rapipago':
            access_token = payment_utils.generate_access_token(self.partner_id.id, self.amount, self.currency_id.id)
            api_url = '/payment/pagos360/rapipago?tx_id=%s&access_token=%s' % (self.id, access_token)

        return {'api_url': api_url, }

    def _pagos360_prepare_preference_request_payload(self):
        """ Create the payload for the payment request based on the transaction values.

        :return: The request payload
        :rtype: dict
        """
        base_url = self.provider_id.get_base_url()
        redirect_url = urls.url_join(base_url, Pagos360Controller._return_url)

        first_due_date, first_total = self.get_first_due_values()
        # second_due_date, second_total = self.get_second_due_values()

        res = {
            'payment_request': {
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
        if self.provider_id.pagos360_excluded_channels:
            res['payment_request'].update({'excluded_channels': safe_eval(self.provider_id.pagos360_excluded_channels)})
        if self.provider_id.pagos360_excluded_installments:
            res['payment_request'].update({'excluded_installments': safe_eval(self.provider_id.pagos360_excluded_installments)})
        if self.provider_id.pagos360_excluded_card_brands:
            res['payment_request'].update({'excluded_card_brands': safe_eval(self.provider_id.pagos360_excluded_card_brands)})
        return res

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

        entity_name = notification_data.get('entity_name')
        if not entity_name:
            raise ValidationError("PAGOS360: " + _("Received data with missing entity name."))

        if entity_name in ['debit_request', 'card_debit_request']:
            domain = [('provider_reference', '=', payload.get('id')), ('provider_code', '=', 'pagos360')]
        else:
            domain = [('reference', '=', payload.get('external_reference')), ('provider_code', '=', 'pagos360')]
        if payload.get('entity_name') == 'payment_request':
            domain.append(['pagos360_adhesion_type', '=',  False])
        tx = self.search(domain)
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
        entity_name = notification_data.get('entity_name')
        entity_id = notification_data.get('entity_id')
        if not entity_id:
            raise ValidationError("PAGOS360: " + _("Received data with missing entity id."))

        self.provider_reference = entity_id
        payment_status = notification_data.get('type')

        try:
            if payment_status in ['pending', 'in_process', 'pending_to_sign', 'transfer_created', 'link_pagos_created', 'banelco_pmc_created', 'debin_created']:
                if self.state != 'pending':
                    self._set_pending()
            elif payment_status == 'signed' and self.operation == 'validation':
                self._set_done()
                if not self.token_id:
                    self._pagos360_tokenize_from_feedback_data(notification_data)
            elif payment_status == 'paid':
                self._set_done(extra_allowed_states=('cancel',))
            elif payment_status == 'reverted':
                self.payment_id.action_draft()
                self.payment_id.action_cancel()
                self._set_canceled("PAGOS360: " + _("Canceled payment with status: %s", payment_status))
            elif payment_status in ['expired', 'canceled', 'rejected','transfer_canceled']:
                # Solo cambio el estado en los casos que puedo hacerlo.
                # las autorizaciones se pueden cancelar cuando estan ya en done
                if self.state in ['draft', 'pending', 'authorized']:
                    self._set_canceled("PAGOS360: " + _("Canceled payment with status: %s", payment_status))
                if entity_name in ['card_adhesion', 'adhesion']:
                    if self.token_id and self.token_id.active:
                        self.token_id.with_context(is_notification=True).write({'active': False})
            else:
                _logger.info(
                    "received data with invalid payment status (%s) for transaction with reference %s",
                    payment_status, self.reference
                )
                message = """
                    Parece que esta transacción no se pudo realizar, ante algún inconveniente por favor comunicarse a
                     través de los siguientes canales:<br/>
                    Correo Electrónico: soporte@pagos360.com.ar<br/>
                    WhatsApp: +54 3512548747\n
                    Información:\n
                    - Transacción PAGOS360: {transaction}<br/>
                    - Código de Error: {error_code}<br/>
                    - Mensaje de Error": {error_msg}<br/>
                """.format(transaction=self.provider_reference, error_code=payment_status, error_msg='')
                self._set_error("PAGOS360: " + message)
        except Exception as e:
            _logger.info(
                "PAGOS360 Error: (%s) for transaction with id %s",
                e, self.id
            )
            message = """
                Parece que esta transacción no se pudo realizar, le sugerimos revisar en su portal de PAGOS360 el
                 estado de la solicitud de pago.
                Ante algún inconveniente con la misma por favor comunicarse a través de los siguientes canales:
                Correo Electrónico: soporte@pagos360.com.ar\n
                WhatsApp: +54 3512548747\n
                Información:\n
                - Transacción id: {transaction}<br/>
                - Mensaje de Error": {error_msg}<br/>
            """.format(transaction=self.id,  error_msg=e)
            self._set_error("PAGOS360: " + message)

    def _pagos360_tokenize_from_feedback_data(self, notification_data):
        """ Create a new token based on the feedback data.

        Note: self.ensure_one()

        :param dict data: The feedback data sent by the provider
        :return: None
        """
        self.ensure_one()
        adhesion_id = notification_data['entity_id']
        if notification_data['entity_name'] == 'card_adhesion':
            endpoint = "/card-adhesion/{}".format(adhesion_id)
        else:
            endpoint = "/adhesion/{}".format(adhesion_id)

        adhesion_data = self.provider_id._pagos360_make_request(endpoint, data=None, method='GET')
        if adhesion_data:
            token_vals = {
                'provider_id': self.provider_id.id,
                'partner_id': self.partner_id.id,
                'provider_ref': adhesion_id,
                'payment_method_id': self.payment_method_id.id,
                'pagos360_adhesion_type': notification_data['entity_name'],
                'pagos360_external_reference': adhesion_data['external_reference'],
                'pagos360_card': adhesion_data['card'] if notification_data['entity_name'] == 'card_adhesion' else None,
                'pagos360_card_number': adhesion_data['last_four_digits'] if notification_data['entity_name'] == 'card_adhesion' else None,
                'pagos360_cbu_number': adhesion_data['cbu_number'] if notification_data['entity_name'] == 'adhesion' else None,
                'pagos360_bank': adhesion_data['bank'] if notification_data['entity_name'] == 'adhesion' else None,
            }
            token = self.env['payment.token'].create(token_vals)
            self.write({
                'token_id': token.id,
                'tokenize': False,
            })
            _logger.info(
                "created token with id %s for partner with id %s", token.id, self.partner_id.id
            )

    def _send_payment_request(self):
        if self.provider_code == 'pagos360':
            if self.token_id.pagos360_adhesion_type == 'card_adhesion':
                req = self._pagos360_card_debit_request()
                self._process_notification_data(self.simulate_webhook('card_adhesion', req))
            if self.token_id.pagos360_adhesion_type == 'adhesion':
                req = self._pagos360_debit_request()
            self.env.cr.commit()
            if req:
                self._process_notification_data(self.simulate_webhook(self.token_id.pagos360_adhesion_type, req))
                self.env.cr.commit()
        return super()._send_payment_request()

    def _pagos360_card_debit_request(self):
        operation_date = fields.Date.today()
        cut_day = int(self.env['ir.config_parameter'].sudo().get_param('pagos360.cut_day', '19'))
        if operation_date.day > cut_day:
            operation_date = operation_date + relativedelta(months=1)
        data = {
            "card_debit_request": {
                "description": _("Payment %s") % self.company_id.display_name,
                "amount": self.amount,
                "month": operation_date.month,
                "year": operation_date.year,
                "card_adhesion_id": int(self.token_id.provider_ref)
            }
        }
        return self.provider_id._pagos360_make_request('card-debit-request', data=data, method='POST')

    def _pagos360_next_business_day(self, due_date, days=3):
        data = {
            "next_business_day": {
                "date": due_date.strftime('%d-%m-%Y'),
                "days": days
            }
        }
        return self.provider_id._pagos360_make_request('validator/next-business-day', data=data, method='POST')

    def _pagos360_debit_request(self):
        first_due_date, first_total = self.get_first_due_values()
        next_business_day = self._pagos360_next_business_day(first_due_date)
        data = {
            "debit_request": {
                "description": _("Payment %s") % self.company_id.display_name,
                "first_total": self.amount,
                # la fecha de vencimiento para cbu es un dia habil hay un sevicio para eso
                "first_due_date": fields.Datetime.from_string(next_business_day[:10]).strftime('%d-%m-%Y'),
                "adhesion_id": int(self.token_id.provider_ref)
            }
        }
        return self.provider_id._pagos360_make_request('debit-request', data=data, method='POST')

    def get_pagos360_info(self, check_payment_state=True):
        result_msg = []
        for tx in self.filtered(lambda x: x.provider_code == 'pagos360'):
            # Check state of adhesion
            payload = False
            ref_sanitarzed = tx.reference.replace('%', '%25')
            if tx.operation == 'validation':
                datas = tx.provider_id._pagos360_make_request('/card-adhesion?external_reference=%s&page=1' % ref_sanitarzed, method='GET')
                entity_name = 'card_adhesion'
                for data in datas['data']:
                    payload = tx.simulate_webhook(entity_name, data)
                    result_msg.append(payload)
                    tx.sudo()._process_notification_data(payload)
                datas = tx.provider_id._pagos360_make_request('/adhesion?external_reference=%s&page=1' % ref_sanitarzed, method='GET')
                entity_name = 'adhesion'
                for data in datas['data']:
                    payload = tx.simulate_webhook(entity_name, data)
                    result_msg.append(payload)
                    tx.sudo()._process_notification_data(payload)

            # Check state of payment
            elif not tx.pagos360_adhesion_type and tx.operation != 'validation':
                # https://api.sandbox.pagos360.com/debit-request?page=1
                data = tx._get_operation_info_from_data(tx.provider_id._pagos360_make_request('/payment-request?external_reference=%s' % ref_sanitarzed, method='GET' ))
                payload = tx.simulate_webhook('payment_request', data)
                result_msg.append(payload)
                tx.sudo()._process_notification_data(payload)
            # Check state of payment
            elif tx.pagos360_adhesion_type == 'adhesion':
                data = tx.provider_id._pagos360_make_request('/debit-request?id=%s' % tx.provider_reference, method='GET')
                payload = tx.simulate_webhook('debit_request', data['data'][0])
                result_msg.append(payload)
                tx.sudo()._process_notification_data(payload)

            elif tx.pagos360_adhesion_type == 'card_adhesion':
                data = tx.provider_id._pagos360_make_request('/card-debit-request?id=%s' % tx.provider_reference, method='GET')
                payload = self.simulate_webhook('card_debit_request', data['data'][0])
                tx.sudo()._process_notification_data(payload)
            self.env.cr.commit()
        return self.pagos360_readable_result(result_msg)

    def pagos360_cancel_transactions(self):
        for tx in self.filtered(lambda t: t.pagos360_adhesion_type in ['adhesion', 'card_adhesion']):
            payment_request_id = tx.provider_reference
            if tx.pagos360_adhesion_type == 'adhesion':
                endpoint = 'debit-request'
            elif tx.pagos360_adhesion_type == 'card_adhesion':
                endpoint = 'card-debit-request'
            else:
                continue
            pagos360_tx = tx.provider_id._pagos360_make_request("/{endpoint}/{id}".format(endpoint=endpoint, id=payment_request_id), method='GET')
            if pagos360_tx and pagos360_tx.get("state") == 'pending':
                response_json = tx.provider_id._pagos360_make_request("/{endpoint}/{id}/cancel".format(endpoint=endpoint, id=payment_request_id), method='PUT')
                if response_json and response_json.get("state") == 'canceled':
                    tx._set_canceled()
        return

    def _get_operation_info_from_data(self, request_info):
        for data in request_info['data']:
            if data['external_reference'] == self.reference:
                return data
        return []

    def pagos360_readable_result(self, result_msg):
        txt = []
        for data in result_msg:
            txt += ['---------------------------']
            txt += ["external_reference: %s" % data['payload'].get('external_reference')]
            txt += ["state: %s" % data['payload'].get('state')]
            txt += ['---------------------------']
            txt += ['%s: %s' % (x, data[x]) for x in data if x != 'payload']
            txt += ['- %s: %s' % (x, data.get('payload', []).get(x)) for x in data.get('payload', [])]
            txt += ['---------------------------']

        raise UserError("%s" % ' \n'.join(txt))

    def simulate_webhook(self, entity_name, data):
        if not data:
            _logger.warning("No data recieved")
            return
        return {'entity_name': entity_name, 'entity_id': data['id'], 'type': data['state'], 'payload': data}


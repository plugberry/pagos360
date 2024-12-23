import uuid
import logging
import requests
from werkzeug import urls

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools.safe_eval import safe_eval

from ..controllers.main import Pagos360Controller
from .. import const

_logger = logging.getLogger(__name__)


class PaymentProvider(models.Model):
    _inherit = 'payment.provider'

    code = fields.Selection(
        selection_add=[('pagos360', "PAGOS360")], ondelete={'pagos360': 'set default'})
    pagos360_api_key = fields.Char(
        string="Api Key (PAGOS360)", groups='base.group_system')
    pagos360_test_api_key = fields.Char(
        string="Test Api Key (PAGOS360)", groups='base.group_system')
    pagos360_form_url = fields.Char("Link formulario debito automático")

    validity_days = fields.Integer(default=15)
    second_validity_days = fields.Integer(default=30)
    second_due_fees = fields.Float(string="Surcharge", default=10)

    pagos360_excluded_channels = fields.Text(help="['credit_card', 'credit_card_agro', 'debit_card', 'banelco_pmc', 'link_pagos', 'DEBIN', 'wire_transfer', 'non_banking', 'QR',]")
    pagos360_excluded_installments = fields.Text(help="[2,3,4,5]")
    pagos360_excluded_card_brands = fields.Text()

    @api.constrains('pagos360_excluded_channels')
    def _validate_pagos360_excluded_channels(self):
        valid_values = ['credit_card', 'credit_card_agro', 'debit_card', 'banelco_pmc', 'link_pagos', 'DEBIN', 'wire_transfer', 'non_banking', 'QR',]
        for rec in self.filtered('pagos360_excluded_channels'):
            vals = safe_eval(rec.pagos360_excluded_channels)
            if not isinstance(vals, list) or any([x not in valid_values for x in vals]):
                raise ValidationError(f"Los canales de pagos solo pueden ser {valid_values}")

    @api.constrains('pagos360_excluded_installments')
    def _validate_pagos360_excluded_installments(self):
        for rec in self.filtered('pagos360_excluded_installments'):
            vals = safe_eval(rec.pagos360_excluded_installments)
            if not isinstance(vals, list) or any([not isinstance(x, int) for x in vals]):
                raise ValidationError("las cuotas solo pueden ser numeros")

    def _pagos360_get_api_url(self):
        self.ensure_one()
        if self.state == 'enabled':
            return const.API_URL
        else:
            return const.API_TEST_URL

    def _pagos360_get_api_key(self):
        self.ensure_one()
        if self.state == 'enabled':
            return self.pagos360_api_key
        else:
            return self.pagos360_test_api_key

    def _pagos360_make_request(self, endpoint, data=None, method='POST'):
        self.ensure_one()
        url = urls.url_join(self._pagos360_get_api_url(), endpoint)

        headers = {
            "Accept": "application/json",
            "Authorization": f'Bearer {self._pagos360_get_api_key()}',
            "Content-Type": "application/json",
        }
        try:
            response = requests.request(method, url, json=data, headers=headers, timeout=60)
            response.raise_for_status()
        except requests.exceptions.RequestException:
            _logger.error(response.text)
            _logger.exception("Unable to communicate with Pagos360: %s", url)
            _logger.error("send data data: %s" % str(data))
            _logger.error("response.text: %s" % response.text)
            raise ValidationError("Pagos360: {error_title} \n ref: {error_ref}".format(
                error_title=_("Could not establish the connection to the API."),
                error_ref=response.text)
            )
        return response.json()

    @api.depends('pagos360_api_key', 'pagos360_test_api_key')
    def ensure_webhook(self):
        base_url = self.get_base_url().replace('http:','https:')
        webhook_url = urls.url_join(base_url, Pagos360Controller._webhook_url)

        message = _("Your Pagos360 Webhook was already set up.")
        notification_type = 'success'
        if not self._webhook_is_set(webhook_url):
            data = {
                "webhook": {
                    "url": webhook_url,
                    "event_types": self._get_event_types()
                }
            }
            response = self._pagos360_make_request('/webhook', data=data)
            if response.get('id'):
                message = _("Your Pagos360 Webhook was successfully set up.")
                notification_type = 'success'
            else:
                message = _("Error setting your webhooks.")
                notification_type = 'danger'
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'message': message,
                'sticky': False,
                'type': notification_type,
                'next': {'type': 'ir.actions.act_window_close'},
            }
        }

    def _webhook_is_set(self, webhook_url):
        # Para chequear si el webhook existe realizamos un GET a /webhook
        # Esto nos regresa todas las urls configuradas y los tipos de eventos para los cual se configuró un webhook
        # Por lo que buscamos si existe el webhook para nuestra url
        # Si existe comparamos los eventos seteados
        # En caso de haber dierencias hacemos un DELETE del webhook para retornar False y que luego se envien de nuevo los webhook
        webhooks = self._pagos360_make_request('/webhook', method='GET')
        if webhooks and webhooks.get('data'):
            data = webhooks.get('data')
            for webhook in data:
                if webhook.get('url') == webhook_url:
                    webhook_id = webhook.get('id')
                    webhook_events = [event["id"] for event in webhook["events"]]
                    if set(webhook_events) == set(self._get_event_types()):
                        return True
                    else:
                        self._pagos360_make_request('/webhook/%s' % webhook_id, method='DELETE')
        return False

    def handled_event_types(self):
        # Se puede heredar en otros modulos para agregar nuevos webhooks
        return const.EVENT_TYPES

    def _get_event_types(self):
        event_types = list()
        types = self._pagos360_make_request('/event-type', method='GET')
        handled_event_types = self.handled_event_types()
        if types and types.get('data'):
            data = types.get('data')
            event_types = [x['id'] for x in data if x['name'] in handled_event_types]
        return event_types

    #=== COMPUTE METHODS ===#
    def _compute_feature_support_fields(self):
        """ Override of `payment` to enable additional features. """
        super()._compute_feature_support_fields()
        self.filtered(lambda p: p.code == 'pagos360').update({
            'support_manual_capture': 'full_only',
            'support_refund': 'full_only',
            'support_tokenization': True,
        })

    def _get_default_payment_method_codes(self):
        """ Override of `payment` to return the default payment method codes. """
        default_codes = super()._get_default_payment_method_codes()
        if self.code != 'pagos360':
            return default_codes
        return const.DEFAULT_PAYMENT_METHODS_CODES

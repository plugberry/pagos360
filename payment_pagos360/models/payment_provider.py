import uuid
import logging
import requests
from werkzeug import urls

from odoo import _, api, fields, models, Command
from odoo.exceptions import UserError, ValidationError

from ..controllers.main import Pagos360Controller
from ..const import API_URL, API_TEST_URL, HANDLED_WEBHOOK_EVENTS

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

    def _pagos360_get_api_url(self):
        self.ensure_one()
        if self.state == 'enabled':
            return API_URL
        else:
            return API_TEST_URL

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
            raise ValidationError("Pagos360: " + _("Could not establish the connection to the API."))
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

    def _get_event_types(self):
        # Se puede heredar en otros modulos para agregar nuevos webhooks
        event_types = list()
        event_types.extend([
            HANDLED_WEBHOOK_EVENTS['payment_request.expired'],
            HANDLED_WEBHOOK_EVENTS['payment_request.paid'],
            HANDLED_WEBHOOK_EVENTS['payment_request.refunded'],
            HANDLED_WEBHOOK_EVENTS['payment_request.rejected'],
            HANDLED_WEBHOOK_EVENTS['adhesion.signed'],
            HANDLED_WEBHOOK_EVENTS['adhesion.canceled'],
            HANDLED_WEBHOOK_EVENTS['card_adhesion.signed'],
            HANDLED_WEBHOOK_EVENTS['card_adhesion.canceled'],
        ])
        return event_types
    
    #=== COMPUTE METHODS ===#

    def _compute_feature_support_fields(self):
        """ Override of `payment` to enable additional features. """
        super()._compute_feature_support_fields()
        self.filtered(lambda p: p.code == 'pagos360').update({
            'support_manual_capture': True,
            'support_refund': 'full_only',
            'support_tokenization': True,
        })

import logging
import requests
from werkzeug import urls

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)

API_URL = "https://api.pagos360.com"
API_TEST_URL = "https://api.sandbox.pagos360.com"


class PaymentProvider(models.Model):
    _inherit = 'payment.provider'

    code = fields.Selection(
        selection_add=[('pagos360', "Pagos 360")], ondelete={'pagos360': 'set default'})
    pagos360_api_key = fields.Char(
        string="Api Key", required_if_provider='pagos360', groups='base.group_system')
    pagos360_test_api_key = fields.Char(
        string="Test Api Key", required_if_provider='pagos360', groups='base.group_system')

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
            _logger.exception("Unable to communicate with Pagos360: %s", url)
            raise ValidationError("Pagos360: " + _("Could not establish the connection to the API."))
        return response.json()
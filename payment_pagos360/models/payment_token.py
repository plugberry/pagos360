import logging

from odoo import _, fields, models, api
from requests.exceptions import RequestException

_logger = logging.getLogger(__name__)


class PaymentToken(models.Model):
    _inherit = 'payment.token'

    pagos360_adhesion_type = fields.Selection([('adhesion', 'CBU Adhesion'), ('card_adhesion', 'Card Adhesion')])
    pagos360_external_reference = fields.Char()
    pagos360_bank = fields.Char()
    pagos360_cbu_number = fields.Char()
    pagos360_card = fields.Char()
    pagos360_card_number = fields.Char()

    def _build_display_name(self, *args, max_length=34, should_pad=True, **kwargs):
        if self.provider_code != 'pagos360':
            return super()._build_display_name(*args, max_length=max_length, should_pad=should_pad, **kwargs)
        else:
            if self.pagos360_adhesion_type == 'card_adhesion':
                display_name = "Debito automático en Tarjeta: {} **** - {}".format(self.pagos360_card, self.pagos360_card_number)
            elif self.pagos360_adhesion_type == 'adhesion':
                display_name = "Debito automático en CBU: {} ****{}".format(self.pagos360_bank, self.pagos360_cbu_number[-5:])
            return display_name

    def write(self, values):
        res = super().write(values)
        if 'active' in values and values['active'] is False and not self.env.context.get('is_notification'):
            for rec in self.filtered(lambda x: x.provider_code == 'pagos360'):
                endpoint = 'adhesion' if rec.pagos360_adhesion_type == 'adhesion' else 'card-adhesion'
                id = rec.provider_ref
                try:
                    rec.provider_id._pagos360_make_request(f'/{endpoint}/{id}/cancel', method='PUT')
                except RequestException:
                    _logger.exception("Unable to delete token in PAGOS360")
        return res

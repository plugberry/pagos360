import requests
import logging
import pprint
import base64

from odoo import models, fields, _

_logger = logging.getLogger(__name__)


class AccountMove(models.Model):
    _inherit = 'account.move'

    pagos360_barcode = fields.Char(copy=False)
    pagos360_barcode_image = fields.Binary(copy=False, attachment=True)
    pagos360_rp_barcode = fields.Char(copy=False)
    pagos360_rp_barcode_image = fields.Char(copy=False)

    def _payment_barcode_request_pagos360(self):
        """Request barcode from Pagos360 for the given invoices.
        """
        provider_ids = self.env["payment.provider"].search([
            ("code", "=", "pagos360"),
            ('state', '!=', 'disabled'),
            ("company_id", "in", self.mapped('company_id').ids)])

        for invoice in self.filtered(lambda x: not x.pagos360_barcode and x.company_id in provider_ids.mapped('company_id') and x.amount_residual > 0):
            provider_id = provider_ids.filtered(lambda x: x.company_id == invoice.company_id)[0]
            payload = {"payment_request": {
                "description": f"Factura {invoice.name}",
                "first_due_date": invoice.invoice_date_due.strftime("%d-%m-%Y"),
                "first_total": invoice.amount_residual,
                "payer_name": invoice.partner_id.name,
                "external_reference": f"inv-{provider_id.id}-{invoice.id}",
            }}
            _logger.info(
                "Sending '/payment-request' request for link creation:\n%s",
                pprint.pformat(payload),
            )
            payment_data = provider_id._pagos360_make_request(
                "/payment-request", data=payload
            )

            if payment_data.get("barcode"):
                invoice.pagos360_barcode = payment_data.get("barcode")
                svg = requests.get(payment_data["barcode_url"]).content
                invoice.pagos360_barcode_image = base64.b64encode(svg)
            if payment_data.get("rapipago_barcode"):
                invoice.pagos360_rp_barcode = payment_data.get("rapipago_barcode")
                rp_svg = requests.get(payment_data["rapipago_barcode_url"]).content
                invoice.pagos360_rp_barcode_image = base64.b64encode(rp_svg)

    def action_post(self):
        res = super().action_post()
        invoices_to_process = self.filtered(lambda inv: inv.move_type == 'out_invoice' and inv.state == 'posted' and inv.payment_state in ['not_paid', 'partial'] and not inv.pagos360_barcode)
        if invoices_to_process:
            invoices_to_process._payment_barcode_request_pagos360()
        return res

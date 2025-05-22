import requests
import logging
import pprint
import base64

from odoo import models, fields, _

_logger = logging.getLogger(__name__)

class AccountMove(models.Model):
    _inherit = 'account.move'
    _description = 'Account Move with Pagos360 Integration'

    pagos360_barcode = fields.Char("Código de Barras")
    pagos360_barcode_image = fields.Binary(string="Pagos 360 Barcode")
    pagos360_rp_barcode = fields.Char("Código de Barras Rapipago")
    pagos360_rp_barcode_url = fields.Char("URL Código de Barras Rapipago")

    def _payment_barcode_request_pagos360(self, invoices_to_process):
        """Request barcode from Pagos360 for the given invoices.
        :param invoices_to_process: Recordset of account.move to process
        :type invoices_to_process: recordset of `account.move`
        """
        provider_ids = self.env["payment.provider"].search([
            ("code", "=", "pagos360"),
            ('state', '!=', 'disabled'),
            ("company_id", "in", self.mapped('company_id').ids)], limit=1)

        for invoice in invoices_to_process.filtered(lambda x: not x.pagos360_barcode and x.company_id in provider_ids.mapped('company_id') and x.amount_residual > 0):
            provider_id = provider_ids.filtered(lambda x: x.company_id == invoice.company_id)[0]

            if provider_id and provider_id.state != 'disabled':
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
                    invoice.pagos360_rp_barcode_url = payment_data.get("rapipago_barcode_url")
            else:
                _logger.warning(
                    "No provider found for Pagos360 or provider is disabled for company %s",
                    invoice.company_id.name,
                )

    def action_send_and_print(self):
        res = super(AccountMove, self).action_send_and_print()
        invoices_to_process = self.filtered(lambda inv: inv.move_type == 'out_invoice' and inv.state == 'posted' and inv.payment_state in ['not_paid', 'partial'] and not inv.transaction_ids and not inv.pagos360_barcode)
        if invoices_to_process:
            self._payment_barcode_request_pagos360(invoices_to_process)
        return res

import base64

import requests
from odoo import fields, models


class AccountMove(models.Model):
    _inherit = "account.move"

    pagos360_barcode = fields.Char(copy=False, help="Código de barras de PagoFacil/RapiPago generado por Pagos360")
    pagos360_barcode_image = fields.Binary(
        copy=False, attachment=True, help="Imagen SVG del código de barras de PagoFacil/RapiPago"
    )
    pagos360_rp_barcode = fields.Char(copy=False, help="Código de barras alternativo de RapiPago generado por Pagos360")
    pagos360_rp_barcode_image = fields.Binary(
        copy=False, attachment=True, help="Imagen SVG del código de barras alternativo de RapiPago"
    )
    pagos360_barcode_amount = fields.Float(copy=False, help="Monto asociado al código de barras de PagoFacil/RapiPago")

    def _payment_barcode_request_pagos360(self):
        """Request barcode from Pagos360 for the given invoices.

        This method creates a payment request in Pagos360 for each invoice and retrieves
        the associated barcodes (PagoFacil/RapiPago) that can be printed on the invoice.
        """
        provider_ids = self.env["payment.provider"].search(
            [
                ("code", "=", "pagos360"),
                ("state", "!=", "disabled"),
                ("company_id", "in", self.mapped("company_id").ids),
            ]
        )

        for invoice in self.filtered(
            lambda x: not x.pagos360_barcode
            and x.company_id in provider_ids.mapped("company_id")
            and x.amount_residual > 0
        ):
            provider_id = provider_ids.filtered(lambda x: x.company_id == invoice.company_id)[0]
            # Use invoice_date_due if available, otherwise fallback to invoice_date
            due_date = invoice.invoice_date_due or invoice.invoice_date
            payload = {
                "payment_request": {
                    "description": f"Factura {invoice.name}",
                    "first_due_date": due_date.strftime("%d-%m-%Y"),
                    "first_total": invoice.amount_residual,
                    "payer_name": invoice.partner_id.name,
                    "external_reference": f"inv-{provider_id.id}-{invoice.id}",
                }
            }

            payment_data = provider_id._pagos360_make_request("/payment-request", data=payload)

            if payment_data.get("barcode"):
                invoice.pagos360_barcode = payment_data.get("barcode")
                svg = requests.get(payment_data["barcode_url"], timeout=10).content
                invoice.pagos360_barcode_image = base64.b64encode(svg)
                invoice.pagos360_barcode_amount = payment_data.get("first_total", 0.0)
            if payment_data.get("rapipago_barcode") and payment_data.get("rapipago_barcode") != payment_data.get(
                "barcode"
            ):
                invoice.pagos360_rp_barcode = payment_data.get("rapipago_barcode")
                rp_svg = requests.get(payment_data["rapipago_barcode_url"], timeout=10).content
                invoice.pagos360_rp_barcode_image = base64.b64encode(rp_svg)

    def action_post(self):
        """Override to create Pagos360 barcodes when invoice is posted."""
        res = super().action_post()
        self._create_pagos360_barcode()
        return res

    def _create_pagos360_barcode(self):
        """Create Pagos360 barcode for customer invoices that are posted and unpaid."""
        invoices_to_process = self.filtered(
            lambda inv: inv.move_type == "out_invoice"
            and inv.state == "posted"
            and inv.payment_state in ["not_paid", "partial"]
            and not inv.pagos360_barcode
        )
        if invoices_to_process:
            invoices_to_process._payment_barcode_request_pagos360()

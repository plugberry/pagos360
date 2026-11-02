import logging
from datetime import timedelta

import requests
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools import BinaryBytes

_logger = logging.getLogger(__name__)

# Automatic requests of the same invoice before the cron gives up and tells the responsible.
BARCODE_MAX_TRIES = 5
# Minutes between two requests of the same invoice, so a slow Pagos360 does not starve the others.
BARCODE_RETRY_MINUTES = 50


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
    pagos360_barcode_pending_since = fields.Datetime(
        copy=False,
        index="btree_not_null",
        help="Since when the invoice waits for its Pagos360 barcode. Empty if it does not wait for it.",
    )
    pagos360_barcode_alerted = fields.Boolean(
        copy=False, help="The responsible was already told that the invoice has no barcode."
    )
    pagos360_barcode_last_try = fields.Datetime(copy=False, help="Last time the Pagos360 barcode was requested.")
    pagos360_barcode_tries = fields.Integer(
        copy=False, help="Requests of the Pagos360 barcode that did not return it since the invoice was posted."
    )
    pagos360_payment_request_id = fields.Integer(copy=False, help="Id of the payment request in Pagos360.")
    pagos360_barcode_url = fields.Char(copy=False, help="Where Pagos360 serves the barcode image.")
    pagos360_rp_barcode_url = fields.Char(copy=False, help="Where Pagos360 serves the RapiPago barcode image.")
    pagos360_sending_data = fields.Json(
        copy=False, help="Sending data kept aside while the invoice waits for its Pagos360 barcode."
    )

    def _pagos360_get_providers(self):
        """Active Pagos360 providers with the API key of their mode, live or test.

        In 20 a provider is born active and is copied to every new company: without its key it
        is not in use. A test database (neutralized to test mode) works as production if the
        provider has its test key.
        """
        return (
            self.env["payment.provider"]
            .sudo()
            .search([("code", "=", "pagos360"), ("company_id", "in", self.company_id.ids)])
            .filtered(lambda provider: provider._pagos360_get_api_key())
        )

    def _pagos360_has_barcode(self):
        """The barcode and the images the invoice report prints are all there."""
        self.ensure_one()
        return bool(
            self.pagos360_barcode
            and self.pagos360_barcode_image
            and (not self.pagos360_rp_barcode or self.pagos360_rp_barcode_image)
        )

    def _pagos360_needs_barcode(self):
        """Customer invoices, posted and unpaid, without a barcode yet."""
        return self.filtered(
            lambda inv: inv.move_type == "out_invoice"
            and inv.state == "posted"
            and inv.payment_state in ["not_paid", "partial"]
            and not inv._pagos360_has_barcode()
        )

    def _pagos360_write_payment_request(self, payment_data):
        """Keep what Pagos360 returned for the payment request of the invoice."""
        self.ensure_one()
        vals = {"pagos360_payment_request_id": payment_data.get("id") or self.pagos360_payment_request_id}
        if payment_data.get("barcode"):
            vals.update(
                pagos360_barcode=payment_data["barcode"],
                pagos360_barcode_url=payment_data.get("barcode_url"),
                pagos360_barcode_amount=payment_data.get("first_total", 0.0),
            )
        if payment_data.get("rapipago_barcode") and payment_data.get("rapipago_barcode") != payment_data.get("barcode"):
            vals.update(
                pagos360_rp_barcode=payment_data["rapipago_barcode"],
                pagos360_rp_barcode_url=payment_data.get("rapipago_barcode_url"),
            )
        self.write(vals)

    def _pagos360_request_payment_request(self):
        """Create the payment request of the invoice in Pagos360, or read it back if it exists.

        Pagos360 does not reject a repeated `external_reference` (it is a free text reference), so
        once a payment request exists it is read by its id and never created again.
        """
        self.ensure_one()
        provider_id = self._pagos360_get_providers().filtered(lambda x: x.company_id == self.company_id)[:1]
        if not provider_id or self.amount_residual <= 0 or self.pagos360_barcode:
            return
        if self.pagos360_payment_request_id:
            response = provider_id._pagos360_make_request(
                "/payment-request/%s" % self.pagos360_payment_request_id, method="GET"
            )
            self._pagos360_write_payment_request(response.get("data", response))
            return
        # Use invoice_date_due if available, otherwise fallback to invoice_date
        due_date = self.invoice_date_due or self.invoice_date
        payload = {
            "payment_request": {
                "description": f"Factura {self.name}",
                "first_due_date": due_date.strftime("%d-%m-%Y"),
                "first_total": self.amount_residual,
                "payer_name": self.partner_id.name,
                "external_reference": f"inv-{provider_id.id}-{self.id}",
            }
        }
        self._pagos360_write_payment_request(provider_id._pagos360_make_request("/payment-request", data=payload))

    def _pagos360_download_barcode_images(self):
        """Download the barcode images that are still missing."""
        self.ensure_one()
        if self.pagos360_barcode and not self.pagos360_barcode_image and self.pagos360_barcode_url:
            self.pagos360_barcode_image = BinaryBytes(requests.get(self.pagos360_barcode_url, timeout=10).content)
        if self.pagos360_rp_barcode and not self.pagos360_rp_barcode_image and self.pagos360_rp_barcode_url:
            self.pagos360_rp_barcode_image = BinaryBytes(requests.get(self.pagos360_rp_barcode_url, timeout=10).content)

    def _payment_barcode_request_pagos360(self):
        """Request barcode from Pagos360 for the given invoices.

        This method creates a payment request in Pagos360 for each invoice and retrieves
        the associated barcodes (PagoFacil/RapiPago) that can be printed on the invoice.
        """
        for invoice in self.filtered(lambda inv: not inv._pagos360_has_barcode()):
            invoice._pagos360_request_payment_request()
            invoice._pagos360_download_barcode_images()

    def _post(self, soft=True):
        """Override to queue the Pagos360 barcode request: posting does not wait for Pagos360.

        On `_post` and not `action_post`, so the invoices posted by the auto-post cron,
        subscriptions or other flows get their barcode too.
        """
        posted = super()._post(soft=soft)
        posted._create_pagos360_barcode()
        return posted

    def _create_pagos360_barcode(self):
        """Mark the invoices that need a Pagos360 barcode and wake up the cron that requests it."""
        companies = self._pagos360_get_providers().company_id
        invoices = self._pagos360_needs_barcode().filtered(
            lambda inv: not inv.pagos360_barcode_pending_since and inv.company_id in companies
        )
        if invoices:
            invoices.write(
                {
                    "pagos360_barcode_pending_since": fields.Datetime.now(),
                    "pagos360_barcode_alerted": False,
                    "pagos360_barcode_tries": 0,
                }
            )
            self.env.ref("pagos360_invoice_barcode.ir_cron_pagos360_invoice_barcode")._trigger()

    def _pagos360_stop_waiting(self):
        """The invoices no longer wait for a barcode: put back in the sending queue the ones that were in it."""
        for invoice in self:
            vals = {
                "pagos360_barcode_pending_since": False,
                "pagos360_barcode_alerted": False,
                "pagos360_barcode_tries": 0,
            }
            if invoice.pagos360_sending_data:
                vals["pagos360_sending_data"] = False
                if invoice.state == "posted":
                    vals["sending_data"] = invoice.pagos360_sending_data
            invoice.write(vals)

    def _pagos360_wait_barcode_to_send(self):
        """Take the invoices out of the sending queue until they have their barcode.

        Left in the queue, the sending cron would pick the same oldest invoices again and again and
        send none of the others.
        """
        for invoice in self:
            invoice.write(
                {
                    "pagos360_sending_data": invoice.sending_data
                    or {"author_user_id": self.env.user.id, "author_partner_id": self.env.user.partner_id.id},
                    "sending_data": False,
                }
            )

    def _pagos360_request_pending_barcodes(self):
        """Request the barcode of each pending invoice. A failure leaves the invoice pending.

        :return: the invoices that stopped waiting
        """
        companies = self._pagos360_get_providers().company_id
        done = self.browse()
        for invoice in self.filtered("pagos360_barcode_pending_since"):
            if not invoice._pagos360_needs_barcode() or invoice.company_id not in companies:
                invoice._pagos360_stop_waiting()
                done |= invoice
                continue
            invoice.write(
                {
                    "pagos360_barcode_last_try": fields.Datetime.now(),
                    "pagos360_barcode_tries": invoice.pagos360_barcode_tries + 1,
                }
            )
            try:
                # Two steps, so a failed image download does not undo the payment request.
                with self.env.cr.savepoint():
                    invoice._pagos360_request_payment_request()
                with self.env.cr.savepoint():
                    invoice._pagos360_download_barcode_images()
            except (ValidationError, requests.RequestException):
                _logger.warning("Pagos360 barcode request failed for invoice %s", invoice.id, exc_info=True)
                continue
            if invoice._pagos360_has_barcode():
                invoice._pagos360_stop_waiting()
                done |= invoice
        return done

    def _pagos360_alert_user(self):
        """The responsible of the invoice, or who created it, if it is a real internal user."""
        self.ensure_one()
        root = self.env.ref("base.user_root")
        for user in self.invoice_user_id | self.create_uid:
            if user != root and not user.share and user.active:
                return user
        return self.env["res.users"]

    def _pagos360_alert_missing_barcode(self):
        """Tell the responsible, once, about the invoices whose automatic requests ran out."""
        for invoice in self.filtered(
            lambda inv: inv.pagos360_barcode_pending_since
            and inv.pagos360_barcode_tries >= BARCODE_MAX_TRIES
            and not inv.pagos360_barcode_alerted
        ):
            note = _(
                "Pagos360 did not return the barcode of this invoice after %(tries)s tries, and it is"
                " not requested automatically any more. The invoice cannot be sent or printed without"
                " it: sending or printing it requests the barcode again.",
                tries=invoice.pagos360_barcode_tries,
            )
            if user := invoice._pagos360_alert_user():
                invoice.activity_schedule(
                    "mail.mail_activity_data_todo",
                    summary=_("Pagos360 barcode missing"),
                    note=note,
                    user_id=user.id,
                )
            else:
                invoice.message_post(body=note)
            invoice.pagos360_barcode_alerted = True

    @api.model
    def _cron_pagos360_request_barcodes(self):
        """Request the barcodes of the pending invoices, one commit per invoice.

        Each invoice is tried at most once per BARCODE_RETRY_MINUTES, least recently tried first, so a
        slow Pagos360 does not keep the cron retrying the same invoices, and at most BARCODE_MAX_TRIES
        times.
        """
        IrCron = self.env["ir.cron"]
        retry_before = fields.Datetime.now() - timedelta(minutes=BARCODE_RETRY_MINUTES)
        invoices = self.search(
            [
                ("pagos360_barcode_pending_since", "!=", False),
                ("pagos360_barcode_tries", "<", BARCODE_MAX_TRIES),
                "|",
                ("pagos360_barcode_last_try", "=", False),
                ("pagos360_barcode_last_try", "<", retry_before),
            ],
            order="pagos360_barcode_last_try asc nulls first, id",
        )
        IrCron._commit_progress(remaining=len(invoices))
        to_send = False
        for invoice in invoices:
            invoice = invoice.try_lock_for_update()
            if invoice and invoice._pagos360_request_pending_barcodes().sending_data:
                to_send = True
            invoice._pagos360_alert_missing_barcode()
            if not IrCron._commit_progress(1):
                break
        if to_send:
            self.env.ref("account.ir_cron_account_move_send")._trigger()

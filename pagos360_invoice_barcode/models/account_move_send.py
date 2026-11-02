from odoo import _, api, models
from odoo.exceptions import UserError


class AccountMoveSend(models.AbstractModel):
    _inherit = "account.move.send"

    @api.model
    def _generate_and_send_invoices(
        self, moves, from_cron=False, allow_raising=True, allow_fallback_pdf=False, **custom_settings
    ):
        """Override to send an invoice only with its Pagos360 barcode.

        A missing barcode is requested now. If Pagos360 still does not return it, sending one
        invoice by hand stops with a message; any other send leaves the invoice out of the sending
        queue until the barcode cron gets it and puts it back.
        """
        if waiting := moves._pagos360_request_barcodes_now():
            if len(moves) == 1 and not from_cron and allow_raising:
                raise UserError(
                    _(
                        "Pagos360 has not returned the barcode of %(invoices)s yet, so it cannot be"
                        " sent without it. The request is retried automatically; try again in a few"
                        " minutes.",
                        invoices=", ".join(waiting.mapped("name")),
                    )
                )
            waiting._pagos360_wait_barcode_to_send()
            moves -= waiting
        return super()._generate_and_send_invoices(
            moves,
            from_cron=from_cron,
            allow_raising=allow_raising,
            allow_fallback_pdf=allow_fallback_pdf,
            **custom_settings,
        )

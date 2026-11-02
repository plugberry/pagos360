from odoo import _, models
from odoo.exceptions import UserError


class IrActionsReport(models.Model):
    _inherit = "ir.actions.report"

    def _pre_render_qweb_pdf(self, report_ref, res_ids=None, data=None):
        """Override to print an invoice only with its Pagos360 barcode: a missing one is requested
        now, and if Pagos360 still does not return it the print stops."""
        if self._is_invoice_report(report_ref) and res_ids:
            pending = self.env["account.move"].browse(res_ids).filtered("pagos360_barcode_pending_since")
            if pending:
                pending.try_lock_for_update()._pagos360_request_pending_barcodes()
                if waiting := pending.filtered("pagos360_barcode_pending_since"):
                    raise UserError(
                        _(
                            "Pagos360 has not returned the barcode of %(invoices)s yet, so it cannot be"
                            " printed without it. Try again in a few minutes.",
                            invoices=", ".join(waiting.mapped("name")),
                        )
                    )
        return super()._pre_render_qweb_pdf(report_ref, res_ids=res_ids, data=data)

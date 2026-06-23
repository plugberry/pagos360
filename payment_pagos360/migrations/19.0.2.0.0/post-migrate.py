import logging

from odoo.tools import SQL

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    """Migra validity_days → pagos360_coupon_validity_days y levanta pagos360_cut_days desde ir.config_parameter."""
    _logger.info("Running post-migration for version %s", version)

    cr.execute(
        SQL(
            """
            UPDATE payment_provider
               SET pagos360_coupon_validity_days = COALESCE(validity_days, 15),
                   pagos360_debit_execution_days = COALESCE(validity_days, 0) + 3,
                   pagos360_debit_use_invoice_due = False
             WHERE code = 'pagos360'
            """
        )
    )
    cr.execute(SQL("SELECT value FROM ir_config_parameter WHERE key = 'pagos360.cut_day'"))
    row = cr.fetchone()
    cr.execute(
        SQL(
            "UPDATE payment_provider SET pagos360_cut_days = %(cut_day)s WHERE code = 'pagos360'",
            cut_day=str(int(row[0])) if row else "19",
        )
    )

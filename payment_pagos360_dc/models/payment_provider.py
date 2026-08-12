from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class PaymentProvider(models.Model):
    _inherit = "payment.provider"

    pagos360_debit_execution_days = fields.Integer(
        string="Días para ejecutar el débito (CBU/TC)",
        default=3,
        help="Días hábiles a esperar desde hoy para ejecutar el débito. "
        "Mínimo 3 (requerimiento técnico de Pagos360). La fecha final se ajusta al próximo día hábil.",
    )
    pagos360_coupon_validity_days = fields.Integer(
        string="Días de validez del cupón (Pago Fácil / Rapipago)",
        default=15,
        help="Días que el cliente final tiene para pagar el cupón antes de que venza.",
    )
    pagos360_cut_days = fields.Char(
        string="Días de corte (débito en tarjeta)",
        default="19",
        help="Días del mes (separados por coma, entre 1 y 28) a partir de los cuales el débito en "
        "tarjeta se imputa al período siguiente. Ej: '10,20'. "
        "Reemplaza al parámetro de sistema 'pagos360.cut_day'.",
    )
    pagos360_debit_use_invoice_due = fields.Boolean(
        string="Debitar al vencimiento de la factura (CBU)",
        default=False,
        help="Si está activado y la transacción tiene facturas con fecha de vencimiento, "
        "el débito CBU se ejecuta el primer día hábil a partir de la fecha de vencimiento más "
        "próxima. Si esa fecha no supera el mínimo técnico de 3 días hábiles, se usa "
        "el mínimo como fallback.",
    )

    @api.constrains("pagos360_debit_execution_days", "pagos360_coupon_validity_days")
    def _check_pagos360_due_days(self):
        for rec in self.filtered(lambda p: p.code == "pagos360"):
            if rec.pagos360_debit_execution_days < 3:
                raise ValidationError(
                    _("Los días de ejecución del débito deben ser al menos 3 (mínimo técnico de Pagos360).")
                )
            if rec.pagos360_coupon_validity_days < 1:
                raise ValidationError(_("La validez del cupón debe ser al menos 1 día."))

    @api.constrains("pagos360_cut_days")
    def _check_pagos360_cut_days(self):
        for rec in self.filtered(lambda p: p.code == "pagos360" and p.pagos360_cut_days):
            for raw_day in rec.pagos360_cut_days.split(","):
                day = raw_day.strip()
                if not day.isdigit() or not (1 <= int(day) <= 28):
                    raise ValidationError(
                        _("Los días de corte deben ser números entre 1 y 28 (válidos en cualquier mes).")
                    )

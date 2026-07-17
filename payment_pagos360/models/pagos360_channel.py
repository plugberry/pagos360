from odoo import fields, models


class Pagos360Channel(models.Model):
    _name = "pagos360.channel"
    _description = "Pagos360 Payment Channel"
    _order = "name"

    name = fields.Char(required=True, translate=True)
    code = fields.Char(
        required=True,
        help="Technical code sent to the Pagos360 API (e.g. 'banelco_pmc').",
    )

    _code_uniq = models.Constraint(
        "unique(code)",
        "The channel code must be unique.",
    )

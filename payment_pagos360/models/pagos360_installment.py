from odoo import _, api, fields, models
from odoo.exceptions import UserError


class Pagos360Installment(models.Model):
    _name = "pagos360.installment"
    _description = "Pagos360 Installment"
    _order = "number"

    number = fields.Integer(required=True)
    name = fields.Char(compute="_compute_name", store=True)

    _number_uniq = models.Constraint(
        "unique(number)",
        "The installment number must be unique.",
    )

    @api.depends("number")
    def _compute_name(self):
        for rec in self:
            rec.name = str(rec.number)

    @api.model
    def _get_or_create(self, numbers):
        """Return the installment records for ``numbers``, creating any that don't exist yet."""
        numbers = {int(n) for n in numbers}
        if not numbers:
            return self.browse()
        existing = self.search([("number", "in", list(numbers))])
        missing = numbers - set(existing.mapped("number"))
        created = self.create([{"number": n} for n in missing]) if missing else self.browse()
        return existing | created

    @api.model
    def name_create(self, name):
        """Allow quick-creating an installment by typing its number in the tags widget."""
        value = (name or "").strip()
        if not value.isdigit() or int(value) <= 0:
            raise UserError(_("The installment must be a positive number."))
        record = self._get_or_create([int(value)])
        return record.id, record.display_name

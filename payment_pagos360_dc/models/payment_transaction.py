import logging
from datetime import date, timedelta

from dateutil.relativedelta import relativedelta
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


class PaymentTransaction(models.Model):
    _inherit = "payment.transaction"

    pagos360_child_amount = fields.Float(
        string="Importe real de la transacción hija (Pagos360)",
        help="Importe original preservado cuando la operación se convierte a `validation` "
        "(flujo de adhesión). Se usa como importe de la transacción hija `online_token` "
        "que se dispara una vez firmada la adhesión.",
    )
    pagos360_child_currency_id = fields.Many2one(
        "res.currency",
        string="Moneda de la transacción hija (Pagos360)",
        help="Moneda original de la transacción antes de convertirla a `validation` (que "
        "pasa a usar la moneda de validación del provider). Se usa junto con "
        "`pagos360_child_amount` para que la transacción hija cobre en la moneda correcta.",
    )
    pagos360_debit_execution_date = fields.Date(
        string="Fecha de débito al cliente",
        readonly=True,
    )

    # -------------------------------------------------------------------------
    # Transacciones hijas para adhesión/tokenización
    # -------------------------------------------------------------------------

    @api.model
    def _get_specific_create_values(self, provider_code, values):
        res = super()._get_specific_create_values(provider_code, values)
        if provider_code != "pagos360":
            return res
        if values.get("operation") not in ("online_redirect", "online_direct"):
            return res
        provider = self.env["payment.provider"].browse(values.get("provider_id"))
        if not (values.get("tokenize") and provider.pagos360_form_url):
            return res
        res.update(
            {
                "operation": "validation",
                "tokenize": True,
                "pagos360_child_amount": values.get("amount", 0.0),
                "pagos360_child_currency_id": values.get("currency_id"),
                "amount": 0.0,
                "currency_id": provider._get_validation_currency().id,
            }
        )
        return res

    def _process_notification_data(self, notification_data):
        super()._process_notification_data(notification_data)
        if self.provider_code != "pagos360":
            return
        if self.operation == "validation" and notification_data.get("type") == "signed" and self.token_id:
            self._pagos360_spawn_child_charge()

    def _pagos360_spawn_child_charge(self):
        """Create and trigger the child charge transaction of a signed Pagos360 adhesion.

        Llamado desde `_process_notification_data` cuando una transacción `validation` de
        Pagos360 queda firmada. Usa `pagos360_child_amount` (preservado al crear la transacción
        por `_get_specific_create_values`) como importe del cobro y dispara `_send_payment_request`
        sobre la hija para ejecutar el débito real.
        """
        self.ensure_one()
        if self.provider_code != "pagos360":
            return
        if self.operation != "validation":
            return
        if self.source_transaction_id:
            return
        if self.child_transaction_ids.filtered(lambda c: c.operation == "online_token"):
            return
        if not self.token_id or not self.token_id.exists() or not self.token_id.active:
            raise ValidationError(
                _(
                    "PAGOS360: no se puede iniciar el cobro de adhesión de %s: el token no está "
                    "disponible (fue eliminado o archivado)."
                )
                % self.reference
            )

        amount = self.pagos360_child_amount
        if not amount:
            _logger.info(
                "PAGOS360: se omite el spawn de la transacción hija para %s — pagos360_child_amount está vacío.",
                self.reference,
            )
            return

        child = self._create_child_transaction(
            amount,
            operation="online_token",
            token_id=self.token_id.id,
            currency_id=(self.pagos360_child_currency_id or self.currency_id).id,
            **self._pagos360_get_child_link_vals(),
        )
        _logger.info(
            "PAGOS360: se creó la transacción hija %s sobre el token id=%s a partir de la transacción de validación %s.",
            child.reference,
            self.token_id.id,
            self.reference,
        )
        try:
            child._send_payment_request()
        except Exception as e:
            _logger.info(
                "PAGOS360: falló el cobro de la transacción hija %s de la adhesión %s: %s",
                child.reference,
                self.reference,
                e,
            )
            if child.state not in ("error", "done"):
                child._set_error(_("PAGOS360: no se pudo ejecutar el cobro de la adhesión: %s") % e)

    def _pagos360_get_child_link_vals(self):
        """Comandos M2M para propagar sale_order_ids/invoice_ids del padre a la hija."""
        self.ensure_one()
        link_vals = {}
        if "sale_order_ids" in self._fields and self.sale_order_ids:
            link_vals["sale_order_ids"] = [(6, 0, self.sale_order_ids.ids)]
        if "invoice_ids" in self._fields and self.invoice_ids:
            link_vals["invoice_ids"] = [(6, 0, self.invoice_ids.ids)]
        return link_vals

    # -------------------------------------------------------------------------
    # Validity days
    # -------------------------------------------------------------------------

    def _pagos360_get_invoice_due_date(self):
        """Fecha de vencimiento futura más próxima entre las facturas posted asociadas.

        None si no hay facturas elegibles o todas tienen vencimiento pasado o igual a hoy.
        """
        today = date.today()
        invoices = self.invoice_ids.filtered(
            lambda m: m.move_type in ("out_invoice", "out_refund")
            and m.state == "posted"
            and m.invoice_date_due
            and m.invoice_date_due > today
        )
        if not invoices:
            return None
        return min(invoices.mapped("invoice_date_due"))

    def get_coupon_due_values(self):
        """Vencimiento del cupón de efectivo (payment-request)."""
        due = fields.Datetime.now() + timedelta(days=self.provider_id.pagos360_coupon_validity_days)
        return due, self.amount

    def get_debit_due_date(self):
        """Fecha de ejecución del débito CBU.

        Toggle OFF -> next_business_day(hoy, days=pagos360_debit_execution_days).

        Toggle ON:
          min_day = next_business_day(hoy, days=3)   <- mínimo técnico fijo de Pagos360
          Con factura futura:
            min_day >= invoice_due -> retorna min_day
            min_day <  invoice_due -> retorna next_business_day(invoice_due - 1 día, days=1)
          Sin facturas elegibles  -> retorna min_day
        """
        provider = self.provider_id

        if provider.pagos360_debit_use_invoice_due:
            min_day_raw = self._pagos360_next_business_day(date.today(), days=3)
            invoice_due = self._pagos360_get_invoice_due_date()
            if invoice_due:
                min_day = fields.Date.from_string(min_day_raw[:10])
                if min_day >= invoice_due:
                    return min_day_raw
                return self._pagos360_next_business_day(invoice_due - timedelta(days=1), days=1)
            return min_day_raw

        return self._pagos360_next_business_day(date.today(), days=provider.pagos360_debit_execution_days)

    def _pagos360_prepare_preference_request_payload(self):
        payload = super()._pagos360_prepare_preference_request_payload()
        due_date, total = self.get_coupon_due_values()
        payload["payment_request"]["first_due_date"] = due_date.strftime("%d-%m-%Y")
        payload["payment_request"]["first_total"] = total
        return payload

    def _pagos360_debit_request(self):
        """Full reimplementation, not a `super()` override with a tweak.

        The base method (`payment_pagos360`) computes `first_due_date` inline from
        `self.get_first_due_values()` + `self._pagos360_next_business_day()` — there's no
        separate overridable hook for "the due date" alone. Routing through
        `get_first_due_values()` here would mean this module keeps calling it, which
        the validity-days design explicitly rules out (that method's semantics —
        cash-coupon validity — no longer apply to CBU debit due dates once this module
        is installed). If `payment_pagos360._pagos360_debit_request` changes its request
        payload shape, mirror the change here too.
        """
        execution_date_raw = self.get_debit_due_date()
        execution_date = fields.Date.from_string(execution_date_raw[:10])
        self.pagos360_debit_execution_date = execution_date
        data = {
            "debit_request": {
                "description": _("Payment %s") % self.company_id.display_name,
                "first_total": self.amount,
                "first_due_date": execution_date.strftime("%d-%m-%Y"),
                "adhesion_id": int(self.token_id.provider_ref),
            }
        }
        return self.provider_id._pagos360_make_request("debit-request", data=data, method="POST")

    def _pagos360_card_debit_request(self):
        """Full reimplementation — see `_pagos360_debit_request` for why `super()` isn't used.

        If `payment_pagos360._pagos360_card_debit_request` changes its request payload
        shape, mirror the change here too.
        """
        today = fields.Date.today()
        cut_days = sorted(int(d.strip()) for d in self.provider_id.pagos360_cut_days.split(","))
        future_cuts = [d for d in cut_days if d >= today.day]
        if future_cuts:
            execution_date = today.replace(day=future_cuts[0])
        else:
            next_month = today + relativedelta(months=1)
            execution_date = next_month.replace(day=cut_days[0])
        self.pagos360_debit_execution_date = execution_date

        data = {
            "card_debit_request": {
                "description": _("Payment %s") % self.company_id.display_name,
                "amount": self.amount,
                "month": execution_date.month,
                "year": execution_date.year,
                "card_adhesion_id": int(self.token_id.provider_ref),
            }
        }
        return self.provider_id._pagos360_make_request("card-debit-request", data=data, method="POST")

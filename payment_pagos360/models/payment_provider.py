import logging
import secrets
from urllib.parse import urlsplit

import requests
from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools.urls import urljoin

from .. import const
from ..controllers.main import Pagos360Controller

_logger = logging.getLogger(__name__)


class PaymentProvider(models.Model):
    _inherit = "payment.provider"

    code = fields.Selection(selection_add=[("pagos360", "PAGOS360")], ondelete={"pagos360": "set default"})
    pagos360_api_key = fields.Char(string="Api Key (PAGOS360)", groups="base.group_system")
    pagos360_test_api_key = fields.Char(string="Test Api Key (PAGOS360)", groups="base.group_system")
    pagos360_form_url = fields.Char("Link formulario debito automático")
    pagos360_webhook_token = fields.Char(
        string="Webhook Token (PAGOS360)",
        groups="base.group_system",
        copy=False,
        help="Secret appended to the webhook URL registered in Pagos360 so the endpoint can "
        "reject requests that don't carry it. Generated automatically by 'Ensure Webhook'.",
    )

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
        "el débito CBU se ejecuta el día hábil siguiente a la fecha de vencimiento más "
        "próxima. Si esa fecha no supera el mínimo técnico de Pagos360, se usa el "
        "mínimo como fallback.",
    )
    # Segundo vencimiento: feature futura (inactiva)
    second_validity_days = fields.Integer(default=30)
    second_due_fees = fields.Float(string="Surcharge", default=10)

    pagos360_excluded_channel_ids = fields.Many2many(
        "pagos360.channel",
        "pagos360_provider_excluded_channel_rel",
        "provider_id",
        "channel_id",
        string="Excluded Channels",
        help="Channels removed from the options offered to the payer on the Pagos360 coupon.",
    )
    pagos360_excluded_installment_ids = fields.Many2many(
        "pagos360.installment",
        "pagos360_provider_excluded_installment_rel",
        "provider_id",
        "installment_id",
        string="Excluded Installments",
        help="Installments removed from the options offered to the payer on the Pagos360 coupon.",
    )
    pagos360_excluded_card_brand_ids = fields.Many2many(
        "pagos360.card.brand",
        "pagos360_provider_excl_brand_rel",
        "provider_id",
        "card_brand_id",
        string="Excluded Card Brands",
        help="Card brands removed from the options offered to the payer on the Pagos360 coupon.",
    )

    pagos360_available_installment_ids = fields.Many2many(
        "pagos360.installment",
        "pagos360_provider_available_installment_rel",
        "provider_id",
        "installment_id",
        string="Available Installments",
        help="Installments the merchant actually has enabled in Pagos360, fetched from the API. "
        "Only these can be excluded.",
    )
    pagos360_available_card_brand_ids = fields.Many2many(
        "pagos360.card.brand",
        "pagos360_provider_avail_brand_rel",
        "provider_id",
        "card_brand_id",
        string="Available Card Brands",
        help="Card brands the merchant actually has enabled in Pagos360, fetched from the API. "
        "Only these can be excluded.",
    )

    # Domains for the exclusion tag widgets, built server-side so referencing the invisible
    # available m2m fields in the view domain is robust across the web client.
    pagos360_excludable_installment_domain = fields.Char(compute="_compute_pagos360_excludable_domains")
    pagos360_excludable_card_brand_domain = fields.Char(compute="_compute_pagos360_excludable_domains")

    @api.depends("pagos360_available_installment_ids", "pagos360_available_card_brand_ids")
    def _compute_pagos360_excludable_domains(self):
        for provider in self:
            provider.pagos360_excludable_installment_domain = repr(
                [("id", "in", provider.pagos360_available_installment_ids.ids)]
            )
            provider.pagos360_excludable_card_brand_domain = repr(
                [("id", "in", provider.pagos360_available_card_brand_ids.ids)]
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

    @api.constrains("allow_tokenization", "pagos360_form_url")
    def _check_pagos360_form_url_required(self):
        for provider in self.filtered(lambda p: p.code == "pagos360" and p.state in ("enabled", "test")):
            if provider.allow_tokenization and not provider.pagos360_form_url:
                raise ValidationError(
                    _("A Pagos 360 adhesion form URL is required when 'Allow Saving Payment Methods' is enabled.")
                )

    def _pagos360_get_coupon_exclusions(self):
        """Build the excluded_* payload entries for a payment request from the M2m config.

        Only includes keys that have values, so an empty selection sends nothing.

        :return: The exclusions to merge into the ``payment_request`` payload.
        :rtype: dict
        """
        self.ensure_one()
        exclusions = {}
        if self.pagos360_excluded_channel_ids:
            exclusions["excluded_channels"] = self.pagos360_excluded_channel_ids.mapped("code")
        if self.pagos360_excluded_installment_ids:
            exclusions["excluded_installments"] = self.pagos360_excluded_installment_ids.mapped("number")
        # Pagos360 identifies card brands by a numeric code (e.g. "39"=Visa), not the brand name:
        # sending anything else is silently ignored by the API. The code is only known after a
        # sync, so brands without one (e.g. migrated but never re-synced) are skipped.
        card_brand_codes = [c for c in self.pagos360_excluded_card_brand_ids.mapped("code") if c]
        if card_brand_codes:
            exclusions["excluded_card_brands"] = card_brand_codes
        return exclusions

    def _pagos360_fetch_available_methods(self):
        """Fetch the brands/installments the merchant has enabled from the Pagos360 API.

        Uses the amount-dependent ``channel-installments`` helper with a reference amount; we
        only need the available installment numbers and the brand ``{name, code}`` pairs, not the
        financial figures. Response shape: a list of ``{name, code, installments: [{installments, ...}]}``.

        :return: (set of installment numbers, list of ``{'name', 'code'}`` brand dicts)
        :rtype: tuple(set[int], list[dict])
        """
        self.ensure_one()
        data = self._pagos360_make_request(
            "/helper/channel-installments/%s" % const.AVAILABLE_METHODS_REFERENCE_AMOUNT,
            method="GET",
        )
        installment_numbers = set()
        brands = []
        for brand in data or []:
            name = brand.get("name")
            if name:
                brands.append({"name": name, "code": str(brand.get("code") or "").strip()})
            for plan in brand.get("installments") or []:
                number = plan.get("installments")
                if number:
                    installment_numbers.add(int(number))
        return installment_numbers, brands

    def action_pagos360_sync_available_methods(self):
        """Refresh the available installments/brands from Pagos360 and prune stale exclusions."""
        self.ensure_one()
        installment_numbers, brands_data = self._pagos360_fetch_available_methods()

        installments = self.env["pagos360.installment"]._get_or_create(installment_numbers)
        brands = self.env["pagos360.card.brand"]._upsert(brands_data)

        # Keep only the exclusions that are still available.
        kept_installments = self.pagos360_excluded_installment_ids & installments
        kept_brands = self.pagos360_excluded_card_brand_ids & brands
        self.write(
            {
                "pagos360_available_installment_ids": [Command.set(installments.ids)],
                "pagos360_available_card_brand_ids": [Command.set(brands.ids)],
                "pagos360_excluded_installment_ids": [Command.set(kept_installments.ids)],
                "pagos360_excluded_card_brand_ids": [Command.set(kept_brands.ids)],
            }
        )
        _logger.info(
            "Pagos360 provider %s: fetched %s available card brands and %s installment plans.",
            self.id,
            len(brands),
            len(installments),
        )
        # 'next': act_window_close is what makes the web client reload the record after the
        # notification closes — without it the toast shows but the available_*/excluded_* fields
        # on screen stay stale. Same pattern as ensure_webhook() above.
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "message": _(
                    "Se sincronizaron %(installments)s cuotas y %(brands)s marcas de tarjeta disponibles.",
                    installments=len(installments),
                    brands=len(brands),
                ),
                "sticky": False,
                "type": "success",
                "next": {"type": "ir.actions.act_window_close"},
            },
        }

    def _pagos360_get_api_url(self):
        self.ensure_one()
        if self.state == "enabled":
            return const.API_URL
        else:
            return const.API_TEST_URL

    def _pagos360_get_api_key(self):
        self.ensure_one()
        if self.state == "enabled":
            return self.sudo().pagos360_api_key
        else:
            return self.sudo().pagos360_test_api_key

    def _pagos360_make_request(self, endpoint, data=None, method="POST"):
        self.ensure_one()
        url = urljoin(self._pagos360_get_api_url(), endpoint)

        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._pagos360_get_api_key()}",
            "Content-Type": "application/json",
        }
        response = None
        try:
            response = requests.request(method, url, json=data, headers=headers, timeout=60)
            response.raise_for_status()
        except requests.exceptions.RequestException:
            response_text = response.text if response is not None else "No response"
            _logger.exception("Unable to communicate with Pagos360: %s", url)
            _logger.error("send data data: %s" % str(data))
            _logger.error("response.text: %s" % response_text)
            raise ValidationError(
                "Pagos360: {error_title} \n ref: {error_ref}".format(
                    error_title=_("Could not establish the connection to the API."), error_ref=response_text
                )
            )
        return response.json()

    @api.depends("pagos360_api_key", "pagos360_test_api_key")
    def ensure_webhook(self):
        if not self.pagos360_webhook_token:
            self.pagos360_webhook_token = secrets.token_urlsafe(32)
        base_url = self.get_base_url().replace("http:", "https:")
        webhook_url = urljoin(base_url, Pagos360Controller._webhook_url) + "?token=" + self.pagos360_webhook_token

        message = _("Your Pagos360 Webhook was already set up.")
        notification_type = "success"
        if not self._webhook_is_set(webhook_url):
            data = {"webhook": {"url": webhook_url, "event_types": self._get_event_types()}}
            response = self._pagos360_make_request("/webhook", data=data)
            if response.get("id"):
                message = _("Your Pagos360 Webhook was successfully set up.")
                notification_type = "success"
            else:
                message = _("Error setting your webhooks.")
                notification_type = "danger"
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "message": message,
                "sticky": False,
                "type": notification_type,
                "next": {"type": "ir.actions.act_window_close"},
            },
        }

    def _webhook_is_set(self, webhook_url):
        # Para chequear si el webhook existe realizamos un GET a /webhook
        # Esto nos regresa todas las urls configuradas y los tipos de eventos para los cual se configuró un webhook
        # Por lo que buscamos si existe el webhook para nuestra url
        # Si existe comparamos los eventos seteados
        # En caso de haber dierencias hacemos un DELETE del webhook para retornar False y que luego se envien de nuevo los webhook
        webhook_path = urlsplit(webhook_url).path
        webhooks = self._pagos360_make_request("/webhook", method="GET")
        if webhooks and webhooks.get("data"):
            data = webhooks.get("data")
            for webhook in data:
                if webhook.get("url") == webhook_url:
                    webhook_id = webhook.get("id")
                    webhook_events = [event["id"] for event in webhook["events"]]
                    if set(webhook_events) == set(self._get_event_types()):
                        return True
                    else:
                        self._pagos360_make_request("/webhook/%s" % webhook_id, method="DELETE")
                elif urlsplit(webhook.get("url") or "").path == webhook_path:
                    # Same endpoint registered under a stale URL — e.g. from before the webhook
                    # token existed, or with a token that got regenerated. Drop it, or Pagos360
                    # keeps double-posting every event to a URL we now always reject (task 72382).
                    self._pagos360_make_request("/webhook/%s" % webhook.get("id"), method="DELETE")
        return False

    def handled_event_types(self):
        # Se puede heredar en otros modulos para agregar nuevos webhooks
        return const.EVENT_TYPES

    def _get_event_types(self):
        event_types = list()
        types = self._pagos360_make_request("/event-type?limit=50", method="GET")
        handled_event_types = self.handled_event_types()
        if types and types.get("data"):
            data = types.get("data")
            event_types = [x["id"] for x in data if x["name"] in handled_event_types]
        return event_types

    # === COMPUTE METHODS ===#

    def _compute_feature_support_fields(self):
        """Override of `payment` to enable additional features."""
        super()._compute_feature_support_fields()
        self.filtered(lambda p: p.code == "pagos360").update(
            {
                "support_manual_capture": "full_only",
                "support_refund": "full_only",
                "support_tokenization": True,
            }
        )

    def _get_default_payment_method_codes(self):
        """Override of `payment` to return the default payment method codes.
        Note: `self.ensure_one()`
        :return: The default payment method codes.
        :rtype: set
        """
        self.ensure_one()
        if self.code != "pagos360":
            return super()._get_default_payment_method_codes()
        return const.DEFAULT_PAYMENT_METHODS_CODES

    def write(self, values):
        # Handle provider state changes for pagos360 before calling super
        providers_pagos360 = self.filtered(lambda p: p.code == "pagos360")
        if "state" in values and providers_pagos360:
            # Check if there are related tokens that would be archived
            state_changed_providers = providers_pagos360.filtered(
                lambda p: p.state in ("enabled", "test") and values["state"] == "disabled"
            )
            if state_changed_providers:
                related_tokens = self.env["payment.token"].search([("provider_id", "in", state_changed_providers.ids)])
                # Exclude test tokens from the check
                test_token = self.env.ref("payment_pagos360.pagos360_tests_token", raise_if_not_found=False)
                if test_token:
                    related_tokens = related_tokens - test_token

                if related_tokens:
                    raise UserError(
                        _(
                            "You have active tokens in PAGOS360. You must archive them before. "
                            "IMPORTANT: This action will also disable tokens in PAGOS360."
                        )
                    )
        res = super().write(values)
        enabled_providers = self.filtered(lambda p: p.code == "pagos360" and p.state in ["enabled", "test"])
        if enabled_providers:
            for provider in enabled_providers:
                if provider.state == "enabled" and not provider.pagos360_api_key:
                    raise UserError(_("You must set an API Key for PAGOS360 before enabling the provider."))
                elif provider.state == "test" and not provider.pagos360_test_api_key:
                    raise UserError(
                        _("You must set a Test API Key for PAGOS360 before setting the provider in test mode.")
                    )
        return res

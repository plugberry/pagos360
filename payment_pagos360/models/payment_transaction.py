import logging
import pprint
from collections import Counter
from datetime import date, timedelta

import pytz
from dateutil.relativedelta import relativedelta
from odoo import _, api, fields, models
from odoo.addons.payment import utils as payment_utils
from odoo.exceptions import UserError, ValidationError
from odoo.tools.misc import format_date, formatLang
from odoo.tools.urls import urljoin

from .. import const
from ..controllers.main import Pagos360Controller

_logger = logging.getLogger(__name__)


class _Pagos360ControlAborted(Exception):
    """Too many individual checks failed in a row: the API is failing in block."""


class PaymentTransaction(models.Model):
    _inherit = "payment.transaction"

    pagos360_adhesion_type = fields.Selection(related="token_id.pagos360_adhesion_type", store=True)
    pagos360_effective_payment_date = fields.Date()
    pagos360_debit_execution_date = fields.Date(string="Fecha de débito al cliente", readonly=True)
    pagos360_child_amount = fields.Float(
        string="Pagos 360 Child Charge Amount",
        help="Original transaction amount preserved when the operation is converted to "
        "`validation` (adhesion flow). Used as the amount of the child "
        "online_token transaction spawned after the adhesion is signed.",
    )

    @api.model
    def _get_specific_create_values(self, provider_code, values):
        """Convert a direct/redirect payment into a validation (adhesion) transaction when needed.

        Trigger: ``tokenize=True`` with a configured adhesion form URL — the user requested to save
        their payment method and the provider has the adhesion form configured.

        Once the adhesion is signed and a token is created, ``_pagos360_spawn_child_charge``
        fires the actual charge as a child transaction against that token.
        """
        res = super()._get_specific_create_values(provider_code, values)
        if provider_code != "pagos360":
            return res
        if values.get("operation") not in ("online_redirect", "online_direct"):
            return res
        provider = self.env["payment.provider"].browse(values.get("provider_id"))
        force_by_tokenize = bool(values.get("tokenize") and provider.pagos360_form_url)
        if not force_by_tokenize:
            return res
        res.update(
            {
                "operation": "validation",
                "tokenize": True,
                "pagos360_child_amount": values.get("amount", 0.0),
                "amount": 0.0,
                "currency_id": provider._get_validation_currency().id,
            }
        )
        return res

    @api.constrains("amount", "currency_id", "payment_method_id", "provider_id")
    def _check_pagos360_cash_maximum_amount(self):
        """Pagos360 silently skips the coupon above its cash limit, leaving the payer stuck."""
        maximum_amount = float(self.env["ir.config_parameter"].sudo().get_param("pagos360.cash_maximum_amount", "0"))
        if not maximum_amount:
            return
        for tx in self.filtered(lambda t: t.provider_code == "pagos360"):
            if tx.payment_method_code not in const.CASH_PAYMENT_METHOD_CODES:
                continue
            company = tx.provider_id.company_id
            converted_amount = tx.currency_id._convert(
                tx.amount,
                company.currency_id,
                company,
                tx.create_date or fields.Date.context_today(tx),
            )
            if company.currency_id.compare_amounts(converted_amount, maximum_amount) > 0:
                limit = formatLang(self.env, maximum_amount, currency_obj=company.currency_id)
                if len(tx.invoice_ids) > 1:
                    message = _(
                        "%(method)s does not accept payments over %(limit)s. Please pay the invoices separately, or choose another payment method.",
                        method=tx.payment_method_id.name,
                        limit=limit,
                    )
                else:
                    message = _(
                        "%(method)s does not accept payments over %(limit)s. Please choose another payment method.",
                        method=tx.payment_method_id.name,
                        limit=limit,
                    )
                raise ValidationError("PAGOS360: " + message)

    def _create_payment(self, **extra_create_values):
        self.ensure_one()

        if self.provider_code == "pagos360" and self.pagos360_effective_payment_date:
            extra_create_values.update(
                {
                    "date": self.pagos360_effective_payment_date,
                }
            )
        return super()._create_payment(**extra_create_values)

    def _get_specific_rendering_values(self, processing_values):
        """Override of `payment` to return Pagos360-specific rendering values.

        Note: self.ensure_one() from `_get_rendering_values`.

        :param dict processing_values: The generic and specific processing values of the transaction
        :return: The dict of provider-specific processing values.
        :rtype: dict
        """
        res = super()._get_specific_rendering_values(processing_values)
        if self.provider_code != "pagos360":
            return res
        if self.operation == "validation":
            return {"api_url": "%s&pReference=%s" % (self.provider_id.pagos360_form_url, self.reference)}

        # Initiate the payment and retrieve the payment link data.
        payload = self._pagos360_prepare_preference_request_payload()
        _logger.info("Sending '/payment-request' request for link creation:\n%s", pprint.pformat(payload))

        payment_data = self.provider_id._pagos360_make_request("/payment-request", data=payload)
        self.sudo().provider_reference = payment_data.get("id")
        if self.payment_method_code == "pagos360":
            api_url = payment_data["checkout_url"]
        elif self.payment_method_code == "pagofacil":
            access_token = payment_utils.generate_access_token(self.partner_id.id, self.amount, self.currency_id.id)
            api_url = "/payment/pagos360/pagofacil?tx_id=%s&access_token=%s" % (self.id, access_token)
        elif self.payment_method_code == "rapipago":
            access_token = payment_utils.generate_access_token(self.partner_id.id, self.amount, self.currency_id.id)
            api_url = "/payment/pagos360/rapipago?tx_id=%s&access_token=%s" % (self.id, access_token)

        return {
            "api_url": api_url,
        }

    def _pagos360_prepare_preference_request_payload(self):
        """Create the payload for the payment request based on the transaction values.

        :return: The request payload
        :rtype: dict
        """
        base_url = self.provider_id.get_base_url()
        redirect_url = urljoin(base_url, Pagos360Controller._return_url)

        first_due_date, first_total = self.get_coupon_due_values()
        # second_due_date, second_total = self.get_second_due_values()

        res = {
            "payment_request": {
                "description": self.reference,
                "external_reference": self.reference,  # No requerido
                "payer_name": self.partner_name,
                "payer_email": self.partner_email,  # No requerido
                "first_due_date": (first_due_date).strftime("%d-%m-%Y"),
                "first_total": first_total,
                # 'second_due_date': (second_due_date).strftime('%d-%m-%Y'),   # No requerido
                # 'second_total': second_total,            # No requerido
                "back_url_success": redirect_url,  # No requerido
                "back_url_pending": redirect_url,  # No requerido
                "back_url_rejected": redirect_url,  # No requerido
            }
        }
        res["payment_request"].update(self.provider_id._pagos360_get_coupon_exclusions())
        return res

    def _pagos360_get_invoice_due_date(self):
        """Retorna la invoice_date_due futura más próxima entre las facturas posted asociadas.
        None si no hay facturas elegibles o todas tienen fecha de vencimiento pasada o igual a hoy."""
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

        1. Calcula min_day = next_business_day(hoy, execution_days) — piso real de Pagos360.
        2. Si toggle activo y hay factura futura:
           - min_day >= invoice_due → devuelve min_day (el piso ya cubre o supera el vencimiento).
           - min_day < invoice_due  → devuelve next_business_day(invoice_due - 1 día, days=1),
             es decir el primer día hábil a partir del vencimiento de la factura.
        3. Sin toggle o sin facturas elegibles: devuelve min_day.
        """
        provider = self.provider_id

        if provider.pagos360_debit_use_invoice_due:
            invoice_due = self._pagos360_get_invoice_due_date()
            min_day_raw = self._pagos360_next_business_day(date.today(), days=3)
            if invoice_due:
                min_day = fields.Date.from_string(min_day_raw[:10])
                if min_day >= invoice_due:
                    return min_day_raw
                return self._pagos360_next_business_day(invoice_due - timedelta(days=1), days=1)
            return min_day_raw

        return self._pagos360_next_business_day(date.today(), days=provider.pagos360_debit_execution_days)

    def get_second_due_values(self):
        second_due_date = fields.Datetime.now() + timedelta(days=self.provider_id.second_validity_days)
        second_total = self.amount * (1 + self.provider_id.second_due_fees / 100.0)
        return second_due_date, second_total

    @api.model
    def _search_by_reference(self, provider_code, payment_data):
        """Override of payment to search the transaction based on Pagos360 data.

        :param str provider_code: The code of the provider that handled the transaction.
        :param dict payment_data: The payment data sent by the provider.
        :return: The transaction, if found.
        :rtype: recordset of `payment.transaction`
        """
        payload = payment_data.get("payload", {})
        entity_name = payment_data.get("entity_name")

        # A debit notification may carry the adhesion's external_reference, so the debit id
        # must be matched before the core lookup by reference finds the adhesion instead.
        # Adhesions are excluded: older notifications wrote the debit id on them.
        if provider_code == "pagos360" and entity_name in ["debit_request", "card_debit_request"]:
            tx = self.search(
                [
                    ("provider_reference", "=", str(payload.get("id"))),
                    ("provider_code", "=", "pagos360"),
                    ("operation", "!=", "validation"),
                ]
            )
            if tx:
                return tx

        tx = super()._search_by_reference(provider_code, payment_data)
        if provider_code != "pagos360" or tx:
            return tx

        if not entity_name:
            _logger.warning("PAGOS360: Received data with missing entity name.")
            return self

        if entity_name in ["debit_request", "card_debit_request"]:
            domain = [
                "|",
                ("provider_reference", "=", str(payload.get("id"))),
                ("reference", "=", payload.get("external_reference")),
                ("provider_code", "=", "pagos360"),
            ]
        else:
            domain = [("reference", "=", payload.get("external_reference")), ("provider_code", "=", "pagos360")]
        if payload.get("entity_name") == "payment_request":
            domain.append(["pagos360_adhesion_type", "=", False])
        tx = self.search(domain)
        if not tx:
            _logger.warning("Pagos360: No transaction found matching reference %s.", payment_data.get("ref"))
        return tx

    @api.model
    def _extract_reference(self, provider_code, payment_data):
        """Override of payment to extract the transaction reference from Pagos360 data.

        :param str provider_code: The code of the provider handling the transaction.
        :param dict payment_data: The payment data sent by the provider.
        :return: The transaction reference.
        :rtype: str
        """
        if provider_code != "pagos360":
            return super()._extract_reference(provider_code, payment_data)

        payload = payment_data.get("payload", {})
        return payload.get("external_reference")

    def _extract_amount_data(self, payment_data):
        """Override of payment to extract the amount and currency from the payment data."""
        if self.provider_code != "pagos360":
            return super()._extract_amount_data(payment_data)
        payload = payment_data.get("payload", {})
        # Each flow below reads the amount from its own key, so the opt out has to be decided
        # with that same key: webhook notifications carry ids and the external reference only,
        # and the API returns no request_result until the payment request is paid. Opting out
        # instead of reporting 0 matters because core would set the transaction to error and
        # return before _apply_updates, leaving the notification unprocessed.
        amount_key = {"adhesion": "first_total", "card_adhesion": "amount"}.get(
            self.pagos360_adhesion_type, "request_result"
        )
        if not payload.get(amount_key):
            return None
        amount = 0.0
        if not self.pagos360_adhesion_type:
            for result in payload.get("request_result", []):
                amount += result.get("amount", 0.0)
        elif self.pagos360_adhesion_type == "adhesion":
            amount += payload.get("first_total", 0.0)
        elif self.pagos360_adhesion_type == "card_adhesion":
            amount += payload.get("amount", 0.0)

        return {
            "amount": amount,
            "currency_code": self.currency_id.name,
        }

    def _apply_updates(self, payment_data):
        """Override of payment to update the transaction based on Pagos360 data.

        Note: self.ensure_one()

        :param dict payment_data: The payment data sent by the provider.
        :return: None
        """
        super()._apply_updates(payment_data)
        if self.provider_code != "pagos360":
            return

        entity_name = payment_data.get("entity_name")
        entity_id = payment_data.get("entity_id")
        if not entity_id:
            _logger.warning("PAGOS360: Received data with missing entity id.")
            return

        self.provider_reference = entity_id
        # Prefer the paid_at already present in the received payload (debits carry it in
        # request_result); only hit the API as a fallback for entities that don't.
        paid_at = self._pagos360_extract_paid_at(payment_data.get("payload")) or (
            self._pagos360_get_paid_at_from_request(entity_name, entity_id)
        )
        if paid_at:
            self.pagos360_effective_payment_date = paid_at[:10]
        payment_status = payment_data.get("type")
        try:
            if payment_status in [
                "pending",
                "in_process",
                "pending_to_sign",
                "transfer_created",
                "link_pagos_created",
                "banelco_pmc_created",
                "debin_created",
            ]:
                if self.state != "pending":
                    self._set_pending(extra_allowed_states=("error",))
            elif payment_status == "signed" and self.operation == "validation":
                self._set_done()
                if not self.token_id and self.tokenize:
                    self._tokenize(payment_data)
                if not self.child_transaction_ids:
                    self._pagos360_spawn_child_charge()
            elif payment_status == "paid":
                if paid_at:
                    self.pagos360_effective_payment_date = paid_at[:10]
                self._set_done(extra_allowed_states=("cancel", "error"))
            elif payment_status == "reverted":
                self.payment_id.action_draft()
                self.payment_id.action_cancel()
                self._set_canceled(
                    "PAGOS360: " + _("Canceled payment with status: %s", payment_status), extra_allowed_states=("done",)
                )
            elif payment_status in ["expired", "canceled", "rejected", "transfer_canceled"]:
                # Solo cambio el estado en los casos que puedo hacerlo.
                # las autorizaciones se pueden cancelar cuando estan ya en done
                if self.state in ["draft", "pending", "authorized"]:
                    self._set_canceled("PAGOS360: " + _("Canceled payment with status: %s", payment_status))
                if entity_name in ["card_adhesion", "adhesion"]:
                    if self.token_id and self.token_id.active:
                        self.token_id.with_context(is_notification=True).write({"active": False})
            else:
                _logger.info(
                    "received data with invalid payment status (%s) for transaction with reference %s",
                    payment_status,
                    self.reference,
                )
                message = """
                    Parece que esta transacción no se pudo realizar, ante algún inconveniente por favor comunicarse a
                     través de los siguientes canales:<br/>
                    Correo Electrónico: soporte@pagos360.com.ar<br/>
                    WhatsApp: +54 3512548747\n
                    Información:\n
                    - Transacción PAGOS360: {transaction}<br/>
                    - Código de Error: {error_code}<br/>
                    - Mensaje de Error: {error_msg}<br/>
                """.format(transaction=self.provider_reference, error_code=payment_status, error_msg="")
                self._set_error("PAGOS360: " + message)
        except Exception as e:
            _logger.info("PAGOS360 Error: (%s) for transaction with id %s", e, self.id)
            message = f"""
                Parece que esta transacción no se pudo realizar, le sugerimos revisar en su portal de PAGOS360 el
                 estado de la solicitud de pago.
                Ante algún inconveniente con la misma por favor comunicarse a través de los siguientes canales:
                Correo Electrónico: soporte@pagos360.com.ar\n
                WhatsApp: +54 3512548747\n
                - Transacción id: {self.id}<br/>
                - Mensaje de Error": {e}<br/>
            """
            self._set_error("PAGOS360: " + message)

    def _pagos360_get_paid_at_from_request(self, entity_name, entity_id):
        """Fetch entity info from Pagos360 and extract the first available paid_at value."""
        endpoint_by_entity = {
            "payment_request": f"/payment-request?id={entity_id}",
            "card_adhesion": f"/card-adhesion/{entity_id}",
            "adhesion": f"/adhesion/{entity_id}",
            "card_debit_request": f"/card-debit-request?id={entity_id}",
            "debit_request": f"/debit-request?id={entity_id}",
        }
        endpoint = endpoint_by_entity.get(entity_name)
        if not endpoint:
            return False

        try:
            entity_data = self.provider_id._pagos360_make_request(endpoint, method="GET")
        except Exception as e:
            _logger.warning(
                "Could not fetch paid_at from Pagos360 API for entity %s (%s): %s", entity_name, entity_id, e
            )
            return False

        return self._pagos360_extract_paid_at(entity_data)

    def _pagos360_extract_paid_at(self, data):
        """Recursively look for a paid_at key in Pagos360 response payloads."""
        if isinstance(data, dict):
            paid_at = data.get("paid_at")
            if paid_at:
                return paid_at
            for value in data.values():
                paid_at = self._pagos360_extract_paid_at(value)
                if paid_at:
                    return paid_at
            return False

        if isinstance(data, list):
            for item in data:
                paid_at = self._pagos360_extract_paid_at(item)
                if paid_at:
                    return paid_at

        return False

    def _extract_token_values(self, payment_data):
        """Create a new token based on the feedback data.

        Note: self.ensure_one()

        :param dict payment_data: The payment data sent by the provider.
        :return: The token values to create a new token.
        :rtype: dict
        """
        self.ensure_one()
        if self.provider_code != "pagos360":
            return super()._extract_token_values(payment_data)

        adhesion_id = payment_data.get("entity_id")
        entity_name = payment_data.get("entity_name")

        if not adhesion_id or not entity_name:
            _logger.warning("PAGOS360: Missing entity_id or entity_name in payment data")
            return {}

        if entity_name == "card_adhesion":
            endpoint = f"/card-adhesion/{adhesion_id}"
        else:
            endpoint = f"/adhesion/{adhesion_id}"

        adhesion_data = self.provider_id._pagos360_make_request(endpoint, data=None, method="GET")
        if not adhesion_data:
            return {}

        if entity_name == "card_adhesion":
            payment_details = "Debito automático en Tarjeta: {} **** - {}".format(
                adhesion_data.get("card"), adhesion_data.get("last_four_digits")
            )
        elif entity_name == "adhesion":
            payment_details = "Debito automático en CBU: {} ****{}".format(
                adhesion_data.get("bank"), adhesion_data.get("cbu_number")
            )
        else:
            payment_details = ""

        return {
            "provider_ref": adhesion_id,
            "payment_details": payment_details,
            "pagos360_adhesion_type": entity_name,
            "pagos360_external_reference": adhesion_data.get("external_reference"),
            "pagos360_card": adhesion_data.get("card"),
            "pagos360_card_number": adhesion_data.get("last_four_digits"),
            "pagos360_cbu_number": adhesion_data.get("cbu_number"),
            "pagos360_bank": adhesion_data.get("bank"),
        }

    def _send_payment_request(self):
        if self.provider_code == "pagos360":
            if self.state != "draft" or self.provider_reference:
                # No se puede enviar la solicitud de pago si no esta en borrador o ya tiene referencia
                _logger.error(
                    f"pagos360: cant send transaction {self.id}. State: {self.state} - Ref: {self.provider_reference}"
                )
                return
            if self.token_id.pagos360_adhesion_type == "card_adhesion":
                req = self._pagos360_card_debit_request()
            elif self.token_id.pagos360_adhesion_type == "adhesion":
                req = self._pagos360_debit_request()
            else:
                raise ValidationError(
                    "PAGOS360: "
                    + _(
                        "Transaction %s cannot be charged: its payment token has no adhesion type.",
                        self.reference,
                    )
                )
            self.env.cr.commit()  # pylint: disable=invalid-commit
            if req:
                entity_name = const.DEBIT_ENTITY_BY_ADHESION_TYPE[self.token_id.pagos360_adhesion_type]
                self._process(self.provider_code, self.simulate_webhook(entity_name, req))
                self.env.cr.commit()  # pylint: disable=invalid-commit
        return super()._send_payment_request()

    def _pagos360_card_debit_request(self):
        today = fields.Date.today()
        cut_days_raw = (self.provider_id.pagos360_cut_days or "19").split(",")
        cut_days = sorted(int(d.strip()) for d in cut_days_raw if d.strip().isdigit())
        if not cut_days:
            cut_days = [19]
        future_cuts = [c for c in cut_days if c >= today.day]
        if future_cuts:
            execution_date = today.replace(day=future_cuts[0])
        else:
            next_month = today + relativedelta(months=1)
            execution_date = next_month.replace(day=cut_days[0])
        data = {
            "card_debit_request": {
                "description": _("Payment %s") % self.company_id.display_name,
                "amount": self.amount,
                "month": execution_date.month,
                "year": execution_date.year,
                "card_adhesion_id": int(self.token_id.provider_ref),
            }
        }
        res = self.provider_id._pagos360_make_request("card-debit-request", data=data, method="POST")
        self.provider_reference = res.get("id")
        self.pagos360_debit_execution_date = execution_date + timedelta(days=const.CARD_DEBIT_DAYS_DAYS)
        return res

    def _pagos360_next_business_day(self, due_date, days=3):
        data = {"next_business_day": {"date": due_date.strftime("%d-%m-%Y"), "days": days}}
        return self.provider_id._pagos360_make_request("validator/next-business-day", data=data, method="POST")

    def _pagos360_debit_request(self):
        next_business_day = self.get_debit_due_date()
        execution_date = fields.Date.from_string(next_business_day[:10])
        data = {
            "debit_request": {
                "description": _("Payment %s") % self.company_id.display_name,
                "first_total": self.amount,
                "first_due_date": execution_date.strftime("%d-%m-%Y"),
                "adhesion_id": int(self.token_id.provider_ref),
            }
        }
        res = self.provider_id._pagos360_make_request("debit-request", data=data, method="POST")
        self.provider_reference = res.get("id")
        self.pagos360_debit_execution_date = execution_date
        return res

    def _pagos360_fetch_and_process_transaction(self):
        """Query Pagos360 for the current state of this transaction and process it as a notification.

        An entity that Pagos360 does not return is skipped.

        :return: The simulated notifications that were processed.
        :rtype: list[dict]
        """
        self.ensure_one()
        provider = self.provider_id
        ref_sanitized = self.reference.replace("%", "%25")
        entities = []
        if self.operation == "validation":
            for entity_name in ("card_adhesion", "adhesion"):
                endpoint = "/%s?external_reference=%s&page=1" % (entity_name.replace("_", "-"), ref_sanitized)
                datas = provider._pagos360_make_request(endpoint, method="GET")
                entities += [(entity_name, data) for data in datas.get("data") or []]
        elif not self.pagos360_adhesion_type:
            if self.provider_reference:
                from_date = (self.create_date - relativedelta(months=1)).strftime("%d-%m-%Y")
                to_date = (self.create_date + relativedelta(months=1)).strftime("%d-%m-%Y")
                url = (
                    f"/payment-request?id={self.provider_reference}&created_at_gte={from_date}&created_at_lte={to_date}"
                )
            else:
                url = "/payment-request?external_reference=%s" % ref_sanitized
            data = self._get_operation_info_from_data(provider._pagos360_make_request(url, method="GET"))
            entities.append(("payment_request", data))
        else:
            entity_name = const.DEBIT_ENTITY_BY_ADHESION_TYPE[self.pagos360_adhesion_type]
            endpoint = "/%s?id=%s" % (entity_name.replace("_", "-"), self.provider_reference)
            datas = provider._pagos360_make_request(endpoint, method="GET").get("data") or [None]
            entities.append((entity_name, datas[0]))

        payloads = []
        for entity_name, data in entities:
            payload = self.simulate_webhook(entity_name, data)
            if payload:
                payloads.append(payload)
                self.sudo()._process(self.provider_code, payload)
        return payloads

    def get_pagos360_info(self, check_payment_state=True):
        result_msg = []
        for tx in self.filtered(lambda x: x.provider_code == "pagos360"):
            result_msg += tx._pagos360_fetch_and_process_transaction()
            self.env.cr.commit()  # pylint: disable=invalid-commit
        return self.pagos360_readable_result(result_msg)

    # === COLLECTION CONTROL === #

    @api.model
    def _cron_pagos360_collection_control(self):
        """Daily control of what the webhook left unresolved.

        Providers in test mode share the sandbox account, so they only run it by hand.
        """
        for provider in self.env["payment.provider"].search([("code", "=", "pagos360"), ("state", "=", "enabled")]):
            self._pagos360_collection_control(provider)

    @api.model
    def _pagos360_collection_control(self, provider):
        """Check the day before against Pagos360 and process what Odoo did not hear about.

        Step 1 checks one by one the transactions that the day's reports or the signed adhesions
        list and that Odoo has not resolved yet. Step 2 checks the unresolved ones whose term ends
        on the control day, or a week before it.

        :return: The summary line, also written to the log.
        :rtype: str
        """
        day = provider._pagos360_get_control_date()
        max_errors = int(self.env["ir.config_parameter"].sudo().get_param("pagos360.control_max_consecutive_errors", 5))
        prefix = "PAGOS360 CONTROL provider %s day %s: " % (provider.id, day)
        stats = Counter()
        errors_in_a_row = 0

        def check(tx):
            """Fetch and process one transaction in its own commit. Return whether Pagos360 answered."""
            nonlocal errors_in_a_row
            try:
                payloads = tx._pagos360_fetch_and_process_transaction()
                self.env.cr.commit()  # pylint: disable=invalid-commit
            except Exception:
                self.env.cr.rollback()
                stats["failed"] += 1
                errors_in_a_row += 1
                _logger.warning(prefix + "the check of %s failed.", tx.reference, exc_info=True)
                if errors_in_a_row >= max_errors:
                    raise _Pagos360ControlAborted("%s checks failed in a row" % errors_in_a_row) from None
                return False
            errors_in_a_row = 0
            stats["failed"] += not payloads
            return bool(payloads)

        try:
            touched = self._pagos360_control_listed(provider, day, prefix, stats, check)
            self._pagos360_control_expired(provider, day, touched, prefix, stats, check)
        except Exception as e:
            self.env.cr.rollback()
            stats["aborted"] = 1
            expected = isinstance(e, (_Pagos360ControlAborted, ValidationError))
            _logger.error(prefix + "aborted, the rest is left for the next run: %s", e, exc_info=not expected)
        summary = prefix + ", ".join("%s=%s" % item for item in stats.items())
        _logger.info(summary)
        return summary

    @api.model
    def _pagos360_control_listed(self, provider, day, prefix, stats, check):
        """Step 1. Return the transactions matched by a row, processed or not."""
        # Everything is read first, so a failed read processes nothing.
        since = day - timedelta(days=const.CONTROL_ADHESION_DAYS)
        sources = {
            "collection": provider._pagos360_get_report("collection", day),
            "chargeback": provider._pagos360_get_report("chargeback", day),
            "signed_adhesion": provider._pagos360_get_signed_adhesions(since),
        }
        events = {"collection": _("paid"), "chargeback": _("reverted"), "signed_adhesion": _("signed")}
        state_labels = dict(self._fields["state"]._description_selection(self.env))
        touched = self.browse()
        for kind, rows in sources.items():
            stats[kind] = len(rows)
            to_check, resolved = const.CONTROL_STATES[kind]
            for row in rows:
                row_id = row.get("id") if kind == "signed_adhesion" else row.get("request_id")
                row_label = "%s row %s (%s)" % (kind, row_id, row.get("external_reference"))
                tx = self._pagos360_control_match(provider, kind, row_id, row.get("external_reference"))
                if len(tx) != 1:
                    stats["ambiguous" if tx else "unmatched"] += 1
                    # The sandbox account is shared by every test database.
                    level = logging.INFO if not tx and provider.state == "test" else logging.WARNING
                    _logger.log(level, prefix + "%s matches %s transactions.", row_label, len(tx))
                    continue
                touched |= tx
                if tx.state in resolved:
                    stats["resolved"] += 1
                    continue
                checked = tx.state in to_check
                if checked and not check(tx):
                    continue
                if tx.state in resolved:
                    stats["rescued"] += 1
                    continue
                _logger.warning(prefix + "%s lists %s, which is %s in Odoo.", row_label, tx.reference, tx.state)
                # A canceled transaction is never rescued: someone has to decide before charging again.
                if checked or (kind == "collection" and tx.state == "cancel"):
                    tx._pagos360_control_post_note(
                        _(
                            "Pagos360 reports transaction %(reference)s as %(event)s on %(date)s (id %(row_id)s), "
                            "but it is %(state)s in Odoo. Please review it before charging again.",
                            reference=tx.reference,
                            event=events[kind],
                            date=format_date(self.env, day),
                            row_id=row_id,
                            state=state_labels[tx.state],
                        )
                    )
        return touched

    @api.model
    def _pagos360_control_match(self, provider, kind, row_id, reference):
        """Return the transactions of `provider` that a report or listing row refers to."""
        domain = [("provider_id", "=", provider.id), ("provider_code", "=", "pagos360")]
        if kind == "signed_adhesion":
            return self.search(domain + [("operation", "=", "validation"), ("reference", "=", reference)])
        domain.append(("operation", "!=", "validation"))
        # Debits carry the external reference of their adhesion, shared by all its debits:
        # they can only be matched by their own id.
        tx = self.search(domain + [("provider_reference", "=", str(row_id))]) if row_id else self.browse()
        if not tx and reference:
            tx = self.search(domain + [("reference", "=", reference), ("pagos360_adhesion_type", "=", False)])
        return tx

    @api.model
    def _pagos360_control_expired(self, provider, day, touched, prefix, stats, check):
        """Step 2: check the unresolved transactions whose term ends on `day`, or a week before it."""
        due_days = {day, day - timedelta(days=const.CONTROL_RECHECK_DAYS)}
        oldest = min(due_days) - timedelta(
            days=max(provider.pagos360_coupon_validity_days, const.CONTROL_ADHESION_DAYS)
        )
        executions = [due - timedelta(days=const.CONTROL_DEBIT_MARGIN_DAYS) for due in due_days]
        domain = [
            ("provider_id", "=", provider.id),
            ("state", "in", ("draft", "pending")),
            ("id", "not in", touched.ids),
        ]
        due = self.search(
            domain
            + [
                "|",
                ("create_date", ">=", oldest - timedelta(days=1)),
                ("pagos360_debit_execution_date", "in", executions),
            ],
            order="create_date, id",
        ).filtered(lambda tx: tx._pagos360_get_control_due_date() in due_days)
        max_checks = int(self.env["ir.config_parameter"].sudo().get_param("pagos360.control_max_checks", 50))
        if len(due) > max_checks:
            stats["over_limit"] = len(due) - max_checks
            _logger.info(
                prefix + "over the limit, left for the recheck: %s", ", ".join(due[max_checks:].mapped("reference"))
            )
        for tx in due[:max_checks]:
            stats["expired_checked"] += 1
            check(tx)

    def _pagos360_get_control_due_date(self):
        """Return the day the term of this transaction ends, in Argentina's time, or None if unknown."""
        self.ensure_one()
        if self.operation != "validation" and self.pagos360_adhesion_type:
            execution = self.pagos360_debit_execution_date
            return execution and execution + timedelta(days=const.CONTROL_DEBIT_MARGIN_DAYS) or None
        created = pytz.utc.localize(self.create_date).astimezone(pytz.timezone(const.CONTROL_TIMEZONE)).date()
        if self.operation == "validation":
            return created + timedelta(days=const.CONTROL_ADHESION_DAYS)
        return created + timedelta(days=self.provider_id.pagos360_coupon_validity_days)

    def _pagos360_control_post_note(self, message):
        """Leave an internal note, hidden from the portal, on the invoices and sale orders of the transaction."""
        sale_orders = self.sale_order_ids if "sale_order_ids" in self._fields else []
        for document in [*self.invoice_ids, *sale_orders]:
            document.message_post(body=message, subtype_xmlid="mail.mt_note")

    def pagos360_cancel_transactions(self):
        for tx in self.filtered(lambda t: t.pagos360_adhesion_type in ["adhesion", "card_adhesion"]):
            payment_request_id = tx.provider_reference
            if tx.pagos360_adhesion_type == "adhesion":
                endpoint = "debit-request"
            elif tx.pagos360_adhesion_type == "card_adhesion":
                endpoint = "card-debit-request"
            else:
                continue
            pagos360_tx = tx.provider_id._pagos360_make_request(f"/{endpoint}/{payment_request_id}", method="GET")
            if pagos360_tx and pagos360_tx.get("state") == "pending":
                response_json = tx.provider_id._pagos360_make_request(
                    f"/{endpoint}/{payment_request_id}/cancel", method="PUT"
                )
                if response_json and response_json.get("state") == "canceled":
                    tx._set_canceled()
        return

    def _get_operation_info_from_data(self, request_info):
        for data in request_info["data"]:
            if data["external_reference"] == self.reference:
                return data
        return []

    def pagos360_readable_result(self, result_msg):
        txt = []
        for data in result_msg:
            txt += ["---------------------------"]
            txt += ["external_reference: %s" % data["payload"].get("external_reference")]
            txt += ["state: %s" % data["payload"].get("state")]
            txt += ["---------------------------"]
            txt += ["%s: %s" % (x, data[x]) for x in data if x != "payload"]
            txt += ["- %s: %s" % (x, data.get("payload", []).get(x)) for x in data.get("payload", [])]
            txt += ["---------------------------"]

        raise UserError("%s" % " \n".join(txt))

    def pagos360_spawn_child_charge(self):
        self._pagos360_spawn_child_charge()

    def _pagos360_spawn_child_charge(self):
        """Create and trigger a child charge transaction after an adhesion is signed.

        Called from `_apply_updates` when a Pagos 360 validation transaction is signed.
        Uses `pagos360_child_amount` (preserved at creation time by `_get_specific_create_values`)
        as the charge amount, propagates sale/invoice links so downstream reconciliation works,
        and triggers `_charge_with_token` to actually charge the customer.
        """
        self.ensure_one()
        if self.provider_code != "pagos360":
            return
        if self.operation != "validation":
            return
        if self.source_transaction_id:
            return
        if not self.token_id:
            return
        if self.child_transaction_ids.filtered(lambda c: c.operation == "online_token"):
            return

        amount = self.pagos360_child_amount
        if not amount:
            _logger.info(
                "Pagos 360: skipping child charge spawn for tx %s — pagos360_child_amount is empty",
                self.reference,
            )
            return

        child = self._create_child_transaction(
            amount,
            operation="online_token",
            **self._pagos360_get_child_link_vals(),
        )
        _logger.info(
            "Pagos 360: spawned child charge %s on token %s from validation tx %s.",
            child.reference,
            self.token_id.display_name,
            self.reference,
        )
        child._charge_with_token()

    def _pagos360_get_child_link_vals(self):
        """Return M2M commands to propagate sale order and invoice links to the child.

        Keeping the same links on the child cobro is what lets the standard Odoo flows
        (reconciliation, invoice payment, sale confirmation) react to the child's done
        state as if the user had paid directly.
        """
        self.ensure_one()
        link_vals = {}
        if "sale_order_ids" in self._fields and self.sale_order_ids:
            link_vals["sale_order_ids"] = [(6, 0, self.sale_order_ids.ids)]
        if "invoice_ids" in self._fields and self.invoice_ids:
            link_vals["invoice_ids"] = [(6, 0, self.invoice_ids.ids)]
        return link_vals

    def simulate_webhook(self, entity_name, data):
        if not data:
            _logger.warning("No data recieved")
            return
        return {"entity_name": entity_name, "entity_id": data["id"], "type": data["state"], "payload": data}

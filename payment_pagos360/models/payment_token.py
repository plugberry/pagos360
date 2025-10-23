import logging
from datetime import datetime, timedelta
from math import ceil

from odoo import _, api, fields, models
from odoo.tools import config
from requests.exceptions import RequestException

_logger = logging.getLogger(__name__)


class PaymentToken(models.Model):
    _inherit = "payment.token"

    pagos360_adhesion_type = fields.Selection([("adhesion", "CBU Adhesion"), ("card_adhesion", "Card Adhesion")])
    pagos360_external_reference = fields.Char()
    pagos360_bank = fields.Char()
    pagos360_cbu_number = fields.Char()
    pagos360_card = fields.Char()
    pagos360_card_number = fields.Char()

    @api.depends(
        "payment_details",
        "create_date",
        "pagos360_adhesion_type",
        "pagos360_card",
        "pagos360_card_number",
        "pagos360_bank",
        "pagos360_cbu_number",
    )
    def _compute_display_name(self):
        pagos360_tokens = self.filtered(lambda t: t.provider_code == "pagos360")
        for token in pagos360_tokens:
            if token.pagos360_adhesion_type == "card_adhesion":
                token.display_name = (
                    f"Debito automático en Tarjeta: {token.pagos360_card} **** - {token.pagos360_card_number}"
                )
            elif token.pagos360_adhesion_type == "adhesion":
                token.display_name = (
                    f"Debito automático en CBU: {token.pagos360_bank} ****{token.pagos360_cbu_number[-5:]}"
                )

        super(PaymentToken, self - pagos360_tokens)._compute_display_name()

    def _handle_archiving(self):
        """Handle the archiving of tokens for Pagos360."""
        super()._handle_archiving()

        # Skip API calls in the following scenarios:
        # 1. During unit tests (test_enable config is True)
        # 2. When loading XML data with noupdate flag (demo data or data files)
        if config["test_enable"] or self.env.context.get("noupdate"):
            return

        if not self.env.context.get("is_notification"):
            for rec in self.filtered(lambda x: x.provider_code == "pagos360"):
                endpoint = "adhesion" if rec.pagos360_adhesion_type == "adhesion" else "card-adhesion"
                id = rec.provider_ref
                try:
                    rec.provider_id._pagos360_make_request(f"/{endpoint}/{id}/cancel", method="PUT")
                except RequestException:
                    _logger.exception("Unable to delete token in PAGOS360")

    def pagos360_check_for_similar_transactions(self, days_frame=1):
        from_date = datetime.strftime(fields.Date.today() - timedelta(days=days_frame), "%d-%m-%Y")
        to_date = datetime.strftime(fields.Date.today(), "%d-%m-%Y")

        card_adhesions = self.filtered(
            lambda x: x.pagos360_adhesion_type == "card_adhesion" and x.provider_code == "pagos360"
        )
        message = ""
        for provider_id in card_adhesions.mapped("provider_id"):
            current_page = 1
            total_pages = 1
            provider_adhesions = card_adhesions.filtered(lambda x: x.provider_id == provider_id)
            provider_references = provider_adhesions.mapped("pagos360_external_reference")

            while current_page <= total_pages:
                data = provider_id._pagos360_make_request(
                    f"/card-debit-request?created_at_gte={from_date}&created_at_lte={to_date}&page={current_page}",
                    method="GET",
                )
                current_page = data.get("current_page", 1) + 1
                items_per_page = data.get("items_per_page", 20)
                total_count = data.get("total_count", 0)
                total_pages = ceil(total_count / items_per_page)
                for transaction in data.get("data", []):
                    if str(transaction.get("adhesion", {}).get("id", "")) in provider_references:
                        message += _(
                            f"Pagos360: Similar payment found {transaction['id']} for  {transaction['adhesion'].get('adhesion_holder_name')}, {transaction['state']} {transaction['first_total']}\n"
                        )

        cbu_adhesions = self.filtered(
            lambda x: x.pagos360_adhesion_type == "adhesion" and x.provider_code == "pagos360"
        )
        for provider_id in cbu_adhesions.mapped("provider_id"):
            current_page = 1
            total_pages = 1
            provider_adhesions = cbu_adhesions.filtered(lambda x: x.provider_id == provider_id)
            provider_references = provider_adhesions.mapped("provider_ref")

            while current_page <= total_pages:
                data = provider_id._pagos360_make_request(
                    f"/debit-request?created_at_gte={from_date}&created_at_lte={to_date}&page={current_page}",
                    method="GET",
                )
                current_page = data.get("current_page", 1) + 1
                items_per_page = data.get("items_per_page", 20)
                total_count = data.get("total_count", 0)
                total_pages = ceil(total_count / items_per_page)
                for transaction in data.get("data", []):
                    if str(transaction.get("adhesion", {}).get("id", "")) in provider_references:
                        message += _(
                            f"Pagos360: Similar payment found {transaction['id']} for  {transaction['adhesion'].get('adhesion_holder_name')}, {transaction['state']} {transaction['first_total']}\n"
                        )
        return message

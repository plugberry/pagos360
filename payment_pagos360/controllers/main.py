import logging
import pprint

import werkzeug
from dateutil.relativedelta import relativedelta
from odoo import http
from odoo.addons.payment import utils as payment_utils
from odoo.addons.portal.controllers import portal
from odoo.exceptions import ValidationError
from odoo.http import Response, request

WEBHOOK_TEST_PAYLOAD = {
    "id": "816",
    "request_result_id": "546",
    "external_reference": "TX-816",
}

_logger = logging.getLogger(__name__)


class Pagos360Controller(portal.CustomerPortal):
    _return_url = "/payment/pagos360/return"
    _webhook_url = "/payment/pagos360/webhook"

    @http.route(
        "/payment/pagos360/pagofacil", type="http", methods=["GET", "POST"], auth="public", website=True, csrf=False
    )
    def pagofacil_barcode(self, tx_id, access_token, **kwargs):
        """Display the payment confirmation page to the user.

        :param str tx_id: The transaction to confirm, as a `payment.transaction` id
        :param str access_token: The access token used to verify the user
        :param dict kwargs: Optional data. This parameter is not used here
        :raise: werkzeug.exceptions.NotFound if the access token is invalid
        """
        tx_id = self._cast_as_int(tx_id)
        if tx_id:
            tx_sudo = request.env["payment.transaction"].sudo().browse(tx_id)

            # Raise an HTTP 404 if the access token is invalid
            if not payment_utils.check_access_token(
                access_token, tx_sudo.partner_id.id, tx_sudo.amount, tx_sudo.currency_id.id
            ):
                raise werkzeug.exceptions.NotFound()  # Don't leak information about ids.
            if tx_sudo.provider_id.code != "pagos360" or tx_sudo.state not in ["draft", "pending"]:
                return request.redirect("/my/home")

            ref_sanitarzed = tx_sudo.reference.replace("%", "%25")
            if tx_sudo.provider_reference:
                from_date = (tx_sudo.create_date - relativedelta(months=1)).strftime("%d-%m-%Y")
                to_date = (tx_sudo.create_date + relativedelta(months=1)).strftime("%d-%m-%Y")
                url = f"/payment-request?id={tx_sudo.provider_reference}&created_at_gte={from_date}&created_at_lte={to_date}"
            else:
                url = "/payment-request?external_reference=%s" % ref_sanitarzed

            values = tx_sudo._get_operation_info_from_data(
                tx_sudo.provider_id._pagos360_make_request(url, method="GET")
            )
            tx_sudo.write(
                {
                    "provider_reference": values.get("id"),
                    "state": values.get("state", "draft"),
                }
            )
            return request.redirect(values["pdf_url"], local=False)
        else:
            # Display the portal homepage to the user
            return request.redirect("/my/home")

    @http.route(
        "/payment/pagos360/rapipago", type="http", methods=["GET", "POST"], auth="public", website=True, csrf=False
    )
    def rapipago_barcode(self, tx_id, access_token, **kwargs):
        """Display the payment confirmation page to the user.

        :param str tx_id: The transaction to confirm, as a `payment.transaction` id
        :param str access_token: The access token used to verify the user
        :param dict kwargs: Optional data. This parameter is not used here
        :raise: werkzeug.exceptions.NotFound if the access token is invalid
        """
        tx_id = self._cast_as_int(tx_id)
        if tx_id:
            tx_sudo = request.env["payment.transaction"].sudo().browse(tx_id)

            # Raise an HTTP 404 if the access token is invalid
            if not payment_utils.check_access_token(
                access_token, tx_sudo.partner_id.id, tx_sudo.amount, tx_sudo.currency_id.id
            ):
                raise werkzeug.exceptions.NotFound()  # Don't leak information about ids.
            if tx_sudo.provider_id.code != "pagos360" or tx_sudo.state not in ["draft", "pending"]:
                return request.redirect("/my/home")

            ref_sanitarzed = tx_sudo.reference.replace("%", "%25")
            values = tx_sudo._get_operation_info_from_data(
                tx_sudo.provider_id._pagos360_make_request(
                    "/payment-request?external_reference=%s" % ref_sanitarzed, method="GET"
                )
            )
            tx_sudo.write(
                {
                    "provider_reference": values.get("id"),
                    "state": values.get("state", "draft"),
                }
            )
            return request.render("payment_pagos360.rapipago_barcode_print", values)
        else:
            # Display the portal homepage to the user
            return request.redirect("/my/home")

    @http.route(_return_url, type="http", auth="public", methods=["GET", "POST"], csrf=False, save_session=False)
    def pagos360_return_from_checkout(self, **data):
        """Process the notification data sent by Pagos360 after redirection from checkout.
        :param dict data: The notification data (only `id`) and the transaction reference (`ref`)
                          embedded in the return URL
        """
        _logger.info("handling redirection from Pagos360 with data:\n%s", pprint.pformat(data))
        return request.redirect("/payment/status")

    @http.route(f"{_webhook_url}", type="http", auth="public", methods=["POST"], csrf=False)
    def pagos360_webhook(self, token=None, **data):
        """Process the notification data sent by Pagos360 to the webhook.

        :param str token: The secret registered for this URL by `ensure_webhook`, used to reject
                           requests that don't know it.
        :param dict _kwargs: The extra query parameters.
        :return: An empty string to acknowledge the notification.
        :rtype: str
        """
        provider_sudo = (
            request.env["payment.provider"]
            .sudo()
            .search([("code", "=", "pagos360"), ("pagos360_webhook_token", "=", token)], limit=1)
            if token
            else request.env["payment.provider"]
        )
        if not provider_sudo:
            _logger.warning("Pagos360 webhook: rejected notification with invalid or missing token")
            return Response("success", status=200)  # Acknowledge without leaking information
        try:
            data = request.get_json_data()
            _logger.info("Notification received from Pagos360 with data: %s", data)
            if data and data.get("payload") == WEBHOOK_TEST_PAYLOAD:
                _logger.warning("Webhook URL is OK")
                return Response("success", status=200)
            data["from_webhook"] = True
            request.env["payment.transaction"].sudo()._process("pagos360", data)
        except ValidationError:  # Acknowledge the notification to avoid getting spammed
            _logger.exception("unable to handle the notification data; skipping to acknowledge")
        return Response("success", status=200)  # Acknowledge the notification

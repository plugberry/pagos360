import logging
import pprint

from odoo import http
from odoo.exceptions import ValidationError
from odoo.http import request, Response


_logger = logging.getLogger(__name__)


class Pagos360Controller(http.Controller):
    _return_url = '/payment/pagos360/return'
    _webhook_url = '/payment/pagos360/webhook'

    @http.route(
        _return_url, type='http', auth='public', methods=['GET', 'POST'], csrf=False,
        save_session=False
    )
    def pagos360_return_from_checkout(self, **data):
        """ Process the notification data sent by Pagos360 after redirection from checkout.
        :param dict data: The notification data (only `id`) and the transaction reference (`ref`)
                          embedded in the return URL
        """
        _logger.info("handling redirection from Pagos360 with data:\n%s", pprint.pformat(data))
        return request.redirect('/payment/status')


    @http.route(
        f'{_webhook_url}', type='http', auth='public', methods=['POST'], csrf=False
    )
    def pagos360_webhook(self, **data):
        """ Process the notification data sent by Pagos360 to the webhook.

        :param str reference: The transaction reference embedded in the webhook URL.
        :param dict _kwargs: The extra query parameters.
        :return: An empty string to acknowledge the notification.
        :rtype: str
        """
        data = request.get_json_data()
        _logger.info("Notification received from Pagos360 with data:\n%s", pprint.pformat(data))
        data['from_webhook'] = True
        try:
            request.env['payment.transaction'].sudo()._handle_notification_data('pagos360', data)
        except ValidationError:  # Acknowledge the notification to avoid getting spammed
            _logger.exception("unable to handle the notification data; skipping to acknowledge")
        return Response('success', status=200)  # Acknowledge the notification

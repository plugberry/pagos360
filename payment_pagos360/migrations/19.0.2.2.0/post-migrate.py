import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    """Register the webhook secret token for existing providers (task 72382).

    Same call the "Ensure Webhook" button makes; runs it here too so the fix takes effect
    without requiring an admin to click the button on every client database.
    """
    env = api.Environment(cr, SUPERUSER_ID, {})
    providers = env["payment.provider"].search([("code", "=", "pagos360"), ("state", "in", ("enabled", "test"))])
    for provider in providers:
        try:
            provider.ensure_webhook()
            _logger.info("PAGOS360 migration: webhook token registered for provider %s", provider.id)
        except Exception:
            # Pagos360 unreachable or a stale API key must not abort the whole upgrade; the
            # provider keeps working with the old, unprotected webhook URL until "Ensure
            # Webhook" is retried manually.
            _logger.exception("PAGOS360 migration: could not register webhook token for provider %s", provider.id)

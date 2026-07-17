import logging

from odoo import SUPERUSER_ID, Command, api
from odoo.tools.safe_eval import safe_eval
from psycopg2 import sql

_logger = logging.getLogger(__name__)

# Old free-text columns. They are not dropped automatically when the Text fields
# are removed, so they still hold the previous configuration at post-migration time.
_OLD_COLUMNS = ("pagos360_excluded_channels", "pagos360_excluded_installments", "pagos360_excluded_card_brands")

# Map the old free-text card brand codes to the display name used in the pagos360.card.brand
# catalog. The numeric code Pagos360 needs is unknown at migration time (it comes from the API
# per merchant), so records are created name-only; the first "Fetch available methods" fills the
# code and the exclusion starts taking effect (the old string codes were silently ignored anyway).
_CARD_BRAND_NAME_BY_CODE = {
    "visa": "Visa",
    "mastercard": "Mastercard",
    "ceconsud": "Cencosud",  # legacy typo
    "cencosud": "Cencosud",
    "naranja": "Naranja",
    "nativa": "Nativa",
    "tarjeta_mercadopago": "Tarjeta MercadoPago",
}


def _parse(value):
    """Parse an old serialized list value. Returns (list, ok)."""
    if not value:
        return [], True
    try:
        parsed = safe_eval(value)
    except Exception:
        return [], False
    if not isinstance(parsed, list):
        return [], False
    return parsed, True


def _log_unresolved(env, provider, issues):
    """Record what could not be migrated: server log + ir.logging (visible in Technical > Logging)."""
    message = "PAGOS360 coupon exclusions migration — provider %s (%s): %s" % (
        provider.id,
        provider.name or "",
        "; ".join(issues),
    )
    _logger.warning(message)
    try:
        env["ir.logging"].create(
            {
                "name": "payment_pagos360.migration",
                "type": "server",
                "level": "WARNING",
                "dbname": env.cr.dbname,
                "message": message,
                "path": "payment_pagos360/migrations/19.0.2.1.0/post-migrate.py",
                "func": "migrate",
                "line": "0",
            }
        )
    except Exception:
        # Logging must never break the upgrade; the server log warning above already kept the trace.
        _logger.exception("PAGOS360 migration: could not write ir.logging entry for provider %s", provider.id)


def migrate(cr, version):
    # Only run if the old columns are still present (skip fresh installs).
    cr.execute(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'payment_provider' AND column_name IN %s
        """,
        (_OLD_COLUMNS,),
    )
    present_set = {row[0] for row in cr.fetchall()}
    if not present_set:
        return
    present = [col for col in _OLD_COLUMNS if col in present_set]

    env = api.Environment(cr, SUPERUSER_ID, {})
    columns = sql.SQL(", ").join(sql.Identifier(col) for col in ["id", *present])
    cr.execute(
        sql.SQL("SELECT {} FROM payment_provider WHERE code = %s").format(columns),
        ("pagos360",),
    )
    rows = cr.fetchall()
    if not rows:
        return

    Channel = env["pagos360.channel"]
    Installment = env["pagos360.installment"]
    CardBrand = env["pagos360.card.brand"]

    for row in rows:
        provider = env["payment.provider"].browse(row[0])
        data = dict(zip(present, row[1:]))
        values = {}
        issues = []

        channel_codes, ok = _parse(data.get("pagos360_excluded_channels"))
        if not ok:
            issues.append("unparseable channels %r" % data.get("pagos360_excluded_channels"))
        if channel_codes:
            channels = Channel.search([("code", "in", channel_codes)])
            missing = set(channel_codes) - set(channels.mapped("code"))
            if missing:
                issues.append("unknown channels %s" % sorted(missing))
            values["pagos360_excluded_channel_ids"] = [Command.set(channels.ids)]

        raw_installments, ok = _parse(data.get("pagos360_excluded_installments"))
        if not ok:
            issues.append("unparseable installments %r" % data.get("pagos360_excluded_installments"))
        numbers = [n for n in raw_installments if isinstance(n, int)]
        if numbers:
            # Installments are open-ended: _get_or_create makes any number that wasn't seeded so no config is lost.
            installments = Installment._get_or_create(numbers)
            values["pagos360_excluded_installment_ids"] = [Command.set(installments.ids)]

        raw_brands, ok = _parse(data.get("pagos360_excluded_card_brands"))
        if not ok:
            issues.append("unparseable card brands %r" % data.get("pagos360_excluded_card_brands"))
        if raw_brands:
            brands = CardBrand.browse()
            for old_code in raw_brands:
                name = _CARD_BRAND_NAME_BY_CODE.get(old_code)
                if not name:
                    issues.append("unknown card brand code %r" % old_code)
                    continue
                brands |= CardBrand.search([("name", "=ilike", name)], limit=1) or CardBrand.create({"name": name})
            if brands:
                values["pagos360_excluded_card_brand_ids"] = [Command.set(brands.ids)]

        if values:
            try:
                provider.write(values)
                _logger.info("PAGOS360 migration: migrated coupon exclusions for provider %s", provider.id)
            except Exception as error:
                # A single bad provider must not abort the whole upgrade; the old columns stay intact.
                _logger.exception("PAGOS360 migration: write failed for provider %s", provider.id)
                issues.append("could not write new fields (%s) — left unchanged" % error)
        if issues:
            _log_unresolved(env, provider, issues)

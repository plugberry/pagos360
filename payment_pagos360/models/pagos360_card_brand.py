import unicodedata

from odoo import api, fields, models


def _normalize(value):
    """Lowercase, strip accents and non-alphanumerics for tolerant brand-name matching."""
    text = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode()
    return "".join(ch for ch in text.lower() if ch.isalnum())


class Pagos360CardBrand(models.Model):
    _name = "pagos360.card.brand"
    _description = "Pagos360 Card Brand"
    _order = "name"

    name = fields.Char(required=True)
    code = fields.Char(
        help="Numeric brand code Pagos360 expects in the excluded_card_brands payload. "
        "Filled/updated from the API on each 'Fetch available methods'; the string code is ignored by the API.",
    )

    _name_uniq = models.Constraint(
        "unique(name)",
        "The card brand name must be unique.",
    )

    @api.model
    def _upsert(self, brands_data):
        """Match/create card brands from the API helper response, refreshing their numeric code.

        Brands are matched by normalized name (the stable key); the numeric ``code`` is volatile
        (it is what Pagos360 actually expects in the payload) so it is refreshed on every sync.

        :param brands_data: list of ``{'name': str, 'code': str}`` from the channel-installments helper.
        :return: recordset of the matched/created brands.
        """
        by_norm = {_normalize(rec.name): rec for rec in self.search([])}
        result = self.browse()
        for brand in brands_data:
            name = (brand.get("name") or "").strip()
            if not name:
                continue
            code = str(brand.get("code") or "").strip()
            rec = by_norm.get(_normalize(name))
            if rec:
                if code and rec.code != code:
                    rec.code = code
            else:
                rec = self.create({"name": name, "code": code})
                by_norm[_normalize(name)] = rec
            result |= rec
        return result

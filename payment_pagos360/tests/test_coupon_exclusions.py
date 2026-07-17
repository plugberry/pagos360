from unittest.mock import patch

from odoo import Command
from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged

# Shape confirmed against the live "Obtener Planes y Cuotas" endpoint
# (GET /helper/channel-installments/{amount}): a list of brands (name + NUMERIC code),
# each with its installment plans.
API_METHODS = [
    {"name": "Visa", "code": "39", "installments": [{"installments": 1}, {"installments": 3}, {"installments": 6}]},
    {"name": "Mastercard", "code": "41", "installments": [{"installments": 1}, {"installments": 12}]},
    {"name": "American Express", "code": "43", "installments": [{"installments": 1}]},
]


@tagged("post_install", "-at_install")
class TestCouponExclusions(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.provider = cls.env["payment.provider"].search([("code", "=", "pagos360")], limit=1)
        cls.channel_credit = cls.env.ref("payment_pagos360.pagos360_channel_credit_card")
        cls.channel_debin = cls.env.ref("payment_pagos360.pagos360_channel_debin")
        Installment = cls.env["pagos360.installment"]
        cls.installment_3 = Installment._get_or_create([3])
        cls.installment_6 = Installment._get_or_create([6])
        cls.installment_24 = Installment._get_or_create([24])
        # _upsert (match-or-create) so the tests don't clash with brands a previous sync may have
        # left in the catalog (there is no seed; brands come from the API).
        CardBrand = cls.env["pagos360.card.brand"]
        cls.visa = CardBrand._upsert([{"name": "Visa", "code": "39"}])
        cls.mastercard = CardBrand._upsert([{"name": "Mastercard", "code": "41"}])

    def _mock_api(self, methods=None):
        methods = API_METHODS if methods is None else methods
        return patch.object(type(self.provider), "_pagos360_make_request", return_value=methods)

    def _set(self, channels=None, installments=None, brands=None):
        self.provider.write(
            {
                "pagos360_excluded_channel_ids": [Command.set(channels.ids if channels else [])],
                "pagos360_excluded_installment_ids": [Command.set(installments.ids if installments else [])],
                "pagos360_excluded_card_brand_ids": [Command.set(brands.ids if brands else [])],
            }
        )

    # --- _pagos360_get_coupon_exclusions ---------------------------------------------

    def test_payload_includes_all_selected(self):
        self._set(
            self.channel_credit + self.channel_debin,
            self.installment_3 + self.installment_6,
            self.visa + self.mastercard,
        )
        res = self.provider._pagos360_get_coupon_exclusions()
        self.assertEqual(set(res["excluded_channels"]), {"credit_card", "DEBIN"})
        self.assertEqual(set(res["excluded_installments"]), {3, 6})
        # Card brands go out as Pagos360's NUMERIC code, not the name/string (the API ignores the rest).
        self.assertEqual(set(res["excluded_card_brands"]), {"39", "41"})

    def test_payload_skips_card_brand_without_code(self):
        # A brand migrated from the old free-text config but never re-synced has no numeric code yet;
        # it must be skipped so we never send an identifier Pagos360 would silently ignore.
        no_code = self.env["pagos360.card.brand"].create({"name": "Marca sin código (test)"})
        self._set(brands=no_code + self.visa)
        res = self.provider._pagos360_get_coupon_exclusions()
        self.assertEqual(res["excluded_card_brands"], ["39"])

    def test_payload_empty_when_nothing_selected(self):
        self._set()
        self.assertEqual(self.provider._pagos360_get_coupon_exclusions(), {})

    def test_payload_partial_only_channels(self):
        self._set(channels=self.channel_debin)
        self.assertEqual(self.provider._pagos360_get_coupon_exclusions(), {"excluded_channels": ["DEBIN"]})

    # --- pagos360.installment quick-create (name_create) ------------------------------

    def test_installment_quick_create_new_number(self):
        installment_model = self.env["pagos360.installment"]
        new_id, _name = installment_model.name_create("15")
        self.assertEqual(installment_model.browse(new_id).number, 15)

    def test_installment_quick_create_reuses_existing(self):
        existing_id, _name = self.env["pagos360.installment"].name_create("3")
        self.assertEqual(existing_id, self.installment_3.id)

    def test_installment_quick_create_rejects_non_numeric(self):
        with self.assertRaises(UserError):
            self.env["pagos360.installment"].name_create("abc")

    # --- pagos360.card.brand._upsert -------------------------------------------------

    def test_brand_upsert_creates_and_refreshes_code(self):
        CardBrand = self.env["pagos360.card.brand"]
        created = CardBrand._upsert([{"name": "Cabal", "code": "45"}])
        self.assertEqual(created.name, "Cabal")
        self.assertEqual(created.code, "45")
        # Matched by name (case/accent-insensitive), numeric code refreshed.
        again = CardBrand._upsert([{"name": "CABAL", "code": "99"}])
        self.assertEqual(again, created)
        self.assertEqual(created.code, "99")

    # --- action_pagos360_sync_available_methods ---------------------------------------

    def test_sync_populates_available_from_api(self):
        with self._mock_api():
            self.provider.action_pagos360_sync_available_methods()
        # Installment numbers are the union across all brands in the payload.
        self.assertEqual(set(self.provider.pagos360_available_installment_ids.mapped("number")), {1, 3, 6, 12})
        avail = self.provider.pagos360_available_card_brand_ids
        self.assertEqual(set(avail.mapped("name")), {"Visa", "Mastercard", "American Express"})
        # The numeric code comes straight from the API response.
        self.assertEqual(avail.filtered(lambda b: b.name == "Visa").code, "39")

    def test_sync_prunes_stale_installment_exclusion(self):
        # 24 is excluded but the API does not offer it -> it must be dropped after sync.
        self._set(installments=self.installment_3 + self.installment_24)
        with self._mock_api():
            self.provider.action_pagos360_sync_available_methods()
        self.assertIn(self.installment_3, self.provider.pagos360_excluded_installment_ids)
        self.assertNotIn(self.installment_24, self.provider.pagos360_excluded_installment_ids)

    def test_sync_prunes_stale_brand_exclusion(self):
        # Mastercard is excluded but the API returns only Visa -> Mastercard must be dropped.
        self._set(brands=self.visa + self.mastercard)
        with self._mock_api([API_METHODS[0]]):
            self.provider.action_pagos360_sync_available_methods()
        excluded = self.provider.pagos360_excluded_card_brand_ids
        self.assertIn(self.visa, excluded)
        self.assertNotIn(self.mastercard, excluded)

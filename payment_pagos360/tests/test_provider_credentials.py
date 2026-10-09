from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestProviderCredentials(TransactionCase):
    """The form URL and the API keys are checked whenever the mode, the activation or a key
    changes, not only when the provider is enabled."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.provider = cls.env.ref("payment_pagos360.payment_provider_pagos360")
        cls.provider.write(
            {
                "active": True,
                "is_live": False,
                "pagos360_test_api_key": "test-key",
                "pagos360_api_key": "live-key",
                "allow_tokenization": True,
                "pagos360_form_url": "https://example.com/adhesion",
            }
        )

    def test_going_live_requires_form_url_when_tokenizing(self):
        self.provider.pagos360_form_url = False
        with self.assertRaises(ValidationError):
            self.provider.is_live = True

    def test_live_provider_cannot_clear_its_api_key(self):
        self.provider.is_live = True
        with self.assertRaises(UserError):
            self.provider.pagos360_api_key = False

    def test_reactivation_requires_the_key_of_its_mode(self):
        self.provider.active = False
        self.provider.pagos360_test_api_key = False
        with self.assertRaises(UserError):
            self.provider.active = True

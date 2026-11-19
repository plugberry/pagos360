from unittest.mock import patch

from odoo.tests.common import TransactionCase, tagged

PLATFORM_WEBHOOK = "https://cliente.adhoc.ar/payment/pagos360/webhook"
EVENTS = ("paid", "reverted")


@tagged("post_install", "-at_install")
class TestEnsureWebhook(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.provider = cls.env.ref("payment_pagos360.payment_provider_pagos360")
        cls.provider.write({"active": True, "is_live": False})
        cls.icp = cls.env["ir.config_parameter"].sudo()
        cls.icp.set_str("saas_client.platform_url", "https://cliente.adhoc.ar")

    def _ensure(self, registered, per_page=100):
        """Press the button against an account that has the `registered` webhooks; return the calls."""
        calls = []

        def fake_request(endpoint, data=None, method="POST"):
            calls.append((method, endpoint, data))
            if method == "GET":
                page = int(endpoint.rsplit("page=", 1)[1])
                chunk = registered[(page - 1) * per_page : page * per_page]
                return {"data": chunk, "total_count": len(registered)}
            return {"id": 99}

        provider_cls = type(self.provider)
        with (
            patch.object(provider_cls, "_pagos360_make_request", side_effect=fake_request, autospec=False),
            patch.object(provider_cls, "_get_event_types", return_value=list(EVENTS), autospec=False),
        ):
            self.provider.ensure_webhook()
        return calls

    def _webhook(self, webhook_id, url, events=EVENTS):
        return {"id": webhook_id, "url": url, "events": [{"id": event} for event in events]}

    def test_registers_platform_domain_and_drops_other_odoo_domains(self):
        self.provider.write({"pagos360_api_key": "live-key", "is_live": True})
        calls = self._ensure(
            [
                self._webhook(1, "https://www.colegio.edu.ar/payment/pagos360/webhook"),
                self._webhook(2, "https://www.otro-sitio.com.ar/payment/pagos360/webhook/"),
                self._webhook(3, "https://api.autogestion.io/pagos360/notify"),
            ]
        )
        self.assertIn(("DELETE", "/webhook/1", None), calls)
        self.assertIn(("DELETE", "/webhook/2", None), calls)
        self.assertNotIn(("DELETE", "/webhook/3", None), calls, "third-party webhooks are kept")
        posts = [data for method, _endpoint, data in calls if method == "POST"]
        self.assertEqual(posts, [{"webhook": {"url": PLATFORM_WEBHOOK, "event_types": list(EVENTS)}}])

    def test_platform_webhook_already_set_is_kept(self):
        self.provider.write({"pagos360_api_key": "live-key", "is_live": True})
        calls = self._ensure(
            [
                self._webhook(1, PLATFORM_WEBHOOK),
                self._webhook(2, "https://www.colegio.edu.ar/payment/pagos360/webhook"),
            ]
        )
        self.assertEqual([c for c in calls if c[0] != "GET"], [("DELETE", "/webhook/2", None)])

    def test_test_mode_keeps_the_webhooks_of_other_databases(self):
        calls = self._ensure([self._webhook(1, "https://123-20-0-all.runbot.example.com/payment/pagos360/webhook")])
        self.assertNotIn(("DELETE", "/webhook/1", None), calls, "the sandbox account is shared")
        posts = [data for method, _endpoint, data in calls if method == "POST"]
        self.assertEqual(posts[0]["webhook"]["url"], PLATFORM_WEBHOOK)

    def test_reads_every_page_of_the_account(self):
        self.provider.write({"pagos360_api_key": "live-key", "is_live": True})
        others = [self._webhook(i, f"https://old-{i}.example.com/payment/pagos360/webhook") for i in range(1, 4)]
        calls = self._ensure([*others, self._webhook(9, PLATFORM_WEBHOOK)], per_page=2)
        self.assertEqual([c[1] for c in calls if c[0] == "DELETE"], ["/webhook/1", "/webhook/2", "/webhook/3"])
        self.assertFalse([c for c in calls if c[0] == "POST"], "the platform webhook was on the second page")

    def test_platform_webhook_with_other_events_is_recreated(self):
        calls = self._ensure([self._webhook(1, PLATFORM_WEBHOOK, events=["paid"])])
        self.assertIn(("DELETE", "/webhook/1", None), calls)
        self.assertTrue(any(method == "POST" for method, _endpoint, _data in calls))

    def test_without_platform_parameter_uses_the_base_url(self):
        self.icp.set_str("saas_client.platform_url", False)
        base_url = self.provider.get_base_url().replace("http:", "https:")
        calls = self._ensure([])
        posts = [data for method, _endpoint, data in calls if method == "POST"]
        self.assertEqual(posts[0]["webhook"]["url"], base_url.rstrip("/") + "/payment/pagos360/webhook")

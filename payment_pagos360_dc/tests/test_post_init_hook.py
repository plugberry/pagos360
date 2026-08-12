from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from .. import post_init_hook


@tagged("post_install", "-at_install")
class TestPostInitHook(TransactionCase):
    def test_hook_migrates_validity_days_and_enables_tokenization(self):
        provider = self.env["payment.provider"].create(
            {
                "name": "Pagos360 Legacy",
                "code": "pagos360",
                "state": "test",
                "pagos360_test_api_key": "test-key",
                "validity_days": 30,
            }
        )
        payment_method = self.env["payment.method"].with_context(active_test=False).search([("code", "=", "pagos360")])
        # Se pisa por SQL, no `.write()`: el `write()` de `payment.method` archiva los
        # tokens vinculados al bloquear tokenización, lo que dispara la cancelación real
        # contra la API de Pagos360 en `payment.token.write()` — hay un token de demo data
        # (`pagos360_tests_token`) activo y vinculado a este método en cualquier DB con
        # demo data. Acá solo queremos simular el estado pre-fix del campo.
        self.env.cr.execute(
            "UPDATE payment_method SET support_tokenization = false WHERE id = %s",
            (payment_method.id,),
        )
        payment_method.invalidate_recordset(["support_tokenization"])

        post_init_hook(self.env)

        self.assertEqual(provider.pagos360_coupon_validity_days, 30)
        self.assertTrue(payment_method.support_tokenization)

    def test_hook_reads_legacy_cut_day_system_parameter(self):
        provider = self.env["payment.provider"].create(
            {
                "name": "Pagos360 Legacy",
                "code": "pagos360",
                "state": "test",
                "pagos360_test_api_key": "test-key",
            }
        )
        self.env["ir.config_parameter"].sudo().set_param("pagos360.cut_day", "25")

        post_init_hook(self.env)

        self.assertEqual(provider.pagos360_cut_days, "25")

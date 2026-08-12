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

    def test_hook_falls_back_to_default_cut_days_when_legacy_value_out_of_range(self):
        provider = self.env["payment.provider"].create(
            {
                "name": "Pagos360 Legacy",
                "code": "pagos360",
                "state": "test",
                "pagos360_test_api_key": "test-key",
            }
        )
        # El código viejo no tenía tope superior (comparaba `day > cut_day`), así que un
        # cliente pudo haber configurado legítimamente un corte de fin de mes.
        self.env["ir.config_parameter"].sudo().set_param("pagos360.cut_day", "30")

        post_init_hook(self.env)

        self.assertEqual(provider.pagos360_cut_days, "19")

    def test_hook_falls_back_to_default_coupon_validity_when_legacy_value_is_zero(self):
        provider = self.env["payment.provider"].create(
            {
                "name": "Pagos360 Legacy",
                "code": "pagos360",
                "state": "test",
                "pagos360_test_api_key": "test-key",
                "validity_days": 0,
            }
        )

        post_init_hook(self.env)

        # `pagos360_coupon_validity_days` exige mínimo 1 día — 0 no es un valor migrable,
        # cae al default de fábrica en vez de dejar el provider en un estado inválido.
        self.assertEqual(provider.pagos360_coupon_validity_days, 15)

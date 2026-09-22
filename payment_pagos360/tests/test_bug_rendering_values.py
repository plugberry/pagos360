from odoo.exceptions import ValidationError
from odoo.tests.common import tagged

from .common import Pagos360TransactionCase

REFERENCE = "TEST/BUG/RENDER"


@tagged("post_install", "-at_install")
class TestRenderingValues(Pagos360TransactionCase):
    """Rendering a Pagos360 payment must fail with an explicit error, never with a NameError.

    Dos comportamientos del mismo mecanismo:

    1. Dado una transacción con un método de pago que el proveedor no sabe renderizar, cuando
       se arman los valores de render, entonces sale un error explicado.
       Bug: `api_url` queda sin asignar y sale un UnboundLocalError.
    2. Dado una respuesta de Pagos360 sin `checkout_url`, cuando se arman los valores,
       entonces también sale un error explicado, no un KeyError.

    Se demuestra en rojo: hoy los dos casos levantan otra excepción que la esperada.
    """

    def _api_response(self, **extra):
        return dict({"id": 999801}, **extra)

    def test_unknown_payment_method_raises_an_explicit_error(self):
        method = self.env["payment.method"].create({"name": "Otro", "code": "otro_pagos360"})
        self.provider.payment_method_ids = [(4, method.id)]
        tx = self._make_tx(REFERENCE, payment_method_id=method.id)
        with self._patch_api(return_value=self._api_response(checkout_url="https://example.com/checkout")):
            with self.assertRaises(ValidationError):
                tx._get_specific_rendering_values({})

    def test_missing_checkout_url_raises_an_explicit_error(self):
        tx = self._make_tx(REFERENCE + "/NOURL")
        with self._patch_api(return_value=self._api_response()):
            with self.assertRaises(ValidationError):
                tx._get_specific_rendering_values({})

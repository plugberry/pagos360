from odoo.addons.payment import utils as payment_utils
from odoo.tests.common import HttpCase, tagged

from .common import Pagos360Common, Pagos360TransactionCase

REFERENCE = "TEST/BUG/COUPON"
ENTITY_ID = 999401


@tagged("post_install", "-at_install")
class TestCouponControllerState(Pagos360Common, HttpCase):
    """The cash coupon controllers must not write the provider state into the transaction.

    Dado una transacción de cupón en borrador, cuando el cliente abre el cupón y Pagos360
    ya la informa pagada, entonces la transacción queda en `done` por la máquina de estados
    de `payment` (con su `account.payment`), no con el valor crudo que devolvió la API.

    Bug: `main.py` hace `write({"state": values.get("state")})`, que saltea `_set_done` y
    escribe un valor que ni siquiera pertenece al selection.

    Se demuestra en rojo: hoy falla; pasa cuando el controlador delegue en `_process`.
    """

    def _api_entity(self, state="paid"):
        return {
            "data": [
                {
                    "id": ENTITY_ID,
                    "state": state,
                    "external_reference": REFERENCE,
                    "pdf_url": "https://example.com/coupon.pdf",
                    "rapipago_barcode_url": "https://example.com/barcode.svg",
                    "request_result": [{"amount": 500.0}],
                }
            ]
        }

    def _open_coupon(self, route, tx):
        access_token = payment_utils.generate_access_token(tx.partner_id.id, tx.amount, tx.currency_id.id, env=self.env)
        return self.url_open(f"/payment/pagos360/{route}?tx_id={tx.id}&access_token={access_token}")

    def test_rapipago_coupon_does_not_write_the_raw_provider_state(self):
        tx = self._make_tx(REFERENCE)
        with self._patch_api(return_value=self._api_entity("paid")):
            response = self._open_coupon("rapipago", tx)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(tx.state, "done", "the coupon controller must go through the state machine")

    def test_pagofacil_coupon_does_not_write_the_raw_provider_state(self):
        tx = self._make_tx(REFERENCE + "/PF")
        with self._patch_api(return_value=self._api_entity("paid")):
            response = self._open_coupon("pagofacil", tx)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(tx.state, "done", "the coupon controller must go through the state machine")


@tagged("post_install", "-at_install")
class TestOperationInfoShape(Pagos360TransactionCase):
    """`_get_operation_info_from_data` must always return a mapping.

    Dado una respuesta de Pagos360 que no trae la referencia de la transacción, cuando se
    extrae la operación, entonces se devuelve un dict vacío — los llamadores hacen `.get()`
    sobre el resultado.

    Bug: devuelve `[]`, así que el llamador revienta con AttributeError/KeyError en vez de
    seguir por la rama "no lo encontré".

    Se demuestra en rojo: hoy devuelve una lista; pasa cuando devuelva `{}`.
    """

    def test_returns_an_empty_mapping_when_the_reference_is_not_in_the_response(self):
        tx = self._make_tx(REFERENCE + "/SHAPE")
        result = tx._get_operation_info_from_data({"data": [{"external_reference": "another-one", "id": 1}]})
        self.assertEqual(result, {})
        self.assertIsNone(result.get("pdf_url"))

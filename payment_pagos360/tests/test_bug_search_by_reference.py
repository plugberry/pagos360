from odoo.tests.common import tagged
from odoo.tools import mute_logger

from .common import Pagos360TransactionCase

ENTITY_ID = 999601


@tagged("post_install", "-at_install")
class TestSearchByReference(Pagos360TransactionCase):
    """Looking up the transaction of a notification must land on exactly one, and the right one.

    Dos comportamientos del mismo mecanismo:

    1. Dado una transacción de adhesión y una notificación de `payment_request` con esa misma
       referencia externa, cuando se busca la transacción, entonces la de adhesión no
       responde: el cobro del cupón y el débito de la adhesión son operaciones distintas.
       Bug: el filtro mira `payload["entity_name"]`, que nunca existe — `entity_name` viaja
       en el payment_data, no en el payload — así que nunca se aplica.
    2. Dado dos transacciones donde una tiene como `provider_reference` el id de la entidad y
       otra tiene esa referencia externa, cuando llega una notificación de débito, entonces
       la búsqueda devuelve una sola.
       Bug: el `search` no acota el resultado y `_process` revienta con ensure_one().

    Se demuestra en rojo: hoy devuelve la transacción de adhesión / dos registros.
    """

    def _notification(self, entity_name, external_reference, entity_id=ENTITY_ID):
        return {
            "entity_name": entity_name,
            "entity_id": entity_id,
            "type": "paid",
            "payload": {"id": entity_id, "external_reference": external_reference},
        }

    @mute_logger("odoo.addons.payment_pagos360.models.payment_transaction")
    def test_payment_request_notification_skips_an_adhesion_transaction(self):
        token = self._make_token(adhesion_type="adhesion")
        tx = self._make_tx("TEST/BUG/SEARCH/ADH", operation="online_token", token_id=token.id)
        self.assertEqual(tx.pagos360_adhesion_type, "adhesion")

        found = self.env["payment.transaction"]._search_by_reference(
            "pagos360", self._notification("payment_request", tx.reference)
        )
        self.assertFalse(found, "a payment_request notification must not match the adhesion transaction")

    @mute_logger("odoo.addons.payment_pagos360.models.payment_transaction")
    def test_debit_notification_returns_a_single_transaction(self):
        by_provider_reference = self._make_tx("TEST/BUG/SEARCH/ONE", provider_reference=str(ENTITY_ID))
        self._make_tx(str(ENTITY_ID))  # its reference is the external_reference of the notification

        found = self.env["payment.transaction"]._search_by_reference(
            "pagos360", self._notification("debit_request", str(ENTITY_ID))
        )
        self.assertEqual(len(found), 1, "the lookup must not return several transactions")
        self.assertEqual(found, by_provider_reference)

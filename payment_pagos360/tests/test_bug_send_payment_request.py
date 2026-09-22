from contextlib import contextmanager
from unittest.mock import patch

from odoo.tests.common import tagged

from .common import Pagos360TransactionCase

REFERENCE = "TEST/BUG/DEBIT"
ENTITY_ID = 999501


@tagged("post_install", "-at_install")
class TestSendPaymentRequest(Pagos360TransactionCase):
    """Charging a token must handle each adhesion type once, and only those it knows.

    Tres comportamientos del mismo mecanismo:

    1. Dado una transacción con un token sin tipo de adhesión (migrado, o creado por otro
       flujo), cuando se la cobra, entonces no se rompe: no hay débito que enviar.
       Bug: `req` queda sin definir y sale un UnboundLocalError.
    2. Dado una transacción con token de tarjeta, cuando se la cobra, entonces la respuesta
       de Pagos360 se procesa una sola vez.
       Bug: se llama `_process` en la rama de tarjeta y otra vez en el `if req` de abajo.
    3. Dado cualquier cobro con token, cuando se lo envía, entonces no se commitea la
       transacción de base a mitad de camino.
       Bug: los dos `cr.commit()` del método parten la operación en el medio — y en 19
       hacen que el método no se pueda testear, por eso los dos primeros los neutralizan.

    Se demuestra en rojo: hoy fallan los tres.
    """

    def _api_response(self):
        return {"id": ENTITY_ID, "state": "pending", "external_reference": REFERENCE}

    @contextmanager
    def _without_explicit_commits(self):
        """Neutralize the commits of `_send_payment_request` (see behaviour 3).

        Without this, the test cursor refuses to commit and hides what we came to verify.
        """
        with patch.object(self.env.cr, "commit"):
            yield

    def test_token_without_adhesion_type_does_not_crash_the_charge(self):
        token = self._make_token(adhesion_type=False)
        tx = self._make_tx(REFERENCE, operation="online_token", token_id=token.id)
        with self._without_explicit_commits(), self._patch_api(return_value=self._api_response()):
            tx._send_payment_request()  # must not raise
        self.assertEqual(tx.state, "draft")

    def test_card_adhesion_response_is_processed_once(self):
        token = self._make_token(adhesion_type="card_adhesion")
        tx = self._make_tx(REFERENCE + "/CARD", operation="online_token", token_id=token.id)
        with (
            self._without_explicit_commits(),
            self._patch_api(return_value=self._api_response()),
            patch.object(type(self.env["payment.transaction"]), "_process", return_value=None) as process,
        ):
            tx._send_payment_request()
        self.assertEqual(process.call_count, 1, "the same response must not be processed twice")

    def test_charge_does_not_commit_halfway(self):
        token = self._make_token(adhesion_type="card_adhesion")
        tx = self._make_tx(REFERENCE + "/COMMIT", operation="online_token", token_id=token.id)
        with (
            self._patch_api(return_value=self._api_response()),
            patch.object(type(self.env["payment.transaction"]), "_process", return_value=None),
            patch.object(self.env.cr, "commit") as commit,
        ):
            tx._send_payment_request()
        self.assertFalse(commit.called, "the charge must not commit the database transaction on its own")

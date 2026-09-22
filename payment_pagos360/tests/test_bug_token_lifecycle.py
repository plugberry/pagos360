from unittest.mock import patch

from odoo.exceptions import ValidationError
from odoo.tests.common import tagged
from odoo.tools import config

from .common import Pagos360TransactionCase


@tagged("post_install", "-at_install")
class TestTokenArchiving(Pagos360TransactionCase):
    """Archiving a token must not depend on Pagos360 answering.

    Dado un token de adhesión, cuando se lo archiva y Pagos360 no responde, entonces el
    token queda archivado igual y el problema se loguea: la baja en Odoo no puede quedar
    atada a la disponibilidad del proveedor.

    Bug: se captura `RequestException`, pero `_pagos360_make_request` levanta
    `ValidationError` — el except no atrapa nada y el archivado explota.

    Se demuestra en rojo: hoy propaga la ValidationError; pasa cuando el except la cubra.
    """

    def test_archiving_survives_an_unreachable_provider(self):
        token = self._make_token(adhesion_type="adhesion")
        # _handle_archiving opts out while running tests; this bug only shows with it active.
        with (
            patch.dict(config.options, {"test_enable": False}),
            self._patch_api(side_effect=ValidationError("Pagos360 is down")),
            self.assertLogs("odoo.addons.payment_pagos360.models.payment_token", level="ERROR"),
        ):
            token.active = False
        self.assertFalse(token.active)


@tagged("post_install", "-at_install")
class TestTokenDisplayName(Pagos360TransactionCase):
    """Every Pagos360 token must have a readable name.

    Dos comportamientos del mismo compute:

    1. Dado un token de adhesión CBU sin número de CBU (la API no lo devolvió), cuando se lee
       su nombre, entonces se obtiene un texto, no un error.
       Bug: `token.pagos360_cbu_number[-5:]` sobre False.
    2. Dado un token de Pagos360 sin tipo de adhesión, cuando se lee su nombre, entonces cae
       al comportamiento estándar de `payment`.
       Bug: el compute no le asigna valor a ese registro.

    Se demuestra en rojo: hoy los dos casos revientan al leer `display_name`.
    """

    def test_cbu_adhesion_without_cbu_number(self):
        token = self._make_token(adhesion_type="adhesion", pagos360_bank="BANCO TEST", pagos360_cbu_number=False)
        self.assertTrue(token.display_name)

    def test_token_without_adhesion_type_falls_back_to_the_standard_name(self):
        token = self._make_token(adhesion_type=False, payment_details="1234")
        self.assertTrue(token.display_name)

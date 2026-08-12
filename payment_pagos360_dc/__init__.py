import logging

from odoo.exceptions import ValidationError

from . import models

_logger = logging.getLogger(__name__)


def post_init_hook(env):
    providers = env["payment.provider"].search([("code", "=", "pagos360")])
    if providers:
        cut_day_param = env["ir.config_parameter"].sudo().get_param("pagos360.cut_day")
        for provider in providers:
            # Los `@api.constrains` de Odoo validan DESPUÉS de escribir: si el valor legado
            # es inválido para el campo nuevo, el `write()` ya aplicó ese valor antes de
            # lanzar `ValidationError` — atajar la excepción sin reescribir un valor válido
            # deja el campo en el estado inválido. Por eso cada `except` reescribe el
            # default explícito, no solo loguea.
            try:
                provider.pagos360_coupon_validity_days = provider.validity_days
            except ValidationError:
                # `validity_days` no tenía piso (0 era un valor legal); `pagos360_coupon_
                # validity_days` exige mínimo 1 día (0 días de validez de cupón no tiene
                # sentido funcional). Se preserva el default de fábrica (15).
                provider.pagos360_coupon_validity_days = 15
                _logger.warning(
                    "PAGOS360: el valor legado de validity_days (%s) no es válido para "
                    "pagos360_coupon_validity_days (mínimo 1 día) en el provider %s; se "
                    "usa el default. Reconfigurar manualmente si corresponde.",
                    provider.validity_days,
                    provider.display_name,
                )
            if cut_day_param:
                try:
                    provider.pagos360_cut_days = str(int(cut_day_param))
                except ValidationError:
                    # El parámetro legado no tenía tope superior (permitía 29-31); el
                    # constraint nuevo exige 1-28. Se preserva el default "19".
                    provider.pagos360_cut_days = "19"
                    _logger.warning(
                        "PAGOS360: el valor legado de pagos360.cut_day (%s) está fuera del "
                        "rango 1-28 soportado por pagos360_cut_days en el provider %s; se "
                        "usa el default. Reconfigurar manualmente si corresponde.",
                        cut_day_param,
                        provider.display_name,
                    )

    # El payment.method base no marca support_tokenization: sin esto, Odoo core
    # (payment.method._get_compatible_payment_methods / controllers/portal.py) nunca ofrece
    # la opción de guardar el medio de pago para Pagos360, y el flujo de transacciones hijas
    # (que depende de tokenize=True) no es alcanzable desde el checkout real. Se escribe por
    # ORM en vez de un <record> de datos porque el registro original está en noupdate="1":
    # un override por XML se aplicaría en el install inicial pero se ignoraría en upgrades
    # posteriores del módulo (ver _load_records: `not (update and d_noupdate)`).
    # `active_test=False`: el payment.method de pagos360 viene desactivado por default en
    # el módulo base (se activa cuando el cliente habilita el provider) — sin esto, el
    # `search` no lo encuentra y el fix nunca se aplica.
    payment_method = env["payment.method"].with_context(active_test=False).search([("code", "=", "pagos360")])
    if payment_method and not payment_method.support_tokenization:
        payment_method.support_tokenization = True

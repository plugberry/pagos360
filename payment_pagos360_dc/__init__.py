from . import models


def post_init_hook(env):
    providers = env["payment.provider"].search([("code", "=", "pagos360")])
    if providers:
        cut_day_param = env["ir.config_parameter"].sudo().get_param("pagos360.cut_day")
        for provider in providers:
            provider.pagos360_coupon_validity_days = provider.validity_days or 15
            if cut_day_param:
                provider.pagos360_cut_days = str(int(cut_day_param))

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

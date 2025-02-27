from odoo import Command
from odoo import SUPERUSER_ID, api

def migrate(cr, version):
    """
    Post-migration script to ensure that when the module is already installed previously,
    the change to newer payment method is applied despite the noupdate attribute in the provider data
    """
    env = api.Environment(cr, SUPERUSER_ID, {})
    payment_method_pagos360 = env.ref('payment_pagos360.payment_method_pagos360')
    payment_method_pagofacil = env.ref('payment_pagos360.payment_method_pagofacil')
    payment_method_rapipago = env.ref('payment_pagos360.payment_method_rapipago')

    # Search for all payment providers with code 'pagos360' across all companies
    deactivated_providers = env['payment.provider'].search([
        ('code', '=', 'pagos360'),
        ('state', '=', 'disabled')
    ])
    activated_providers = env['payment.provider'].search([
        ('code', '=', 'pagos360'),
        ('state', '!=', 'disabled')
    ])

    # Is necesary to validate if all the providers are disabled.
    # This is because if all of them are disabled then it wont be possible to link
    # the provider with the method due to L176 of payment/models/payment_method.py
    if not activated_providers and deactivated_providers:
        # Use one disabled provider and set it to 'test' in order to link the method
        # Then rollback the changes.
        provider = deactivated_providers[0]

        backup_values = {
            'pagos360_test_api_key': provider.pagos360_test_api_key,
            'journal_id': provider.journal_id.id,
        }

        journal_ids = env['account.journal'].search([
            ('type', '=', 'bank'),
            ('company_id', '=', provider.company_id.id),
        ]).ids

        provider.write({
            'state': 'test',
            'payment_method_ids': [Command.set([
                payment_method_pagos360.id if payment_method_pagos360 else False,
                payment_method_pagofacil.id if payment_method_pagofacil else False,
                payment_method_rapipago.id if payment_method_rapipago else False,
            ])],
            'pagos360_test_api_key': backup_values['pagos360_test_api_key'] or 'dummyToken123',
            'journal_id': backup_values['journal_id'] or journal_ids[0],
        })

        # Update payment providers
        deactivated_providers.write({
            'payment_method_ids': [Command.set([
                payment_method_pagos360.id if payment_method_pagos360 else False,
                payment_method_pagofacil.id if payment_method_pagofacil else False,
                payment_method_rapipago.id if payment_method_rapipago else False,
            ])],
        })

        provider.write({
            'state': 'disabled',
            'pagos360_test_api_key': backup_values['pagos360_test_api_key'],
            'journal_id': backup_values['journal_id'],
        })

        deactivated_providers._deactivate_unsupported_payment_methods()
    else:
        (activated_providers + deactivated_providers).write({
            'payment_method_ids': [Command.set([
                payment_method_pagos360.id if payment_method_pagos360 else False,
                payment_method_pagofacil.id if payment_method_pagofacil else False,
                payment_method_rapipago.id if payment_method_rapipago else False,
            ])],
        })

        activated_providers._activate_default_pms()

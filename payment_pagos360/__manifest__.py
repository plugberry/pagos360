{
    'name': 'Payment Provider: Pagos 360',
    'version': '16.0.1.0',
    'category': 'Accounting/Payment Providers',
    'sequence': 350,
    'depends': ['payment'],
    'data': [
        'views/payment_provider_views.xml',
        'views/payment_pagos360_template.xml',
        'data/payment_provider_data.xml',
    ],
    'assets': {
        'web.assets_frontend': [
        ],
    },
    'license': 'LGPL-3',
    'application': True,
    'installable': True,
    'post_init_hook': 'post_init_hook',
    'uninstall_hook': 'uninstall_hook',
}

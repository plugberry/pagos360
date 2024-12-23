{
    'name': 'Payment Provider: Pagos 360',
    'version': "17.0.2.0.0",
    'category': 'Accounting/Payment Providers',
    'sequence': 350,
    'depends': ['account_payment'],
    'data': [
        'views/payment_provider_views.xml',
        'views/payment_pagos360_template.xml',
        'views/payment_transaction_views.xml',
        'data/payment_provider_data.xml',
    ],
    'demo': [
        'demo/payment_provider_demo.xml',
    ],
    'license': 'LGPL-3',
    'application': True,
    'installable': True,
    'post_init_hook': 'post_init_hook',
    'uninstall_hook': 'uninstall_hook',
}

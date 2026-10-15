{
<<<<<<< 46afca8d5b9bed90a0cffff5410e6e01cb07043d
    "name": "Payment Provider: Pagos 360",
    "version": "18.0.1.3.0",
    "category": "Accounting/Payment Providers",
    "sequence": 350,
    "author": "Plugberry",
    "depends": ["account_payment"],
    "data": [
        "views/payment_provider_views.xml",
        "views/payment_pagos360_template.xml",
        "views/payment_transaction_views.xml",
        "data/ir_config_parameter_data.xml",
        "data/payment_method_data.xml",
        "data/payment_provider_data.xml",
||||||| 5f07c217381b7c14381c2d8e377d620ec9aba9a4
    'name': 'Payment Provider: Pagos 360',
    'version': "17.0.3.2.0",
    'category': 'Accounting/Payment Providers',
    'sequence': 350,
    'depends': ['account_payment'],
    'data': [
        'views/payment_provider_views.xml',
        'views/payment_pagos360_template.xml',
        'views/payment_transaction_views.xml',
        'data/payment_method_data.xml',
        'data/payment_provider_data.xml',
=======
    'name': 'Payment Provider: Pagos 360',
    'version': "17.0.3.2.0",
    'category': 'Accounting/Payment Providers',
    'sequence': 350,
    'depends': ['account_payment'],
    'data': [
        'views/payment_provider_views.xml',
        'views/payment_pagos360_template.xml',
        'views/payment_transaction_views.xml',
        'data/payment_method_data.xml',
        'data/payment_provider_data.xml',
        'data/ir_cron_data.xml',
>>>>>>> 67c1fd4c965e834192dce6711c5230d534fb71a3
    ],
    "demo": [
        "demo/payment_provider_demo.xml",
    ],
    "license": "LGPL-3",
    "application": True,
    "installable": True,
    "post_init_hook": "post_init_hook",
    "uninstall_hook": "uninstall_hook",
}

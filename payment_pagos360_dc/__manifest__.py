{
    "name": "Pagos 360 - Director's Cut",
    "version": "18.0.1.0.0",
    "category": "Accounting/Payment Providers",
    "author": "Plugberry, Adhoc",
    "depends": ["payment_pagos360"],
    "data": [
        "views/payment_provider_views.xml",
        "views/payment_transaction_views.xml",
    ],
    "license": "LGPL-3",
    "installable": True,
    "post_init_hook": "post_init_hook",
}

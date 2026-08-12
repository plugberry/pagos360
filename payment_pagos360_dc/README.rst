==========================
Pagos 360 - Director's Cut
==========================

Módulo puente (``_inherit``) sobre ``payment_pagos360`` que agrega, solo para quien lo
instale, dos mejoras portadas desde ``payment_pagos360_19`` (Odoo 19) sin tocar el
conector base estable:

1. **Transacciones hijas para adhesión/tokenización**: al pedir tokenizar (guardar
   método de pago) contra un provider Pagos360 con formulario de adhesión configurado,
   la transacción se convierte a ``validation`` (importe 0) y, una vez firmada la
   adhesión y creado el token, se dispara automáticamente una transacción hija
   ``online_token`` por el importe real, contra el token recién creado.
2. **Separación de días de validez (``validity_days``)** en cuatro campos
   independientes del provider: ``pagos360_debit_execution_days``,
   ``pagos360_coupon_validity_days``, ``pagos360_cut_days`` y
   ``pagos360_debit_use_invoice_due``, más el campo ``pagos360_debit_execution_date``
   en la transacción.

¿Quién debería instalarlo?
==========================

Clientes en Odoo 18 que usan ``payment_pagos360`` y quieren estas dos mejoras (ya
validadas en la migración a Odoo 19) sin pasar por el upgrade completo de versión.
Instalar este módulo no modifica ningún archivo de ``payment_pagos360``; todo el
cambio es por herencia.

Migración de datos
==================

Al instalar el módulo, un ``post_init_hook`` migra automáticamente, para los
providers existentes con ``code == "pagos360"``:

- El valor que el cliente ya tenía en ``validity_days`` → ``pagos360_coupon_validity_days``
  (preserva la configuración existente, no pisa con el default salvo que el valor legado
  ya no sea válido para el campo nuevo — ej. ``0``, rechazado por el mínimo de 1 día —
  en cuyo caso cae al default y se loguea un warning para reconfigurar a mano).
- El parámetro de sistema ``pagos360.cut_day`` (si existía) → ``pagos360_cut_days``
  (mismo criterio: si el valor legado no entra en el rango 1-28 del campo nuevo, cae al
  default ``"19"`` con warning).

El campo ``validity_days`` del módulo base no se toca ni se elimina.

El mismo hook también activa ``support_tokenization`` en el ``payment.method`` de
Pagos360: sin esto, el checkout de Odoo nunca ofrece la opción de guardar el medio de
pago para este provider, y el flujo de transacciones hijas (que depende de
``tokenize=True``) no es alcanzable en la práctica. Se resuelve por ORM en el hook, no
por dato XML, porque el registro original vive en ``noupdate="1"``.

Especificación
==============

El diseño funcional completo (user stories, acceptance criteria, escenarios
Given/When/Then y clarificaciones) vive en las specs del workspace OBA:

- ``oba/specs/10_draft/pagos/payment-pagos360-oba.md``
- ``oba/specs/10_draft/pagos/payment-pagos360-oba-transacciones-hijas.md``
- ``oba/specs/10_draft/pagos/payment-pagos360-separar-validity-days.md``

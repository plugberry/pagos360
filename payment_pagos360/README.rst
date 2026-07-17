===========================
Payment Provider: Pagos 360
===========================

Integración del proveedor de pagos Pagos360 con el flujo de pagos de Odoo
(solicitudes de pago / cupón, adhesiones para débito automático y webhooks).

Características
===============

- Agrega Pagos360 como proveedor de pago (``code = 'pagos360'``).
- Genera la solicitud de pago (cupón) contra la API de Pagos360, con fecha de
  validez configurable (``validity_days``).
- Soporte de tokenización vía adhesión: cuando se permite guardar el medio de
  pago, redirige al formulario de adhesión de Pagos360 (``pagos360_form_url``) y
  dispara el cobro hijo sobre el token.
- Configuración de webhooks (alta y verificación) para los eventos de Pagos360.
- Exclusión de medios en el cupón mediante etiquetas: el administrador
  selecciona qué canales, cuotas y marcas de tarjeta quitar de las opciones que
  ve el pagador. Lo seleccionado se envía a la API como ``excluded_channels``,
  ``excluded_installments`` y ``excluded_card_brands``.
- Canales, cuotas y marcas de tarjeta se modelan con catálogos propios: los canales
  son un conjunto fijo; las cuotas (por número) y las marcas (nombre + código
  numérico) se pueblan/actualizan desde la API en cada sincronización ("Fetch
  available methods"), y los selectores de exclusión se acotan a lo disponible.
- ``excluded_card_brands`` se envía con el **código numérico** que espera Pagos360
  (ej. ``39`` = Visa), no con el nombre: la API descarta en silencio cualquier otro
  identificador. El código no es estable, por eso se refresca desde la API en cada
  "Fetch available methods".

Detalles Técnicos
=================

Modelos nuevos:

- ``pagos360.channel``: catálogo de canales de pago (``code`` técnico enviado a la
  API + ``name`` legible).
- ``pagos360.installment``: catálogo de cuotas (``number``). Se puebla desde la API
  en la sincronización (``_get_or_create``); también soporta alta por número
  (``name_create``).
- ``pagos360.card.brand``: catálogo de marcas de tarjeta (``name`` + ``code``
  numérico de Pagos360). Se puebla/actualiza desde la API en la sincronización
  (``_upsert``, match por nombre normalizado).

Modelos heredados:

- ``payment.provider``: credenciales (API keys), ``pagos360_form_url``,
  ``validity_days`` y los tres campos de exclusión
  (``pagos360_excluded_channel_ids``, ``pagos360_excluded_installment_ids``,
  ``pagos360_excluded_card_brand_ids``), más los métodos de API, webhooks y armado
  de las exclusiones del cupón (``_pagos360_get_coupon_exclusions``).
- ``payment.transaction``: armado del payload de la solicitud de pago, flujo de
  adhesión/tokenización y cobro hijo.
- ``payment.token``: datos de adhesión del token.

Otros elementos:

- Formulario del proveedor (pestaña *Fees and Due dates*) con los selectores de
  exclusión como etiquetas (``many2many_tags``).
- ``security/ir.model.access.csv``: accesos a los catálogos nuevos.
- ``data/pagos360_catalog_data.xml``: canales soportados y planes de cuotas
  habituales.
- ``migrations/19.0.2.1.0/post-migrate.py``: convierte la configuración previa
  (campos de texto con listas serializadas) a los nuevos Many2many.

Uso
===

#. Ir a *Contabilidad/Facturación → Configuración → Proveedores de pago* y abrir
   el proveedor Pagos360.
#. En *Credenciales*, cargar la API key (productiva o de prueba) y habilitar el
   proveedor.
#. En la pestaña *Fees and Due dates*, definir los días de validez. Usar
   *Fetch available methods* para traer de la API las cuotas y marcas que el
   comercio tiene habilitadas; recién entonces los selectores de exclusión ofrecen
   esas opciones (los canales son una lista fija).
#. Seleccionar, como etiquetas, los canales, cuotas y marcas de tarjeta a excluir
   del cupón.
#. Usar *Ensure Webhook* para registrar o verificar el webhook en Pagos360.

Arquitectura
============

El módulo extiende el framework de pagos de Odoo (``account_payment``). El
controlador (``controllers/main.py``) expone las URLs de retorno y webhook. Al
crear una transacción de cupón, ``payment.transaction`` arma el payload e
incorpora las exclusiones resueltas desde los Many2many del proveedor. Las marcas
de tarjeta se envían con el código numérico que espera Pagos360 (se guarda en el
catálogo ``pagos360.card.brand`` desde la API); las que aún no tienen código
—p. ej. migradas de la config vieja pero sin re-sincronizar— se omiten del payload.

Dependencias
============

- ``account_payment``

Autor
=====

Plugberry

Licencia
========

LGPL-3

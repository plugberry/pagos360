# Pagos360 Invoice Barcode

## Descripción

Este módulo permite agregar códigos de barras de Pagos360 (PagoFácil y RapiPago) directamente en las facturas de venta de Odoo. Cuando se emite una factura, el módulo genera en segundo plano una solicitud de pago en Pagos360 y obtiene los códigos de barras correspondientes que se imprimen en el reporte de factura. Confirmar la factura no espera a Pagos360.

## Características

- **Generación automática de códigos de barras**: Al validar una factura (a mano, por la confirmación automática, desde una suscripción o por cualquier otro camino), queda pendiente de código y una acción planificada pide los códigos a Pagos360. Si Pagos360 no responde, la factura se confirma igual y el pedido se reintenta cada hora, hasta 5 veces.
- **Envío e impresión con el código**: La factura se envía y se imprime solo cuando tiene su código de barras. Si al enviarla todavía no lo tiene, se pide en ese momento; si Pagos360 sigue sin responder, el envío directo se frena con un mensaje y el envío en cola espera hasta que llegue el código.
- **Aviso al responsable**: Si después de 5 intentos la factura sigue sin código, ya no se reintenta sola y se le crea una actividad al responsable de la factura (o a quien la creó). Enviarla o imprimirla vuelve a pedir el código.
- **Soporte para múltiples métodos de pago**: Incluye códigos de barras para PagoFácil y RapiPago.
- **Visualización en factura**: Los códigos de barras se muestran automáticamente en el reporte PDF de la factura.
- **Integración transparente**: Funciona sin intervención del usuario una vez configurado el proveedor de pago Pagos360.
- **Reconciliación automática**: Cuando se recibe un pago a través del código de barras, el sistema crea automáticamente la transacción y la vincula a la factura correspondiente.

## Requisitos

- Odoo 20.0
- Módulo `payment_pagos360` instalado y configurado
- Cuenta activa en Pagos360 con API Key configurada

## Instalación

1. Asegúrese de tener el módulo `payment_pagos360` instalado y configurado correctamente.
2. Instale este módulo desde la lista de aplicaciones de Odoo.

## Configuración

### Configurar Proveedor de Pago Pagos360

1. Vaya a **Contabilidad > Configuración > Proveedores de Pago**.
2. Seleccione o cree un proveedor **Pagos360**.
3. Configure los siguientes campos:
   - **Api Key**: Clave de API de producción de Pagos360
   - **Test Api Key**: Clave de API de prueba de Pagos360
   - **Activo** y **Producción**: active el proveedor; marque "Producción" para usar la API Key de producción, o déjelo sin marcar para usar la de prueba. Los códigos de barras se piden con el proveedor activo que tenga la clave de su modo, así que una base de prueba los genera igual que producción si el proveedor tiene su Test Api Key
   - **Medios de pago**: active el medio **PagoFacil** del proveedor, que es el que se usa para registrar los cobros con código de barras
   - **Días de validez**: Días hasta el vencimiento de la primera cuota (por defecto 15)

### Webhooks

Es importante configurar los webhooks en Pagos360 para recibir notificaciones de pagos:

1. En el formulario del proveedor de pago, haga clic en el botón **"Asegurar Webhook"**.
2. Esto configurará automáticamente el webhook en Pagos360 para su URL de Odoo.

## Uso

### Generación de Códigos de Barras

1. Cree una factura de cliente desde **Contabilidad > Clientes > Facturas**.
2. Complete la información de la factura (cliente, líneas de factura, etc.).
3. Haga clic en **Validar**.
4. La factura queda confirmada y pendiente de código. La acción planificada **Pagos360: request invoice barcodes**, que corre apenas se confirma y después cada hora:
   - Crea una solicitud de pago en Pagos360
   - Obtiene los códigos de barras
   - Los almacena en la factura

   Si Pagos360 no responde, la factura queda pendiente y se reintenta en la próxima corrida, sin frenar las demás. Después de 5 intentos se deja de reintentar y se avisa al responsable.

### Envío de la Factura

- **Envío desde la factura** (botón Enviar): si la factura todavía no tiene código, se pide en ese momento. Si Pagos360 no lo devuelve, el envío no se hace y se muestra un mensaje para probar de nuevo en unos minutos.
- **Envío masivo o automático**: las facturas sin código quedan en la cola de envío y salen cuando llega el código.
- **Imprimir o descargar**: igual que el envío desde la factura. Si no tiene código, se pide en ese momento, y si Pagos360 no lo devuelve, no se imprime.

### Visualización en Reporte

Cuando imprima la factura o la envíe por correo electrónico:

1. Vaya a la factura y haga clic en **Imprimir > Factura**.
2. En el PDF generado, al final de la factura verá:
   - Una sección titulada "Medio de pago electrónico"
   - El código de barras visual de PagoFácil/RapiPago
   - El número del código de barras en formato texto
   - Si corresponde, un código de barras adicional para RapiPago

### Proceso de Pago

1. El cliente recibe la factura con el código de barras.
2. El cliente puede pagar en cualquier sucursal de PagoFácil o RapiPago mostrando el código de barras.
3. Cuando Pagos360 recibe el pago, envía un webhook a Odoo.
4. El sistema automáticamente:
   - Crea una transacción de pago
   - La vincula a la factura correspondiente
   - Registra el pago en la factura

## Detalles Técnicos

### Modelos Extendidos

#### account.move

Campos agregados:
- `pagos360_barcode`: Código numérico de PagoFácil/RapiPago
- `pagos360_barcode_image`: Imagen SVG del código de barras
- `pagos360_rp_barcode`: Código numérico alternativo de RapiPago
- `pagos360_rp_barcode_image`: Imagen SVG del código de barras alternativo
- `pagos360_barcode_amount`: Monto de la solicitud de pago
- `pagos360_barcode_pending_since`: Desde cuándo la factura espera su código (vacío si no lo espera)
- `pagos360_barcode_alerted`: Si ya se le avisó al responsable que falta el código
- `pagos360_barcode_last_try`: Último pedido del código
- `pagos360_barcode_tries`: Pedidos que no devolvieron el código
- `pagos360_payment_request_id`: Id de la solicitud de pago en Pagos360; con él, un reintento consulta la solicitud en lugar de crear otra
- `pagos360_barcode_url`, `pagos360_rp_barcode_url`: Dónde Pagos360 sirve las imágenes de los códigos
- `pagos360_sending_data`: Datos de envío guardados mientras la factura espera su código

Métodos:
- `_payment_barcode_request_pagos360()`: Solicita códigos de barras a Pagos360
- `_pagos360_request_payment_request()`: Crea la solicitud de pago, o la consulta por su id si ya existe
- `_pagos360_download_barcode_images()`: Descarga las imágenes de los códigos que faltan
- `_create_pagos360_barcode()`: Marca como pendientes de código las facturas validadas y despierta la acción planificada
- `_post()`: Override que marca las facturas pendientes de código, sin llamar a Pagos360
- `_pagos360_request_pending_barcodes()`: Pide el código de las facturas pendientes; una falla deja la factura pendiente
- `_pagos360_alert_missing_barcode()`: Crea la actividad de aviso después de 5 intentos, una sola vez
- `_cron_pagos360_request_barcodes()`: Acción planificada que pide los códigos pendientes, de la más vieja a la más nueva

#### account.move.send

Métodos:
- `_generate_and_send_invoices()`: Override que pide el código faltante antes de enviar, frena el envío directo sin código y deja en la cola el envío masivo

#### ir.actions.report

Métodos:
- `_pre_render_qweb_pdf()`: Override que pide el código faltante antes de imprimir una factura y frena la impresión si no llega

#### payment.transaction

Métodos:
- `_pagos360_get_provider_invoice_from_reference()`: Extrae información de proveedor y factura desde una referencia
- `_pagos360_get_barcode_paid_amount()`: Obtiene el monto cobrado de la solicitud de pago
- `_search_by_reference()`: Override para buscar/crear transacciones desde pagos con código de barras

### Formato de Referencias

Las referencias de pago siguen el formato: `inv-{provider_id}-{invoice_id}`

Por ejemplo: `inv-42-1234`
- `inv`: Prefijo que indica que es un pago de factura
- `42`: ID del proveedor de pago
- `1234`: ID de la factura

### Flujo de Datos

1. **Creación de código de barras**:
   ```
   Factura validada → Pendiente de código → Acción planificada → Solicitud API a Pagos360 →
   Almacenamiento de códigos → Disponible en reporte y envío
   ```

2. **Recepción de pago**:
   ```
   Pago en PagoFácil/RapiPago → Webhook de Pagos360 → Búsqueda de transacción →
   Creación si no existe → Vinculación a factura → Registro de pago
   ```

### API de Pagos360 Utilizada

- `POST /payment-request`: Crea solicitud de pago y obtiene códigos de barras
- Webhooks recibidos: `paid` (pago confirmado)

## Solución de Problemas

### Los códigos de barras no se generan

**Causas posibles**:
- El proveedor de pago Pagos360 no está configurado correctamente, o no tiene la API Key de su modo (producción o prueba)
- La API Key es inválida o está deshabilitada
- La factura no tiene fecha de vencimiento configurada
- La factura ya tiene un código de barras generado
- Pagos360 no respondió todavía: la factura queda pendiente y se reintenta cada hora

**Solución**:
1. Verifique la configuración del proveedor en Contabilidad > Configuración > Proveedores de Pago
2. Asegúrese de que la API Key es válida
3. Configure una fecha de vencimiento en la factura
4. Revise los logs de Odoo para ver errores detallados
5. Verifique que la acción planificada **Pagos360: request invoice barcodes** esté activa

### Los pagos no se registran automáticamente

**Causas posibles**:
- Los webhooks no están configurados correctamente
- La URL de Odoo no es accesible desde Internet
- El webhook está bloqueado por firewall

**Solución**:
1. Use el botón "Asegurar Webhook" en el proveedor de pago
2. Verifique que su servidor Odoo sea accesible públicamente
3. Revise la configuración del firewall para permitir conexiones desde Pagos360

### Errores de timeout al generar códigos

**Causas posibles**:
- Problemas de conectividad con la API de Pagos360
- Timeout configurado muy bajo

**Solución**:
1. Verifique la conectividad a Internet del servidor
2. El timeout está configurado en 10 segundos para descargar imágenes, puede ajustarse si es necesario

## Seguridad

- Las API Keys se almacenan en campos protegidos solo accesibles por administradores del sistema
- Las imágenes de códigos de barras se almacenan como adjuntos en Odoo
- Las transacciones se crean con validaciones estrictas para evitar duplicados

## Mantenimiento

### Actualización de Versiones

El módulo es compatible con Odoo 20.0 y con la versión 20.0 del módulo `payment_pagos360`.

### Migración desde versiones anteriores

Si está migrando desde versiones anteriores de Odoo:
1. Actualice primero el módulo `payment_pagos360`
2. Actualice este módulo
3. Verifique que los webhooks estén correctamente configurados

## Soporte

Para soporte técnico relacionado con:
- **El módulo**: Contacte a Plugberry
- **La API de Pagos360**: Email: soporte@pagos360.com.ar, WhatsApp: +54 3512548747

## Licencia

LGPL-3

## Créditos

- **Autor**: Plugberry
- **Mantenedor**: Plugberry

## Changelog

### Versión 20.0.1.0.0
- Adaptación a Odoo 20.0
- El código de barras se pide por una acción planificada con reintento, y no al confirmar la factura
- La factura se envía solo con su código de barras
- La factura se imprime solo con su código de barras
- Aviso al responsable después de 5 intentos sin código
- El código se pide también para las facturas confirmadas sin el botón Validar

### Versión 19.0.1.0.0
- Adaptación a Odoo 19.0
- Actualización para compatibilidad con `payment_pagos360` v19.0
- Cambio de `_get_tx_from_notification_data` a `_search_by_reference` según nueva API de payment
- Mejoras en documentación y código
- Mejoras en traducciones

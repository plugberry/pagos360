# Pagos360 Invoice Barcode

## Descripción

Este módulo permite agregar códigos de barras de Pagos360 (PagoFácil y RapiPago) directamente en las facturas de venta de Odoo. Cuando se emite una factura, el módulo automáticamente genera una solicitud de pago en Pagos360 y obtiene los códigos de barras correspondientes que se imprimen en el reporte de factura.

## Características

- **Generación automática de códigos de barras**: Al validar una factura, se genera automáticamente una solicitud de pago en Pagos360 y se obtienen los códigos de barras.
- **Soporte para múltiples métodos de pago**: Incluye códigos de barras para PagoFácil y RapiPago.
- **Visualización en factura**: Los códigos de barras se muestran automáticamente en el reporte PDF de la factura.
- **Integración transparente**: Funciona sin intervención del usuario una vez configurado el proveedor de pago Pagos360.
- **Reconciliación automática**: Cuando se recibe un pago a través del código de barras, el sistema crea automáticamente la transacción y la vincula a la factura correspondiente.

## Requisitos

- Odoo 19.0
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
   - **Estado**: Seleccione "Habilitado" para producción o "Modo de prueba" para testing
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
4. El sistema automáticamente:
   - Crea una solicitud de pago en Pagos360
   - Obtiene los códigos de barras
   - Los almacena en la factura

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

Métodos:
- `_payment_barcode_request_pagos360()`: Solicita códigos de barras a Pagos360
- `_create_pagos360_barcode()`: Crea códigos de barras para facturas validadas
- `action_post()`: Override para generar códigos al validar

#### payment.transaction

Métodos:
- `_pagos360_get_provider_invoice_from_reference()`: Extrae información de proveedor y factura desde una referencia
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
   Factura validada → Solicitud API a Pagos360 → Almacenamiento de códigos → Disponible en reporte
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
- El proveedor de pago Pagos360 no está configurado correctamente
- La API Key es inválida o está deshabilitada
- La factura no tiene fecha de vencimiento configurada
- La factura ya tiene un código de barras generado

**Solución**:
1. Verifique la configuración del proveedor en Contabilidad > Configuración > Proveedores de Pago
2. Asegúrese de que la API Key es válida
3. Configure una fecha de vencimiento en la factura
4. Revise los logs de Odoo para ver errores detallados

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

El módulo es compatible con Odoo 19.0 y versiones futuras del módulo `payment_pagos360`.

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

### Versión 19.0.1.0.0
- Adaptación a Odoo 19.0
- Actualización para compatibilidad con `payment_pagos360` v19.0
- Cambio de `_get_tx_from_notification_data` a `_search_by_reference` según nueva API de payment
- Mejoras en documentación y código
- Mejoras en traducciones

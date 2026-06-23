# Payment Provider: Pagos360

Integración de [Pagos360](https://www.pagos360.com/) como proveedor de pago en Odoo 19.
Desarrollado por **Plugberry**, mantenido por **Adhoc**.

---

## Flujos de pago soportados

### 1. Botón de pago (checkout Pagos360)

Redirige al cliente al checkout de Pagos360 donde puede pagar con tarjeta, DEBIN, QR, etc.
Endpoint: `POST /payment-request`.

### 2. Cupones de efectivo
la
Genera un cupón de cobro para pago presencial. Endpoint: `POST /payment-request`.

| Método | Canal |
|---|---|
| **Pago Fácil** | Genera código de barras PagoFácil |
| **Rapipago** | Genera código de barras Rapipago |

La fecha de vencimiento del cupón se calcula como `hoy + pagos360_coupon_validity_days` (default: 15 días).

### 3. Débito automático en CBU (adhesión bancaria)

El cliente adhiere su CBU mediante un formulario externo de Pagos360. Una vez confirmada la adhesión, se genera un token en Odoo y los débitos futuros se ejecutan vía `POST /debit-request`.

**Fecha de débito** — calculada por `get_debit_due_date()` con dos modos:

| Toggle `Debitar al vencimiento` | Lógica |
|---|---|
| **Inactivo** | `next_business_day(hoy, days=pagos360_debit_execution_days)` |
| **Activo, con factura futura** | `next_business_day(invoice_due - 1 día, days=1)`, respetando el piso de 3 días hábiles |
| **Activo, sin factura elegible** | Fallback: `next_business_day(hoy, days=3)` |

El mínimo técnico de **3 días hábiles** (requerimiento de Pagos360) se valida mediante constraint y se consulta a la API `/validator/next-business-day`.

### 4. Débito automático en Tarjeta de Crédito (adhesión TC)

El cliente adhiere su tarjeta mediante el mismo formulario externo. Los débitos se ejecutan vía `POST /card-debit-request`.

La fecha de débito se calcula a partir del campo **Días de corte** (`pagos360_cut_days`): se elige el primer día de corte del mes >= día actual; si ninguno aplica, se usa el primer corte del mes siguiente. El resultado se guarda como fecha concreta (`date(year, month, cut_day)`).

---

## Configuración del proveedor

| Campo | Descripción | Default |
|---|---|---|
| `pagos360_api_key` | API Key productiva (solo administradores) | — |
| `pagos360_test_api_key` | API Key de pruebas (solo administradores) | — |
| `pagos360_form_url` | URL del formulario de adhesión de Pagos360 | — |
| `pagos360_coupon_validity_days` | Días de validez del cupón de efectivo | 15 |
| `pagos360_debit_execution_days` | Días hábiles para ejecutar el débito CBU (mín. 3) | 3 |
| `pagos360_cut_days` | Días de corte para débito TC, separados por coma | `"19"` |
| `pagos360_debit_use_invoice_due` | Debitar al vencimiento de la factura (CBU) | False |
| `pagos360_excluded_channels` | Canales excluidos del checkout (lista Python) | — |
| `pagos360_excluded_installments` | Cuotas excluidas (lista Python) | — |
| `pagos360_excluded_card_brands` | Marcas de tarjeta excluidas (lista Python) | — |

> Los grupos **Débito bancario (CBU)** y **Débito en tarjeta (TC)** solo se muestran cuando
> **Permitir guardar métodos de pago** está activo y se ha cargado la URL del formulario de adhesión.

### Webhook

El botón **Ensure Webhook** en el formulario del proveedor registra o actualiza automáticamente
el webhook en Pagos360 para recibir notificaciones de estado de pago/adhesión.

---

## Flujo de adhesión (tokenización)

Cuando el cliente selecciona "guardar método de pago" en el checkout:

1. La transacción se convierte automáticamente a tipo `validation` (monto $0).
2. El cliente completa el formulario externo de adhesión de Pagos360.
3. Pagos360 notifica via webhook → se crea el `payment.token` en Odoo con el tipo de adhesión (CBU o TC) y los datos bancarios/tarjeta.
4. Se genera una transacción hija (`online_token`) por el monto original almacenado en `pagos360_child_amount`.

---

## Modelo `payment.token`

Campos adicionales por adhesión:

| Campo | Descripción |
|---|---|
| `pagos360_adhesion_type` | `adhesion` (CBU) o `card_adhesion` (TC) |
| `pagos360_bank` / `pagos360_cbu_number` | Banco y últimos dígitos del CBU |
| `pagos360_card` / `pagos360_card_number` | Marca y últimos dígitos de la tarjeta |

Al archivar un token, se cancela automáticamente la adhesión en la API de Pagos360
(`PUT /adhesion/{id}/cancel` o `PUT /card-adhesion/{id}/cancel`).

---

## Modelo `payment.transaction`

Campos adicionales:

| Campo | Descripción |
|---|---|
| `pagos360_adhesion_type` | Tipo de adhesión (related desde token) |
| `pagos360_effective_payment_date` | Fecha efectiva usada al registrar el pago contable |
| `pagos360_debit_execution_date` | Fecha de débito al cliente (calculada al crear el débito) |
| `pagos360_child_amount` | Monto original preservado en el flujo de adhesión |

---

## Prevención de pagos duplicados

El método `pagos360_check_for_similar_transactions()` en `payment.token` consulta a la API
de Pagos360 los débitos creados en el último día para los tokens activos, detectando posibles
duplicados antes de generar un nuevo débito.

---

## Migración

| Versión | Descripción |
|---|---|
| `19.0.2.0.0` | Separa `validity_days` en campos por flujo (`pagos360_coupon_validity_days`, `pagos360_debit_execution_days`, `pagos360_cut_days`, `pagos360_debit_use_invoice_due`). Migra el valor previo de `validity_days` a `pagos360_coupon_validity_days`. Lee `ir.config_parameter pagos360.cut_day` y lo migra a `pagos360_cut_days`. |

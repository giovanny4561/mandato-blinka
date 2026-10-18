# Playbook JEV en Mandato

JEV (TypeSafe "System One") recibe un `state` y preguntas tipadas y devuelve cada respuesta con una
probabilidad calibrada. En Mandato es el **segundo filtro**: las reglas deterministas deciden y JEV
puede endurecer esa decisión.

## Qué decide JEV (6 preguntas, `app/decisor.py::PREGUNTAS`)

| Clave | Tipo | Juicio |
|---|---|---|
| `is_invoice` | noul | ¿Es una factura o solicitud de pago real (no recibo, cotización, publicidad)? |
| `already_paid` | noul | ¿Es un recibo, comprobante de pago o factura marcada como ya pagada o cancelada («PAGADO», «Paid in full», CFDI tipo P)? |
| `hidden_instructions` | noul | ¿Hay texto dirigido a un sistema automático o IA (aprobar, saltar controles, cambiar cuenta, pagar ya)? |
| `matches_agreement` | choice | `matches` · `differs_amount` · `differs_concept` · `no_agreement` · `other` |
| `payment_risk` | score 0–3 | 0 ninguno · 1 bajo · 2 alto (cuenta nueva, urgencia, dominio distinto) · 3 evidente (pide saltar controles) |
| `expense_category` | choice | `software` · `professional_services` · `contractor_payroll` · `logistics` · `marketing` · `other` |

`state` = texto de la factura + extracción validada + acuerdo del proveedor (si existe) + cuenta
del maestro enmascarada. Preguntas en inglés (rinde mejor), contenido en español.

## Qué va en código, no en JEV

Todo lo que se puede comparar con un `==` o un `>`: montos y topes (R06, R08), duplicados (R04, R05),
presupuesto (R07), fraccionamiento (R10), fechas (R11), cuenta vs maestro (R02), beneficiario
permitido (R01), moneda (R03), remitente (R13). JEV no ve la base de decisiones ni los límites;
solo juzga el documento y los datos maestros que le pasamos.

## Umbrales (`umbrales_jev` en `data/mandato.json`) y qué pasa en cada franja

| Regla | Lectura | Umbral | Franja | Acción |
|---|---|---|---|---|
| J01 | `hidden_instructions` | `instrucciones_ocultas_frenar` = 0.35 | ≥ 0.35 | **FRENAR** (persona verifica por canal independiente) |
| J02 | `is_invoice` | `no_es_factura_escalar` = 0.5 | < 0.5 | ESCALAR a persona |
| J02 | `already_paid` | `ya_pagado_escalar` = 0.5 | ≥ 0.5 | ESCALAR a persona (sin respuesta: no escala) |
| J03 | `payment_risk` | `riesgo_escalar` = 1.5 / `riesgo_frenar` = 2.5 | 1.5–2.5 → ESCALAR · ≥ 2.5 → **FRENAR** | persona decide / persona verifica |
| J04 | `matches_agreement` ≠ `matches` | `coincide_confianza_min` = 0.6 | confianza ≥ 0.6 | ESCALAR (alimenta R09) |

Debajo del umbral, las reglas de código deciden sin intervención. J01 tiene el umbral más bajo: un
falso positivo cuesta una revisión y un falso negativo cuesta un pago a un estafador.
`expense_category` alimenta el panel y los reportes; ninguna regla la usa.

## Regla de oro

**JEV puede endurecer una decisión; ninguna respuesta suya convierte un FRENAR en PAGAR.** El motor
consolida FRENAR > ESCALAR > PAGAR. Una probabilidad alta de `is_invoice` o `matches` deja todo igual:
el agente paga cuando las 18 reglas (código y JEV) devolvieron OK.

## Cómo se mide

- **Exactitud**: `scripts/evaluar.py` corre los casos de `data/casos.json` y compara con `esperado`;
  ningún caso `trampa=true` puede terminar en PAGAR.
- **Latencia**: `latencia_ms` por llamada (perf_counter alrededor del POST), visible en el panel.
- **Costo**: TypeSafe cobra 0.042 USD por millón de tokens de entrada, salida gratis
  (`costo_usd = input_tokens × 0.042 / 1e6`); el shim toma `usage.cost` de OpenRouter.
- `scripts/probar_jev.py` es el smoke con red: factura legítima vs. inyección.

## Cambiar del shim a JEV real

```
TYPESAFE_API_KEY=...                       # con JEV_BACKEND=auto basta esto
TYPESAFE_BASE_URL=https://api.typesafe.ai  # o https://ai-gateway.vercel.sh/typesafe
TYPESAFE_MODEL=jev-latest                  # o typesafe-ai/jev vía Vercel AI Gateway
```

Sin llave, `auto` usa `llm` (shim por OpenRouter con el mismo contrato). `JEV_BACKEND=typesafe`
sin llave o con TypeSafe caído **falla con RuntimeError**, no cae al shim: el panel debe mostrar
qué backend corrió de verdad (`RespuestasJEV.backend`).

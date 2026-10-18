# Entrega HackRadar (formulario "Mi proyecto")

**Nombre del proyecto:** Mandato

**Reto que resuelve:** Reto Blinka: Agentes que pagan por la empresa

**Repositorio público de GitHub:** https://github.com/giovanny4561/mandato-blinka

**Demo (URL):** solo en el formulario de HackRadar

**Video demo (URL, 2:25):** solo en el formulario de HackRadar; el archivo está en `app/static/mandato-demo.mp4`

## Descripción (pegar en el formulario)

Mandato es un agente de cuentas por pagar que lee facturas y decide PAGAR, ESCALAR o FRENAR dentro de un mandato que define la empresa: a quién se le paga, a qué cuenta, cuánto por pago y por mes, y contra qué acuerdo. Cuando la factura cabe en el mandato, el agente la paga sin pedir permiso. Cuando algo se sale, la frena o se la pasa a una persona y cita la regla que lo causó.

Cómo decide:
1. La factura llega por PDF, imagen, XML de la DIAN o CFDI, correo o chat. Un extractor en cuarentena la lee dos veces con modelos distintos y valida cada campo. Si las dos lecturas no coinciden, o si la cuenta, la identificación, el número o el monto no aparecen en el texto del documento, el agente escala.
2. Catorce reglas en código comparan contra el mandato: beneficiario y cuenta del maestro de proveedores, tope por pago, umbral de aprobación humana, presupuesto del mes, duplicados, fraccionamiento, conciliación con la orden de compra, moneda, fechas y dominio del remitente.
3. JEV real (TypeSafe, por Vercel AI Gateway) contesta preguntas tipadas con su probabilidad: si el documento trae instrucciones ocultas para el sistema, si es una factura, si ya está pagada, qué riesgo tiene el pago y si coincide con lo acordado.
4. El motor consolida FRENAR > ESCALAR > PAGAR. Una regla puede frenar o escalar; ninguna aprueba, y JEV solo endurece. El LLM extrae y las reglas deciden.
5. Cada decisión queda en un registro sellado, con la regla, la evidencia y un texto en español que sale de una plantilla. Cada fila guarda el hash de la anterior y la cabeza de la cadena va firmada con Ed25519 (/api/integridad): si alguien edita o borra una decisión, el panel dice en qué fila. Cada 10 minutos la cabeza se ancla en Bitcoin con OpenTimestamps (/api/anclajes), así que ni nosotros podemos reescribir la historia sin que se note. Una persona aprueba o rechaza lo escalado, y esa acción agrega una fila nueva.
6. Compatible con todos los rieles de Blinka: cada proveedor tiene su riel en el maestro (ACH, wire, SWIFT, SEPA, Bre-B, Pix, TED, SPEI, CBU/CVU, transferencias locales en 5 países, rutas en USD y USDC en 5 redes), validado con el dígito de control de su estándar, y factura en su moneda: los límites se miden en USD con la tasa de referencia del mandato. Si la decisión es PAGAR y el proveedor acepta USDC, Mandato arma la orden de pago en formato x402 v2 (Base Sepolia, autorización EIP-3009) y la deja sin firmar ni enviar.

Demo de punta a punta con 132 facturas (las 24 del set, 10 inyecciones y 98 de un mes de operación; 54 con trampa), corrida real del 17-oct-2026: 132/132 aciertos, 0 de 54 trampas pagadas, 0 legítimas frenadas, 70,5 % de autonomía, US$ 0,055 y 14 segundos para todo el set, con JEV real.

Resultados medidos sobre el set principal de 24 facturas simuladas (15 con trampa):
- Exactitud 24/24 (decisión y regla decisiva).
- 62,5 % de autonomía (15 de 24 decididas sin una persona).
- US$ 0,0116 el set completo con JEV real, US$ 0,0005 por factura, unos 2,2 s por factura.
- Arena de mutaciones: 1.056 ataques que violan el mandato, con extractor y JEV engañados: 0 terminaron en PAGAR.
- 237 pruebas automáticas, 94 % de cobertura, en SQLite y Postgres; GitHub Actions las corre en cada push.
- Set ciego de 12 facturas escrito por un agente aislado y congelado con hash: 12/12, 0 pagos indebidos, 0 legítimas frenadas.

Las trampas incluyen cambio de cuenta del proveedor, correo de suplantación, instrucciones ocultas, duplicados exactos y casi duplicados, factura partida para quedar bajo el umbral, monto o concepto distintos a la orden de compra, otra moneda, proveedor desconocido, tope y presupuesto superados, factura vencida e imagen ilegible.

Seguridad: "PAGAR implica las 18 reglas en orden" es una invariante. Los tests la prueban sobre los casos trampa y el panel la recalcula en cada carga. La demo está abierta, sin PIN, con un cupo diario de facturas por conexión; sin ese modo, cada acción que gasta créditos o cambia estado pide un PIN con rol. La API enmascara cuentas e identificaciones.

Stack: Python, FastAPI, Postgres (Railway), OpenRouter (Gemini 2.5 Flash/Flash-Lite y GPT-4.1 mini/nano) y JEV vía API de TypeSafe o Vercel AI Gateway. Licencia MIT; patrón de cuarentena adaptado de anthropics/financial-services (Apache-2.0) y formato del mandato inspirado en AP2.

Para probar: la portada muestra la red En vivo, las cifras y la integridad del registro; Decisiones tiene el registro completo y cada detalle muestra el recorrido de la factura paso a paso. Abre F16 (instrucciones ocultas) o F08 (cambio de cuenta) para ver por qué se frenaron. Para subir tus facturas usa el formulario de la portada, sin PIN; en Bandeja hay facturas de ejemplo para descargar. Arena muestra los 1.056 ataques y Mandato, las reglas y el riel de Blinka de cada proveedor.

Datos 100 % simulados. Mandato no mueve dinero, no abre cuentas y no decide cumplimiento ni fraude, tal como pide el reto.

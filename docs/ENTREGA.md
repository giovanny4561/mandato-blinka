# Entrega HackRadar (formulario "Mi proyecto")

**Nombre del proyecto:** Mandato

**Reto que resuelve:** Reto Blinka: Agentes que pagan por la empresa

**Repositorio público de GitHub:** https://github.com/giovanny4561/mandato-blinka

**Demo (URL):** solo en el formulario de HackRadar

**Video demo (URL, 2:40):** solo en el formulario de HackRadar; el archivo está en `app/static/mandato-demo.mp4`

## Descripción (pegar en el formulario)

Mandato es un agente de cuentas por pagar para empresas de EE. UU. con proveedores en Latinoamérica. Paga las facturas que caben en las reglas de la empresa, frena o pasa a una persona las demás, y explica cada decisión. En la demo decidió 132 facturas en 14 segundos y no pagó ninguna de las 54 trampas.

IMPACTO
Hoy alguien de finanzas lee cada factura, la compara con lo acordado, pide aprobación por chat y crea el pago a mano, decenas de veces al mes. El reto pide pasarle ese trabajo a un agente, y el obstáculo es la confianza: que pague solo lo que debe, explique por qué y nunca se pase del límite. Con Mandato la empresa fija las reglas una vez y una persona revisa solo las excepciones.

Resultados en la demo desplegada (132 facturas simuladas, 54 con trampa, JEV real, corrida del 17-oct-2026):
- 132 de 132 decisiones correctas.
- 0 de 54 trampas pagadas y 0 facturas legítimas frenadas.
- 70,5 % decididas sin una persona; el resto, frenadas o escaladas con la regla que lo causó.

CÓMO DECIDE (VIABILIDAD TÉCNICA)
1. Lee. La factura llega en PDF, imagen, XML de la DIAN o CFDI, correo o chat. Dos modelos distintos la leen en cuarentena; si no coinciden, o si la cuenta, el monto o el número no aparecen en el documento, escala.
2. Compara. Catorce reglas en código la cruzan con el mandato: beneficiario y cuenta del maestro, tope por pago, umbral de aprobación humana, presupuesto del mes, duplicados, fraccionamiento, orden de compra, moneda, fechas y remitente.
3. Pregunta a JEV. JEV (TypeSafe, vía Vercel AI Gateway) responde con probabilidades calibradas si el documento trae instrucciones ocultas, si es una factura, si ya está pagada y qué riesgo tiene. JEV solo puede endurecer la decisión.
4. Decide. Frenar gana a escalar y escalar gana a pagar. Ninguna regla aprueba: pagar exige los 18 controles en orden. El modelo extrae y el código decide.
5. Explica y registra. Cada decisión cita la regla y la evidencia en español. Lo dudoso va a una persona, ordenado por el riesgo que calcula JEV.

LO NUEVO (INNOVACIÓN)
- Un registro que nadie puede reescribir: cada fila guarda el hash de la anterior, la cabeza va firmada con Ed25519 (/api/integridad) y cada 10 minutos la anclamos en Bitcoin con OpenTimestamps (/api/anclajes). Si alguien borra o cambia una decisión, incluso nosotros, el panel dice en qué fila.
- JEV como segunda opinión: en 10 facturas con instrucciones escondidas, 6 engañaron al extractor, JEV marcó 8 y ninguna terminó pagada.
- Listo para pagos entre agentes: para quien cobra en USDC, Mandato arma la orden x402 v2 (Base Sepolia, EIP-3009) sin firmar, y un servidor MCP deja que otro agente lo use como herramienta.

CÓMO LO PROBAMOS
- Arena: 1.056 ataques que violan el mandato, con el extractor y JEV engañados a propósito. Ninguno terminó en pago.
- Set ciego: un agente aislado escribió 12 facturas que congelamos con hash antes de correrlas. 12 de 12, sin pagos indebidos.
- 238 pruebas automáticas (95 % de cobertura) en SQLite y Postgres, en cada push.
Las trampas cubren cambio de cuenta, correo de suplantación, instrucciones ocultas, duplicados, factura partida para esquivar el umbral, monto distinto a la orden de compra, otra moneda, proveedor desconocido, tope y presupuesto superados, factura vencida e imagen ilegible.

IMPLEMENTACIÓN Y COSTO
- US$ 0,055 las 132 facturas: US$ 0,0004 por factura y unos 2 segundos cada una.
- Rieles de Blinka: cada proveedor tiene el suyo en el maestro (ACH, wire, SWIFT, SEPA, Bre-B, Pix, TED, SPEI, CBU/CVU, transferencias locales en 5 países, rutas en USD y USDC en 5 redes), con la cuenta validada por el dígito de control de su estándar. Cada uno factura en su moneda y los límites se miden en USD con la tasa del mandato.
- Stack: Python, FastAPI, Postgres en Railway, OpenRouter (Gemini 2.5 Flash y Flash-Lite, GPT-4.1 mini y nano) y JEV.

QUÉ ES REAL Y QUÉ ES SIMULADO
Real: los modelos, JEV, el registro firmado, el anclaje en Bitcoin y el despliegue. Simulado: la empresa, los proveedores, las facturas y las órdenes de pago, que quedan sin firmar ni enviar. Mandato no mueve dinero, no abre cuentas y no decide cumplimiento ni fraude, como pide el reto.

SIGUIENTES PASOS
Conectar las órdenes aprobadas a la API de Blinka y correr Mandato en paralelo con el equipo de finanzas de un cliente, sin pagar, para medir aciertos con facturas reales antes de soltarle el primer dólar.

PRUÉBALO EN DOS MINUTOS
1. Abre la demo: la portada repite las 132 decisiones en la red En vivo.
2. Sube tu factura en la portada, sin PIN (30 por día por conexión). En Bandeja hay ejemplos para descargar.
3. Abre una frenada, como F16 (instrucciones ocultas) o F08 (cambio de cuenta), y mira el recorrido paso a paso.
4. En Arena ves los 1.056 ataques; en Mandato, las reglas y el riel de cada proveedor.

Todos los derechos reservados. Patrón de cuarentena adaptado de anthropics/financial-services (Apache-2.0); mandato inspirado en AP2.

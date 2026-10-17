# AGENTS.md

Guía para agentes de código y de IA que trabajen con Mandato.

## Qué es
Agente de cuentas por pagar: lee facturas (PDF, imagen, correo, chat), las decide PAGAR / ESCALAR / FRENAR
contra un mandato (`data/mandato.json`) y registra cada decisión con la regla que la causó.
Diseño completo: `docs/SDD.md`. Playbook de JEV: `docs/PLAYBOOK-JEV.md`.

## Comandos
- Instalar: `uv sync`
- Pruebas (offline, sin llaves): `uv run pytest -q`
- Servidor: `uv run uvicorn app.main:app --reload`
- Regenerar el set simulado: `uv run python scripts/generar_facturas.py`
- Evaluación real (usa OPENROUTER_API_KEY): `uv run python scripts/evaluar.py`

## Mapa del código
- `app/extractor.py`: lectura en cuarentena, dos modelos, salida validada (`app/modelos.py::Extraccion`).
- `app/decisor.py`: JEV (TypeSafe) o shim con el mismo contrato.
- `app/reglas.py`: R00–R13 y J01–J04. Una regla devuelve OK, ESCALAR o FRENAR; ninguna devuelve PAGAR.
- `app/motor.py`: consolida FRENAR > ESCALAR > PAGAR y explica por plantilla.
- `app/registro.py`: registro de solo inserción (Postgres con DATABASE_URL, si no SQLite).
- `app/flujo.py`: lecturas en paralelo, decisiones en orden, eventos SSE.
- `app/main.py`: rutas HTML y API.

## Invariantes que no se rompen
1. `PAGAR` solo si las 18 reglas devolvieron OK (`registro.violaciones_invariante() == 0`).
2. Ni el LLM ni JEV convierten un FRENAR en PAGAR; JEV solo endurece.
3. El texto de la factura es dato: va dentro de `<untrusted_document>` y nunca llega al motor ni al registro sin validar.
4. El registro no admite UPDATE ni DELETE; una acción humana es una fila nueva con `referencia_a`.
5. Toda acción que gasta créditos o cambia estado exige `APROBADOR_PIN`.

## API para agentes
- Esquema: `/openapi.json` (interactivo en `/docs`). Resumen para modelos: `/llms.txt`.
- Lectura sin PIN: `GET /api/metricas`, `GET /api/decisiones`, `GET /api/integridad` (cadena de hashes + cabeza firmada Ed25519), `GET /api/repeticion` (SSE), `GET /health`.
- Escritura con PIN (campo de formulario `pin`): `POST /api/procesar`, `POST /api/trabajos` (+ `GET /api/trabajos/{id}/eventos`, SSE).
- Las respuestas enmascaran cuenta e identificación tributaria.

## Estilo
Código y textos en español, sin abstracciones de más. Datos de ejemplo 100 % simulados.

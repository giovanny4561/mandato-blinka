"""Servidor MCP: las operaciones de /api como herramientas para agentes externos (Streamable HTTP en /mcp).

Lectura libre; procesar_factura exige un PIN con permiso de procesar, igual que POST /api/procesar. Las respuestas enmascaran
cuenta e identificación tributaria, y la factura pasa por la misma cuarentena que una subida por la web.
"""
import base64
import binascii
import io
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import UploadFile
from fastapi.concurrency import run_in_threadpool
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import Field

from app import mandato as mod_mandato
from app import registro, rieles, x402

servidor = FastMCP(
    "Mandato",
    instructions="Agente de cuentas por pagar del reto Blinka. Decide PAGAR, ESCALAR o FRENAR cada factura contra el "
                 "mandato de la empresa y cita la regla que causó la decisión. Ninguna regla aprueba sola: PAGAR exige "
                 "los 18 controles en OK. Las órdenes de pago son simuladas: nada mueve dinero. Datos de demostración.",
    stateless_http=True,  # cada llamada es independiente: sobrevive a reinicios y no guarda sesiones en memoria
    json_response=True,
    max_request_body_size=8 * 1024 * 1024,  # una factura de 5 MB en base64 ocupa ~6,7 MB
    # servidor público: la protección contra DNS rebinding es para servidores que escuchan en localhost
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)
LECTURA = ToolAnnotations(readOnlyHint=True)


@servidor.tool()
async def procesar_factura(
    nombre_archivo: Annotated[str, Field(description="Nombre del archivo con su extensión: pdf, png, jpg, jpeg, xml, eml o txt")],
    contenido_base64: Annotated[str, Field(description="Contenido del archivo en base64 (máximo 5 MB ya decodificado)")],
    pin: Annotated[str, Field(description="PIN de aprobador, operador o jurado (con cupo diario); sin un PIN válido no se procesa nada")],
) -> dict:
    """Sube una factura y la decide PAGAR, ESCALAR o FRENAR contra el mandato. Llama a los modelos (gasta créditos) y
    escribe una fila en el registro sellado, por eso exige un PIN (aprobador, operador o jurado con cupo diario). Devuelve la decisión, la regla decisiva,
    la explicación, el id, el sello y la extracción con cuenta e identificación tributaria enmascaradas."""
    from app import main  # aquí y no arriba: main importa este módulo para montar /mcp

    rol = main.exigir_pin(pin, "procesar")
    try:
        contenido = base64.b64decode(contenido_base64, validate=True)
    except binascii.Error:
        raise ValueError("contenido_base64 no es base64 válido")
    doc = await main.leer_subida(UploadFile(io.BytesIO(contenido), filename=nombre_archivo))
    main.gastar_cupo(rol, 1)
    return main.publico(registro.obtener(await run_in_threadpool(main.procesar, doc)))


@servidor.tool(annotations=LECTURA)
def consultar_decision(id: Annotated[int, Field(description="id de la decisión en el registro")]) -> dict:
    """Una decisión del registro: decisión, regla decisiva, explicación, los 18 controles, respuestas de JEV, sello y
    la extracción de la factura con cuenta e identificación tributaria enmascaradas."""
    from app import main

    d = registro.obtener(id)
    if d is None:
        raise ValueError(f"no existe la decisión {id}")
    return main.publico(d)


@servidor.tool(annotations=LECTURA)
def listar_decisiones(limite: Annotated[int, Field(ge=1, le=100, description="Cuántas decisiones traer (1 a 100)")] = 20) -> list[dict]:
    """Las últimas decisiones del registro, de la más reciente a la más antigua, con el mismo detalle que
    consultar_decision. Incluye las acciones humanas (aprobar o rechazar una escalada), que apuntan a la original."""
    from app import main

    return [main.publico(d) for d in registro.listar(limite)]


@servidor.tool(annotations=LECTURA)
def metricas() -> dict:
    """Totales por decisión (PAGAR, ESCALAR, FRENAR), autonomía, pendientes, pagos fuera de reglas (debe ser 0),
    costo y latencia."""
    return registro.metricas()


@servidor.tool(annotations=LECTURA)
def integridad() -> dict:
    """Verifica la cadena de hashes del registro (solo inserción) y devuelve su cabeza firmada con Ed25519: el mensaje
    firmado, la firma y la llave pública para comprobarla. Si el registro fue alterado, dice en qué fila y no firma."""
    from app import main

    return main.api_integridad()


@servidor.tool(annotations=LECTURA)
def orden_de_pago(id: Annotated[int, Field(description="id de una decisión PAGAR")]) -> dict:
    """Orden de pago SIMULADA de una decisión PAGAR por el riel de Blinka del proveedor (ACH, wire, SWIFT, SEPA, Bre-B,
    Pix, SPEI, CBU, USDC...), con el beneficiario del maestro, nunca el de la factura. Si el proveedor cobra en USDC
    en Base, trae además la orden x402 v2 sin firmar. No firma ni envía nada."""
    from app import main

    d = registro.obtener(id)
    if d is None:
        raise ValueError(f"no existe la decisión {id}")
    prov = main.proveedor_de(d)
    orden, orden_x402 = rieles.orden(d, prov), x402.orden(d, prov)
    if orden is None and orden_x402 is None:
        raise ValueError("esta decisión no tiene orden de pago: no es PAGAR o el proveedor no tiene riel en el mandato")
    return {"orden": orden, "x402": orden_x402}


@servidor.tool(annotations=LECTURA)
def mandato_resumen() -> dict:
    """El mandato vigente: empresa y moneda base, límites (tope por pago, umbral de aprobación humana, presupuesto),
    conciliación, tasas de referencia y los proveedores permitidos con su riel de pago (cuentas enmascaradas)."""
    m = mod_mandato.cargar()
    return m.model_dump(mode="json", include={"version", "empresa", "limites", "conciliacion", "tasas_referencia"}) | {
        "hash": mod_mandato.hash_de(m),
        "proveedores": [{"id": p.id, "nombre": p.nombre, "pais": p.pais, "moneda": p.moneda, "riel": rieles.etiqueta(p),
                         "cuenta": f"****{p.cuenta[-4:]}"} for p in m.proveedores_permitidos],
    }


@asynccontextmanager
async def sesiones():
    """Corre el session manager del SDK dentro del lifespan de la app."""
    # el SDK no deja correr dos veces un mismo session manager, y la app arranca de nuevo con cada TestClient
    servidor._session_manager = None
    servidor.streamable_http_app()
    async with servidor.session_manager.run():
        yield


class _Puerta:
    """ASGI de /mcp hacia el session manager vigente. Es una clase y no una función para que Starlette no la trate como vista."""

    async def __call__(self, scope, receive, send):
        await servidor.session_manager.handle_request(scope, receive, send)


puerta = _Puerta()

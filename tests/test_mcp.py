"""Servidor MCP: herramientas llamadas directo, por el cliente en memoria del SDK y por HTTP en /mcp."""
import base64
import json

import anyio
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from mcp.shared.memory import create_connected_server_and_client_session

from app import registro, servidor_mcp
from app import mandato as mod_mandato
from app.main import app
from tests.conftest import hacer_decision
from tests.test_api import CHAT, simulados  # noqa: F401  (fixture: extractor y JEV simulados)

HERRAMIENTAS = {"procesar_factura", "consultar_decision", "listar_decisiones", "metricas", "integridad", "orden_de_pago",
                "mandato_resumen"}


def test_herramientas_de_lectura():
    registro.insertar(hacer_decision())
    d = servidor_mcp.consultar_decision(1)
    assert d["decision"] == "PAGAR" and d["extraccion"]["cuenta_destino"] == "****0193"
    assert d["extraccion"]["proveedor_tax_id"] == "***7-8"
    assert [x["id"] for x in servidor_mcp.listar_decisiones(5)] == [1]
    assert servidor_mcp.metricas()["total"] == 1
    i = servidor_mcp.integridad()
    assert i["integro"] and i["firma"]["algoritmo"] == "Ed25519" and i["cabeza"]["hash"] == d["sello"]
    orden = servidor_mcp.orden_de_pago(1)
    assert orden["orden"]["riel"] == "SWIFT" and orden["orden"]["beneficiario"]["cuenta"].startswith("****")
    with pytest.raises(ValueError):
        servidor_mcp.consultar_decision(999)
    registro.insertar(hacer_decision(factura_hash="otra", decision="FRENAR", regla_decisiva="R02"))
    with pytest.raises(ValueError, match="no tiene orden"):
        servidor_mcp.orden_de_pago(2)


def test_mandato_resumen_sin_cuentas_completas():
    r = servidor_mcp.mandato_resumen()
    texto = json.dumps(r)
    m = mod_mandato.cargar()
    assert r["hash"] == mod_mandato.hash_de(m) and r["limites"]["tope_por_pago"]
    assert {p["riel"] for p in r["proveedores"]} >= {"SWIFT (USD)", "USDC en Base por x402 (simulado)"}
    for p in m.proveedores_permitidos:
        assert p.cuenta not in texto and p.tax_id not in texto
        assert not p.billetera_usdc or p.billetera_usdc not in texto


def test_procesar_factura_exige_pin_y_decide(simulados):  # noqa: F811
    b64 = base64.b64encode(CHAT).decode()
    with pytest.raises(HTTPException) as e:
        anyio.run(servidor_mcp.procesar_factura, "F04.txt", b64, "mal")
    assert e.value.status_code == 403 and registro.listar() == []
    with pytest.raises(ValueError, match="base64"):
        anyio.run(servidor_mcp.procesar_factura, "F04.txt", "no es base64!", "123456")
    with pytest.raises(HTTPException) as e:
        anyio.run(servidor_mcp.procesar_factura, "x.exe", base64.b64encode(b"MZ").decode(), "123456")
    assert e.value.status_code == 400
    d = anyio.run(servidor_mcp.procesar_factura, "F04_carla_rios.txt", b64, "123456")
    assert d["decision"] == "PAGAR" and d["id"] == 1 and d["sello"] and d["extraccion"]["cuenta_destino"] == "****7890"
    otra = anyio.run(servidor_mcp.procesar_factura, "F22.txt", b64, "123456")
    assert otra["decision"] == "FRENAR" and otra["regla_decisiva"] == "R04"


def test_cliente_en_memoria():
    async def sesion():
        async with create_connected_server_and_client_session(servidor_mcp.servidor) as c:
            nombres = {t.name for t in (await c.list_tools()).tools}
            mal = await c.call_tool("procesar_factura", {"nombre_archivo": "a.txt", "contenido_base64": "", "pin": "mal"})
            ok = await c.call_tool("metricas", {})
            return nombres, mal, ok
    nombres, mal, ok = anyio.run(sesion)
    assert nombres == HERRAMIENTAS
    assert mal.isError and "PIN incorrecto" in mal.content[0].text
    assert not ok.isError and json.loads(ok.content[0].text)["total"] == 0


def test_mcp_por_http():
    cabeceras = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    with TestClient(app) as c:
        r = c.post("/mcp", headers=cabeceras, json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "prueba", "version": "1"}}})
        assert r.status_code == 200 and r.json()["result"]["serverInfo"]["name"] == "Mandato"
        r = c.post("/mcp", headers=cabeceras, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        herramientas = {t["name"]: t for t in r.json()["result"]["tools"]}
        assert set(herramientas) == HERRAMIENTAS
        assert herramientas["metricas"]["annotations"]["readOnlyHint"] is True
        assert "readOnlyHint" not in (herramientas["procesar_factura"].get("annotations") or {})

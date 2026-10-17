"""Orden x402 simulada: solo para PAGAR en USD a proveedores con billetera USDC, sin firma."""
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app import mandato as mod_mandato
from app import registro, x402
from app.main import app
from tests.conftest import hacer_decision, hacer_extraccion


def proveedor(id_):
    return next(p for p in mod_mandato.cargar().proveedores_permitidos if p.id == id_)


def test_orden_para_un_pago_en_usdc():
    pixel = proveedor("PRV-002")
    d = hacer_decision(proveedor_id="PRV-002", extraccion=hacer_extraccion(monto_total=Decimal("1250.50")))
    d.id = registro.insertar(d)
    d = registro.obtener(d.id)
    o = x402.orden(d, pixel)
    assert o["x402Version"] == 2 and o["accepted"]["network"] == "eip155:84532"
    assert o["accepted"]["amount"] == o["payload"]["authorization"]["value"] == "1250500000"
    assert o["accepted"]["payTo"] == pixel.billetera_usdc and o["payload"]["signature"] is None
    assert o["extensions"]["mandato"]["simulada"] and o["extensions"]["mandato"]["sello"] == d.sello
    assert x402.riel(pixel) == "USDC por x402 (simulado)"


def test_sin_orden_si_no_es_pagar_o_no_acepta_usdc():
    nube = proveedor("PRV-001")
    assert x402.riel(nube) == "Transferencia a Bancolombia"
    assert x402.orden(hacer_decision(), nube) is None
    assert x402.orden(hacer_decision(decision="ESCALAR"), proveedor("PRV-002")) is None
    assert x402.orden(hacer_decision(extraccion=hacer_extraccion(moneda="COP")), proveedor("PRV-002")) is None


def test_billetera_mal_formada_invalida_el_mandato():
    datos = mod_mandato.cargar().model_dump()
    datos["proveedores_permitidos"][1]["billetera_usdc"] = "no-es-una-direccion"
    with pytest.raises(ValueError):
        mod_mandato.Mandato.model_validate(datos)


def test_api_y_detalle():
    pagada = registro.insertar(hacer_decision(proveedor_id="PRV-002"))
    otra = registro.insertar(hacer_decision(archivo="otra.pdf"))
    with TestClient(app) as c:
        assert c.get(f"/api/decisiones/{pagada}/x402").json()["accepted"]["scheme"] == "exact"
        assert c.get(f"/api/decisiones/{otra}/x402").status_code == 404
        assert c.get("/api/decisiones/9999/x402").status_code == 404
        assert "Orden de pago x402" in c.get(f"/decisiones/{pagada}").text
        assert "Orden de pago x402" not in c.get(f"/decisiones/{otra}").text

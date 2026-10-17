"""Replay offline del set de facturas (data/casos.json, producido por scripts/generar_facturas.py) en orden.

Invariante del reto: ningún caso trampa termina en PAGAR y ningún PAGAR tiene una regla distinta de OK.
"""
import json
from datetime import date

import pytest

from app import config, mandato, motor
from app.modelos import EntradaHistorial, Extraccion, RespuestasJEV

HOY = date(2026, 10, 17)
RUTA = config.RAIZ / "data" / "casos.json"


def test_casos_en_orden_dan_lo_esperado_y_respetan_la_invariante():
    if not RUTA.exists():
        pytest.skip("data/casos.json aún no existe (lo produce scripts/generar_facturas.py)")
    casos = json.loads(RUTA.read_text(encoding="utf-8"))
    m = mandato.cargar()
    historial: list[EntradaHistorial] = []
    fallos = []
    for i, caso in enumerate(casos):
        extraccion = Extraccion(**caso["extraccion_esperada"])
        jev = RespuestasJEV(**caso["jev_esperado"])
        decision, regla, reglas = motor.decidir_offline(
            extraccion, jev, m, historial, canal=caso["canal"], remitente=caso.get("remitente"),
            consistente=True, factura_hash=caso["archivo"], hoy=HOY,
        )
        if decision != caso["esperado"] or (caso["regla_esperada"] is not None and regla != caso["regla_esperada"]):
            fallos.append(f"{caso['archivo']}: dio {decision}/{regla}, esperado {caso['esperado']}/{caso['regla_esperada']}")
        assert not (caso.get("trampa") and decision == "PAGAR"), f"{caso['archivo']} es trampa y terminó en PAGAR"
        assert not (decision == "PAGAR" and any(r.veredicto != "OK" for r in reglas)), f"{caso['archivo']} PAGAR con regla fallida"
        prov = mandato.proveedor_por(extraccion, m)
        historial.append(EntradaHistorial(
            id=i + 1, ts="2026-10-17T00:00:00", proveedor_id=prov.id if prov else None,
            numero_factura=extraccion.numero_factura, monto_total=extraccion.monto_total, moneda=extraccion.moneda,
            fecha_emision=extraccion.fecha_emision, decision=decision, factura_hash=caso["archivo"], decidido_por="agente", referencia_acuerdo=extraccion.referencia_acuerdo,
        ))
    assert not fallos, "\n".join(fallos)

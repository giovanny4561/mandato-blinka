"""Orquesta una decisión: extracción → proveedor/acuerdo → JEV → reglas → consolidación → explicación por plantilla.

Nunca recibe texto libre del documento salvo para pasárselo a JEV dentro del `estado`.
La consolidación es la invariante de seguridad: PAGAR solo si las 18 reglas dieron OK.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from datetime import date

from app import config, rieles
from app.mandato import acuerdo_por, hash_de, proveedor_por
from app.modelos import (
    Acuerdo, Decision, Documento, EntradaHistorial, Extraccion, Mandato, Proveedor, RespuestasJEV, ResultadoRegla,
    Veredicto,
)
from app.reglas import evaluar, mascara, monto


def consolidar(reglas: list[ResultadoRegla]) -> tuple[Veredicto, str | None]:
    """FRENAR > ESCALAR > PAGAR. La regla decisiva es la primera, en orden, con el veredicto final."""
    for veredicto in ("FRENAR", "ESCALAR"):
        for r in reglas:
            if r.veredicto == veredicto:
                return veredicto, r.regla_id
    return "PAGAR", None


def explicar(decision: Veredicto, regla_decisiva: str | None, reglas: list[ResultadoRegla], extraccion: Extraccion,
             proveedor: Proveedor | None) -> str:
    if decision == "PAGAR":
        nombre = proveedor.nombre if proveedor else extraccion.proveedor_nombre
        cuenta = extraccion.cuenta_destino or (proveedor.cuenta if proveedor else None)
        return (f"PAGAR · Dentro del mandato: {nombre}, {extraccion.moneda} {monto(extraccion.monto_total)}, "
                f"cuenta {mascara(cuenta)}, {len(reglas)} controles OK.")
    r = next(x for x in reglas if x.regla_id == regla_decisiva)
    otros = ", ".join(x.regla_id for x in reglas if x.veredicto != "OK" and x.regla_id != regla_decisiva)
    cabeza = f"{decision} · {r.regla_id} {r.nombre}: {r.mensaje.rstrip('.')}."
    if decision == "ESCALAR":
        return cabeza + " Requiere aprobación de una persona." + (f" Otros controles con observación: {otros}." if otros else "")
    return cabeza + " El agente no paga esto sin intervención humana." + (f" Otros: {otros}." if otros else "")


def estado_para_jev(doc: Documento, extraccion: Extraccion, acuerdo: Acuerdo | None, proveedor: Proveedor | None,
                    hoy: date) -> dict:
    texto = doc.texto or "[imagen adjunta]"
    if extraccion.observaciones:
        texto += "\n" + extraccion.observaciones
    return {
        "document_text": texto,
        "extracted": extraccion.model_dump(mode="json"),
        "agreement": acuerdo.model_dump(mode="json") if acuerdo else None,
        "master_vendor": {"name": proveedor.nombre, "bank": proveedor.banco, "account_masked": mascara(proveedor.cuenta)}
        if proveedor else None,
        "today": hoy.isoformat(),
    }


def decidir(doc: Documento, mandato: Mandato, *, extraer: Callable[[Documento], tuple[Extraccion, bool, float]],
            preguntar: Callable[[dict], RespuestasJEV] | None, historial: list[EntradaHistorial],
            hoy: date | None = None) -> Decision:
    """Decide una factura. No la inserta en el registro: eso lo hace quien llama."""
    t0 = time.perf_counter()
    hoy = hoy or config.hoy()
    extraccion, consistente, costo_extraccion = extraer(doc)
    proveedor = proveedor_por(extraccion, mandato)
    acuerdo = acuerdo_por(extraccion.referencia_acuerdo, proveedor.id, mandato, hoy) if proveedor else None
    jev = preguntar(estado_para_jev(doc, extraccion, acuerdo, proveedor, hoy)) if preguntar else None
    reglas = evaluar(extraccion, mandato, historial, canal=doc.canal, remitente=doc.remitente, consistente=consistente,
                     hoy=hoy, jev=jev, factura_hash=doc.hash, texto=doc.texto)
    decision, decisiva = consolidar(reglas)
    fecha_pago = None
    if decision == "PAGAR":
        ven = extraccion.fecha_vencimiento
        fecha_pago = ven if ven and ven >= hoy else hoy
    return Decision(
        factura_hash=doc.hash, archivo=doc.nombre, canal=doc.canal, proveedor_id=proveedor.id if proveedor else None,
        extraccion=extraccion, consistente=consistente, reglas=reglas, jev=jev, decision=decision, regla_decisiva=decisiva,
        explicacion=explicar(decision, decisiva, reglas, extraccion, proveedor), fecha_pago_programada=fecha_pago,
        riel_sugerido=rieles.etiqueta(proveedor) if decision == "PAGAR" else None,
        mandato_hash=hash_de(mandato), costo_usd=costo_extraccion + (jev.costo_usd if jev else 0.0),
        latencia_ms=int((time.perf_counter() - t0) * 1000),
    )


def a_historial(decision: Decision, id: int | None = None) -> EntradaHistorial:
    """Para acumular historial sin SQLite (evaluador offline). El id lo pone quien llama."""
    e = decision.extraccion
    return EntradaHistorial(
        id=id if id is not None else (decision.id or 0), ts=decision.ts, proveedor_id=decision.proveedor_id,
        numero_factura=e.numero_factura, monto_total=e.monto_total, moneda=e.moneda, fecha_emision=e.fecha_emision,
        decision=decision.decision, factura_hash=decision.factura_hash, decidido_por=decision.decidido_por,
        referencia_a=decision.referencia_a, referencia_acuerdo=e.referencia_acuerdo,
    )


def decidir_offline(extraccion: Extraccion, jev: RespuestasJEV | None, mandato: Mandato,
                    historial: list[EntradaHistorial], *, canal: str, remitente: str | None, consistente: bool,
                    factura_hash: str, hoy: date) -> tuple[Veredicto, str | None, list[ResultadoRegla]]:
    """Reglas + consolidación sin extractor ni JEV reales: lo que usan los tests de casos."""
    reglas = evaluar(extraccion, mandato, historial, canal=canal, remitente=remitente, consistente=consistente, hoy=hoy,
                     jev=jev, factura_hash=factura_hash)
    decision, decisiva = consolidar(reglas)
    return decision, decisiva, reglas

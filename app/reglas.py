"""Reglas deterministas R00–R13 y juicios J01–J04 sobre JEV (SDD §5).

Ninguna regla devuelve PAGAR: solo OK, ESCALAR o FRENAR. PAGAR es lo que queda cuando
las 18 devuelven OK. La evidencia es JSON plano (floats, strings, fechas ISO) porque va al registro.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from app.mandato import acuerdo_por, alfanumerico, proveedor_por
from app.modelos import (
    Acuerdo, Conciliacion, EntradaHistorial, Extraccion, Limites, Mandato, Proveedor, RespuestasJEV,
    ResultadoRegla, UmbralesJEV,
)

NOMBRES = {
    "R00": "Legible y completa",
    "R01": "Beneficiario permitido",
    "R02": "Cuenta coincide con el maestro",
    "R03": "Moneda del acuerdo",
    "R04": "Duplicado exacto",
    "R05": "Casi duplicado",
    "R06": "Tope por pago",
    "R07": "Presupuesto del periodo",
    "R08": "Umbral de aprobación humana",
    "R09": "Conciliación con lo acordado",
    "R10": "Fraccionamiento",
    "R11": "Fechas",
    "R12": "Extracción consistente",
    "R13": "Remitente",
    "J01": "JEV: instrucciones ocultas",
    "J02": "JEV: no parece factura",
    "J03": "JEV: riesgo del pago",
    "J04": "JEV: coincide con lo acordado",
}
ORDEN = list(NOMBRES)


def monto(d: Decimal | float | int | None) -> str:
    """3200 -> '3.200,00', como lo lee finanzas en Latam."""
    return f"{Decimal(d or 0):,.2f}".translate(str.maketrans(",.", ".,"))


def mascara(cuenta: str | None) -> str:
    return "****" + alfanumerico(cuenta)[-4:]


def _plano(v):
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, list):
        return [_plano(x) for x in v]
    if isinstance(v, dict):
        return {k: _plano(x) for k, x in v.items()}
    return v


def _r(regla_id: str, veredicto: str = "OK", mensaje: str = "", **evidencia) -> ResultadoRegla:
    return ResultadoRegla(regla_id=regla_id, nombre=NOMBRES[regla_id], veredicto=veredicto,
                          evidencia=_plano(evidencia), mensaje=mensaje)


# ---------- R00–R13 ----------

def _r00(e: Extraccion) -> ResultadoRegla:
    if not e.legible:
        return _r("R00", "ESCALAR", "El documento no es legible o está vacío; una persona debe revisarlo", legible=False)
    faltan = [c for c in ("monto_total", "moneda", "fecha_emision", "numero_factura") if getattr(e, c) is None]
    if e.proveedor_tax_id is None and e.proveedor_nombre is None:
        faltan.append("proveedor")
    if faltan:
        return _r("R00", "ESCALAR", f"Faltan datos esenciales en la factura: {', '.join(faltan)}", faltan=faltan)
    return _r("R00")


def _r01(e: Extraccion, prov: Proveedor | None) -> ResultadoRegla:
    if prov:
        return _r("R01", proveedor_id=prov.id)
    return _r("R01", "FRENAR",
              f"'{e.proveedor_nombre or '?'}' (tax ID {e.proveedor_tax_id or '?'}) no está entre los beneficiarios permitidos del mandato",
              nombre=e.proveedor_nombre, tax_id=e.proveedor_tax_id)


def _r02(e: Extraccion, prov: Proveedor) -> ResultadoRegla:
    # shortcut: compara solo la cuenta, no el banco (nombre libre del extractor); ampliar si el maestro guarda códigos de banco
    if e.cuenta_destino is None:
        return _r("R02", sin_cuenta_en_factura=True)
    if alfanumerico(e.cuenta_destino) == alfanumerico(prov.cuenta):
        return _r("R02", cuenta=mascara(prov.cuenta))
    return _r("R02", "FRENAR",
              f"La cuenta de la factura ({mascara(e.cuenta_destino)}) no coincide con la del maestro "
              f"({mascara(prov.cuenta)}, {prov.banco}). Posible suplantación de proveedor; verificar por canal independiente",
              cuenta_factura=mascara(e.cuenta_destino), cuenta_maestro=mascara(prov.cuenta))


def _r03(e: Extraccion, prov: Proveedor) -> ResultadoRegla:
    if e.moneda == prov.moneda:
        return _r("R03", moneda=e.moneda)
    return _r("R03", "ESCALAR", f"La factura está en {e.moneda} y el acuerdo con {prov.nombre} es en {prov.moneda}",
              moneda_factura=e.moneda, moneda_acuerdo=prov.moneda)


def _r04(e: Extraccion, prov: Proveedor | None, historial: list[EntradaHistorial], factura_hash: str) -> ResultadoRegla:
    num = alfanumerico(e.numero_factura)
    for h in historial:
        if h.factura_hash == factura_hash:
            return _r("R04", "FRENAR", f"Este mismo archivo ya fue procesado en la decisión #{h.id} ({h.decision})",
                      decision_previa=h.id, por="hash")
        if prov and num and h.proveedor_id == prov.id and alfanumerico(h.numero_factura) == num:
            return _r("R04", "FRENAR",
                      f"La factura {e.numero_factura} de {prov.nombre} ya fue procesada en la decisión #{h.id} ({h.decision})",
                      decision_previa=h.id, por="numero_factura")
    return _r("R04")


def _r05(e: Extraccion, prov: Proveedor, historial: list[EntradaHistorial], ventana: int) -> ResultadoRegla:
    num = alfanumerico(e.numero_factura)
    for h in historial:
        if not (h.proveedor_id == prov.id and h.monto_total == e.monto_total and h.moneda == e.moneda):
            continue
        if num and alfanumerico(h.numero_factura) == num:
            continue  # mismo número: eso es R04
        if h.fecha_emision and e.fecha_emision and abs((e.fecha_emision - h.fecha_emision).days) <= ventana:
            dias = abs((e.fecha_emision - h.fecha_emision).days)
            return _r("R05", "ESCALAR",
                      f"La decisión #{h.id} (factura {h.numero_factura or '?'} del {h.fecha_emision}) tiene el mismo monto "
                      f"{e.moneda} {monto(e.monto_total)} con otro número, {dias} días de diferencia",
                      decision_previa=h.id, numero_previo=h.numero_factura, dias=dias)
    return _r("R05")


def _r06(e: Extraccion, lim: Limites) -> ResultadoRegla:
    if e.monto_total <= lim.tope_por_pago:
        return _r("R06", monto=e.monto_total, tope=lim.tope_por_pago)
    return _r("R06", "FRENAR", f"{e.moneda} {monto(e.monto_total)} supera el tope por pago de {e.moneda} {monto(lim.tope_por_pago)}",
              monto=e.monto_total, tope=lim.tope_por_pago)


def _r07(e: Extraccion, lim: Limites, historial: list[EntradaHistorial], hoy: date) -> ResultadoRegla:
    # El presupuesto se consume por mes de PAGO (ts del registro), no por fecha de emisión de la factura.
    mes = hoy.isoformat()[:7]
    consumido = sum((h.monto_total for h in historial
                     if h.decision == "PAGAR" and h.monto_total and h.moneda == e.moneda and h.ts[:7] == mes), Decimal(0))
    presupuesto = lim.presupuesto_periodo.monto
    ev = dict(consumido=consumido, monto=e.monto_total, presupuesto=presupuesto, disponible=presupuesto - consumido)
    if consumido + e.monto_total <= presupuesto:
        return _r("R07", **ev)
    return _r("R07", "ESCALAR",
              f"Presupuesto mensual de {e.moneda} {monto(presupuesto)}: ya consumido {monto(consumido)}, "
              f"disponible {monto(presupuesto - consumido)}, y esta factura es de {monto(e.monto_total)}", **ev)


def _r08(e: Extraccion, lim: Limites) -> ResultadoRegla:
    u = lim.umbral_aprobacion_humana
    if e.monto_total <= u:
        return _r("R08", monto=e.monto_total, umbral=u)
    return _r("R08", "ESCALAR", f"{e.moneda} {monto(e.monto_total)} supera el umbral de aprobación humana de {e.moneda} {monto(u)}",
              monto=e.monto_total, umbral=u)


def _r09(e: Extraccion, c: Conciliacion, prov: Proveedor, acuerdo: Acuerdo | None,
         historial: list[EntradaHistorial] = ()) -> ResultadoRegla:
    if not e.referencia_acuerdo:
        if e.monto_total >= c.exigir_acuerdo_desde:
            return _r("R09", "ESCALAR",
                      f"{e.moneda} {monto(e.monto_total)} alcanza el monto desde el que se exige acuerdo "
                      f"({monto(c.exigir_acuerdo_desde)}) y la factura no referencia ninguno",
                      exigir_acuerdo_desde=c.exigir_acuerdo_desde)
        return _r("R09", sin_acuerdo=True)
    if acuerdo is None:
        return _r("R09", "ESCALAR",
                  f"Acuerdo no encontrado: la factura cita {e.referencia_acuerdo}, que no existe, no está vigente o no es de {prov.nombre}",
                  referencia=e.referencia_acuerdo)
    diferencia = abs(e.monto_total - acuerdo.monto)
    tolerancia = max(c.tolerancia_abs, acuerdo.monto * c.tolerancia_pct / 100)
    pct = float(round(diferencia / acuerdo.monto * 100, 1)) if acuerdo.monto else None
    ev = dict(acuerdo=acuerdo.id, acordado=acuerdo.monto, facturado=e.monto_total, diferencia=diferencia,
              diferencia_pct=pct, tolerancia=tolerancia)
    if diferencia > tolerancia:
        return _r("R09", "ESCALAR",
                  f"El acuerdo {acuerdo.id} es por {acuerdo.moneda} {monto(acuerdo.monto)} y la factura por "
                  f"{e.moneda} {monto(e.monto_total)} ({pct} % de diferencia; tolerancia {monto(tolerancia)})", **ev)
    # Lo ya pagado contra el mismo acuerdo también cuenta: una OC no se cobra dos veces.
    ya = sum((h.monto_total for h in historial
              if h.decision == "PAGAR" and h.proveedor_id == prov.id and h.monto_total
              and (h.referencia_acuerdo or "").upper() == acuerdo.id.upper()), Decimal(0))
    if ya and ya + e.monto_total > acuerdo.monto + tolerancia:
        return _r("R09", "ESCALAR",
                  f"Contra el acuerdo {acuerdo.id} ({acuerdo.moneda} {monto(acuerdo.monto)}) ya se pagaron "
                  f"{monto(ya)}; con esta factura ({monto(e.monto_total)}) se superaría lo acordado", ya_pagado=ya, **ev)
    return _r("R09", ya_pagado=ya, **ev)


def _r10(e: Extraccion, lim: Limites, prov: Proveedor, historial: list[EntradaHistorial], hoy: date) -> ResultadoRegla:
    u = lim.umbral_aprobacion_humana
    if e.monto_total > u:
        return _r("R10", omitida="R08 ya escala")
    fecha = e.fecha_emision or hoy
    # Solo lo ya PAGADO cuenta: lo escalado lo ve una persona y lo frenado nunca salió.
    previas = [h for h in historial
               if h.proveedor_id == prov.id and h.decision == "PAGAR" and h.moneda == e.moneda
               and h.monto_total is not None and h.monto_total <= u
               and h.fecha_emision and abs((h.fecha_emision - fecha).days) <= lim.ventana_fraccionamiento_dias]  # ventana simétrica: el orden de llegada no importa
    suma = sum((h.monto_total for h in previas), e.monto_total)
    ev = dict(facturas=[dict(id=h.id, numero=h.numero_factura, monto=h.monto_total, fecha=h.fecha_emision) for h in previas],
              suma=suma, umbral=u)
    if previas and suma > u:
        return _r("R10", "ESCALAR",
                  f"Con esta factura, {prov.nombre} suma {e.moneda} {monto(suma)} en {len(previas) + 1} facturas pagadas en "
                  f"{lim.ventana_fraccionamiento_dias} días, por encima del umbral de {monto(u)} sin que ninguna lo supere por sí sola",
                  **ev)
    return _r("R10", **ev)


def _r11(e: Extraccion, hoy: date) -> ResultadoRegla:
    em, ven = e.fecha_emision, e.fecha_vencimiento
    ev = dict(emision=em, vencimiento=ven, hoy=hoy)
    if em and ven and em > ven:
        return _r("R11", "ESCALAR", f"La fecha de emisión ({em}) es posterior al vencimiento ({ven})", **ev)
    if ven and ven < hoy - timedelta(days=60):
        return _r("R11", "ESCALAR", f"La factura venció el {ven}, hace {(hoy - ven).days} días", **ev)
    if em and em > hoy:
        return _r("R11", "ESCALAR", f"La fecha de emisión ({em}) está en el futuro", **ev)
    return _r("R11", **ev)


def _r12(consistente: bool) -> ResultadoRegla:
    if consistente:
        return _r("R12", consistente=True)
    return _r("R12", "ESCALAR", "Las dos pasadas de extracción (modelos A y B) no coinciden en proveedor, monto o cuenta",
              consistente=False)


def _r13(canal: str, remitente: str | None, prov: Proveedor) -> ResultadoRegla:
    if canal != "correo":
        return _r("R13", omitida="no es correo")
    if not prov.dominio_correo:
        return _r("R13", omitida="sin dominio en maestro")
    dominio = (remitente or "").rsplit("@", 1)[-1].strip(" >").lower()
    if dominio == prov.dominio_correo.lower():
        return _r("R13", dominio=dominio)
    return _r("R13", "ESCALAR", f"El correo viene de '{dominio or '?'}' y {prov.nombre} factura desde '{prov.dominio_correo}'",
              dominio_remitente=dominio, dominio_maestro=prov.dominio_correo)


# ---------- J01–J04 (JEV solo endurece) ----------

def _respuesta(jev: RespuestasJEV | None, clave: str):
    return jev.respuestas.get(clave) if jev else None


def _j01(jev, u: UmbralesJEV) -> ResultadoRegla:
    r = _respuesta(jev, "hidden_instructions")
    if r is None or r.probabilidad is None:
        return _r("J01", sin_jev=True)
    ev = dict(probabilidad=r.probabilidad, umbral=u.instrucciones_ocultas_frenar)
    if r.probabilidad >= u.instrucciones_ocultas_frenar:
        return _r("J01", "FRENAR",
                  f"Probabilidad {r.probabilidad:.0%} de que el documento traiga instrucciones dirigidas a un sistema automático "
                  f"(se frena desde {u.instrucciones_ocultas_frenar:.0%})", **ev)
    return _r("J01", **ev)


def _j02(jev, u: UmbralesJEV) -> ResultadoRegla:
    r = _respuesta(jev, "is_invoice")
    if r is None or r.probabilidad is None:
        return _r("J02", sin_jev=True)
    ev = dict(probabilidad=r.probabilidad, umbral=u.no_es_factura_escalar)
    if r.probabilidad < u.no_es_factura_escalar:
        return _r("J02", "ESCALAR",
                  f"Solo {r.probabilidad:.0%} de probabilidad de que sea una factura o cuenta de cobro (mínimo {u.no_es_factura_escalar:.0%})",
                  **ev)
    return _r("J02", **ev)


def _j03(jev, u: UmbralesJEV) -> ResultadoRegla:
    r = _respuesta(jev, "payment_risk")
    if r is None or r.valor is None:
        return _r("J03", sin_jev=True)
    riesgo = float(r.valor)
    ev = dict(riesgo=riesgo, escalar_desde=u.riesgo_escalar, frenar_desde=u.riesgo_frenar)
    msg = f"Riesgo del pago {riesgo:g} de 3 según JEV (escala desde {u.riesgo_escalar:g}, frena desde {u.riesgo_frenar:g})"
    if riesgo >= u.riesgo_frenar:
        return _r("J03", "FRENAR", msg, **ev)
    if riesgo >= u.riesgo_escalar:
        return _r("J03", "ESCALAR", msg, **ev)
    return _r("J03", **ev)


def _j04(jev, u: UmbralesJEV, e: Extraccion) -> ResultadoRegla:
    if not e.referencia_acuerdo:
        return _r("J04", omitida="sin acuerdo")
    r = _respuesta(jev, "matches_agreement")
    if r is None:
        return _r("J04", sin_jev=True)
    confianza = r.confianza or 0.0
    ev = dict(valor=r.valor, confianza=confianza, confianza_min=u.coincide_confianza_min)
    if r.valor not in ("matches", "no_agreement") and confianza >= u.coincide_confianza_min:
        return _r("J04", "ESCALAR",
                  f"JEV juzga que la factura no coincide con el acuerdo {e.referencia_acuerdo} ('{r.valor}', confianza {confianza:.0%})",
                  **ev)
    return _r("J04", **ev)


# ---------- Orquestación ----------

def evaluar(extraccion: Extraccion, mandato: Mandato, historial: list[EntradaHistorial], *, canal: str,
            remitente: str | None, consistente: bool, hoy: date, jev: RespuestasJEV | None,
            factura_hash: str) -> list[ResultadoRegla]:
    """Siempre las 18 reglas, en el orden de `ORDEN`. Las que no pueden juzgar devuelven OK con `omitida`."""
    e, m, lim = extraccion, mandato, mandato.limites
    r00 = _r00(e)
    prov = proveedor_por(e, m) if r00.veredicto == "OK" else None
    acuerdo = acuerdo_por(e.referencia_acuerdo, prov.id, m, hoy) if prov else None
    if e.monto_total is None:
        sin_monto = "sin monto"
    elif e.moneda != m.empresa.moneda_base:
        sin_monto = "moneda distinta"
    else:
        sin_monto = None

    def con_prov(rid, f):
        return f() if prov else _r(rid, omitida="sin proveedor")

    def con_monto(rid, f):
        return f() if sin_monto is None else _r(rid, omitida=sin_monto)

    return [
        r00,
        _r01(e, prov) if r00.veredicto == "OK" else _r("R01", omitida="sin datos"),
        con_prov("R02", lambda: _r02(e, prov)),
        con_prov("R03", lambda: _r03(e, prov)),
        _r04(e, prov, historial, factura_hash),
        con_prov("R05", lambda: _r05(e, prov, historial, lim.ventana_casi_duplicado_dias)),
        con_monto("R06", lambda: _r06(e, lim)),
        con_prov("R07", lambda: con_monto("R07", lambda: _r07(e, lim, historial, hoy))),
        con_monto("R08", lambda: _r08(e, lim)),
        con_prov("R09", lambda: _r09(e, m.conciliacion, prov, acuerdo, historial)),
        con_prov("R10", lambda: con_monto("R10", lambda: _r10(e, lim, prov, historial, hoy))),
        _r11(e, hoy),
        _r12(consistente),
        con_prov("R13", lambda: _r13(canal, remitente, prov)),
        _j01(jev, m.umbrales_jev),
        _j02(jev, m.umbrales_jev),
        _j03(jev, m.umbrales_jev),
        _j04(jev, m.umbrales_jev, e),
    ]

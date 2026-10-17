"""Una prueba (OK y fallo) por regla R00–R13 y J01–J04. Todo offline, hoy = 2026-10-17."""
import json
from datetime import date
from decimal import Decimal

import pytest

from app import mandato, reglas
from app.modelos import EntradaHistorial, RespuestaJEV, RespuestasJEV
from tests.conftest import hacer_extraccion

HOY = date(2026, 10, 17)
ORDEN = [f"R{i:02d}" for i in range(14)] + ["J01", "J02", "J03", "J04"]
DEPENDEN_DEL_PROVEEDOR = ["R02", "R03", "R05", "R07", "R09", "R10", "R13"]
DE_MONTO = ["R06", "R07", "R08", "R10"]


@pytest.fixture
def m():
    return mandato.cargar()


def entrada(id_, proveedor_id="PRV-001", numero="NA-2026-0900", monto="3200", fecha=date(2026, 10, 1),
            decision="PAGAR", moneda="USD", hash_=None):
    return EntradaHistorial(id=id_, ts="2026-10-17T00:00:00", proveedor_id=proveedor_id, numero_factura=numero,
                            monto_total=Decimal(monto), moneda=moneda, fecha_emision=fecha, decision=decision,
                            factura_hash=hash_ or f"hash-{id_}", decidido_por="agente")


def jev(ocultas=0.02, factura=0.97, riesgo=0.3, acuerdo="matches", confianza=0.9):
    return RespuestasJEV(backend="llm", modelo="simulado", respuestas={
        "is_invoice": RespuestaJEV(tipo="noul", probabilidad=factura),
        "hidden_instructions": RespuestaJEV(tipo="noul", probabilidad=ocultas),
        "matches_agreement": RespuestaJEV(tipo="choice", valor=acuerdo, confianza=confianza),
        "payment_risk": RespuestaJEV(tipo="score", valor=riesgo, confianza=0.9),
    })


def ev(m, e=None, historial=(), *, canal="pdf", remitente=None, consistente=True, jev_="defecto", hash_="hash-actual"):
    res = reglas.evaluar(e or hacer_extraccion(), m, list(historial), canal=canal, remitente=remitente,
                         consistente=consistente, hoy=HOY, jev=jev() if jev_ == "defecto" else jev_,
                         factura_hash=hash_)
    assert [r.regla_id for r in res] == ORDEN
    json.dumps([r.model_dump() for r in res])  # la evidencia debe poder guardarse en el registro
    return {r.regla_id: r for r in res}


def test_factura_normal_pasa_los_18_controles(m):
    assert all(r.veredicto == "OK" for r in ev(m).values())


def test_r00_ilegible_o_incompleta_escala_y_omite_lo_que_depende_del_proveedor(m):
    assert ev(m, hacer_extraccion(legible=False))["R00"].veredicto == "ESCALAR"
    r = ev(m, hacer_extraccion(monto_total=None))
    assert r["R00"].veredicto == "ESCALAR" and "monto_total" in r["R00"].mensaje
    r = ev(m, hacer_extraccion(proveedor_nombre=None, proveedor_tax_id=None))
    assert r["R00"].veredicto == "ESCALAR" and r["R01"].veredicto == "OK"
    assert all(r[k].veredicto == "OK" and r[k].evidencia == {"omitida": "sin proveedor"} for k in DEPENDEN_DEL_PROVEEDOR)


def test_r01_proveedor_desconocido_frena_y_omite_dependientes(m):
    r = ev(m, hacer_extraccion(proveedor_nombre="Servicios Globales Omega SAS", proveedor_tax_id="900111222-3"))
    assert r["R01"].veredicto == "FRENAR" and "Servicios Globales Omega" in r["R01"].mensaje
    assert all(r[k].veredicto == "OK" and r[k].evidencia == {"omitida": "sin proveedor"} for k in DEPENDEN_DEL_PROVEEDOR)
    assert ev(m, hacer_extraccion(proveedor_tax_id=None, proveedor_nombre="nube andina s.a.s."))["R01"].veredicto == "OK"


def test_r02_cuenta_distinta_frena_con_cuentas_enmascaradas(m):
    r = ev(m, hacer_extraccion(cuenta_destino="03100554821"))["R02"]
    assert r.veredicto == "FRENAR" and "****4821" in r.mensaje and "****0193" in r.mensaje
    assert "Posible suplantación de proveedor; verificar por canal independiente" in r.mensaje
    assert ev(m, hacer_extraccion(cuenta_destino=None))["R02"].evidencia == {"sin_cuenta_en_factura": True}
    assert ev(m)["R02"].veredicto == "OK"


def test_r03_moneda_distinta_escala_y_omite_reglas_de_monto(m):
    r = ev(m, hacer_extraccion(moneda="COP", monto_total=Decimal("12800000"), referencia_acuerdo=None))
    assert r["R03"].veredicto == "ESCALAR" and "COP" in r["R03"].mensaje
    assert all(r[k].veredicto == "OK" and r[k].evidencia == {"omitida": "moneda distinta"} for k in DE_MONTO)


def test_r04_duplicado_por_numero_o_por_hash_frena(m):
    r = ev(m, historial=[entrada(1, numero="NA-2026-0917", decision="ESCALAR")])["R04"]
    assert r.veredicto == "FRENAR" and "#1" in r.mensaje
    assert ev(m, historial=[entrada(2, numero="NA-2026-0001", hash_="hash-actual")])["R04"].veredicto == "FRENAR"
    otros = [entrada(3, numero="NA-2026-0001"), entrada(4, proveedor_id="PRV-002", numero="NA-2026-0917")]
    assert ev(m, historial=otros)["R04"].veredicto == "OK"


def test_r05_mismo_monto_otro_numero_dentro_de_la_ventana_escala(m):
    e = hacer_extraccion(numero_factura="NA-2026-0921", fecha_emision=date(2026, 10, 4))
    r = ev(m, e, [entrada(1, numero="NA-2026-0917", fecha=date(2026, 10, 1))])["R05"]
    assert r.veredicto == "ESCALAR" and r.evidencia["dias"] == 3
    assert ev(m, e, [entrada(1, numero="NA-2026-0917", fecha=date(2026, 9, 1))])["R05"].veredicto == "OK"
    assert ev(m, e, [entrada(1, numero="NA-2026-0917", monto="3100")])["R05"].veredicto == "OK"
    assert ev(m, e, [entrada(1, numero="NA-2026-0921")])["R05"].veredicto == "OK"  # mismo número: eso es R04


def test_r06_tope_por_pago_frena(m):
    r = ev(m, hacer_extraccion(monto_total=Decimal("18900"), referencia_acuerdo=None))["R06"]
    assert r.veredicto == "FRENAR" and "18.900,00" in r.mensaje and "10.000,00" in r.mensaje
    assert ev(m, hacer_extraccion(monto_total=Decimal("10000"), referencia_acuerdo=None))["R06"].veredicto == "OK"


def test_r07_presupuesto_del_mes_suma_solo_lo_pagado(m):
    e = hacer_extraccion(monto_total=Decimal("4900"), proveedor_tax_id="76543210-9", proveedor_nombre="Logística Sur SpA",
                         cuenta_destino="00123456789", referencia_acuerdo="OC-2026-027")
    pagadas = [entrada(1, monto="10350"), entrada(2, monto="2950", proveedor_id="PRV-002", numero="PF-1201")]
    r = ev(m, e, pagadas)["R07"]
    assert r.veredicto == "ESCALAR" and r.evidencia["consumido"] == 13300 and r.evidencia["disponible"] == 4700
    assert "13.300,00" in r.mensaje
    no_cuentan = [entrada(1, monto="10350", decision="ESCALAR"), entrada(2, monto="2950", decision="FRENAR"),
                  entrada(3, monto="9000", fecha=date(2026, 9, 15))]
    assert ev(m, e, no_cuentan)["R07"].veredicto == "OK"


def test_r08_umbral_de_aprobacion_humana_escala(m):
    r = ev(m, hacer_extraccion(monto_total=Decimal("7500"), referencia_acuerdo="OC-2026-019"))["R08"]
    assert r.veredicto == "ESCALAR" and "7.500,00" in r.mensaje
    assert ev(m, hacer_extraccion(monto_total=Decimal("5000"), referencia_acuerdo="OC-2026-019"))["R08"].veredicto == "OK"


def test_r09_conciliacion_con_el_acuerdo(m):
    r = ev(m, hacer_extraccion(monto_total=Decimal("3900")))["R09"]
    assert r.veredicto == "ESCALAR" and r.evidencia["diferencia_pct"] == 21.9 and "3.900,00" in r.mensaje
    assert ev(m, hacer_extraccion(monto_total=Decimal("3150")))["R09"].veredicto == "OK"  # dentro de la tolerancia
    assert ev(m, hacer_extraccion(referencia_acuerdo="OC-2026-021"))["R09"].veredicto == "ESCALAR"  # de otro proveedor
    assert ev(m, hacer_extraccion(referencia_acuerdo="OC-9999"))["R09"].veredicto == "ESCALAR"
    sin_ref = ev(m, hacer_extraccion(referencia_acuerdo=None, monto_total=Decimal("3000")))["R09"]
    assert sin_ref.veredicto == "ESCALAR" and "exige acuerdo" in sin_ref.mensaje
    assert ev(m, hacer_extraccion(referencia_acuerdo=None, monto_total=Decimal("2999")))["R09"].veredicto == "OK"


def test_r10_fraccionamiento_suma_solo_lo_pagado_en_la_ventana(m):
    pixel = dict(proveedor_tax_id="87-1234567", proveedor_nombre="Pixel Forge LLC", cuenta_destino="202411778821",
                 referencia_acuerdo=None)
    previas = [entrada(1, proveedor_id="PRV-002", numero="PF-1188", monto="1850", fecha=date(2026, 10, 2)),
               entrada(2, proveedor_id="PRV-002", numero="PF-1201", monto="2950", fecha=date(2026, 10, 6)),
               entrada(3, proveedor_id="PRV-002", numero="PF-0001", monto="18900", fecha=date(2026, 10, 6), decision="FRENAR")]
    e = hacer_extraccion(numero_factura="PF-1203", monto_total=Decimal("2800"), fecha_emision=date(2026, 10, 8), **pixel)
    r = ev(m, e, previas)["R10"]
    assert r.veredicto == "ESCALAR" and r.evidencia["suma"] == 7600 and len(r.evidencia["facturas"]) == 2
    e = hacer_extraccion(numero_factura="PF-1201", monto_total=Decimal("2950"), fecha_emision=date(2026, 10, 6), **pixel)
    assert ev(m, e, previas[:1])["R10"].veredicto == "OK"  # 1.850 + 2.950 = 4.800
    e = hacer_extraccion(numero_factura="PF-1210", monto_total=Decimal("980"), fecha_emision=date(2026, 10, 16), **pixel)
    assert ev(m, e, previas)["R10"].veredicto == "OK"  # fuera de la ventana de 7 días


def test_r11_fechas(m):
    assert ev(m, hacer_extraccion(fecha_emision=date(2026, 10, 20), fecha_vencimiento=date(2026, 10, 16)))["R11"].veredicto == "ESCALAR"
    r = ev(m, hacer_extraccion(fecha_emision=date(2026, 6, 20), fecha_vencimiento=date(2026, 7, 19)))["R11"]
    assert r.veredicto == "ESCALAR" and "90 días" in r.mensaje
    assert ev(m, hacer_extraccion(fecha_emision=date(2026, 10, 18), fecha_vencimiento=date(2026, 11, 2)))["R11"].veredicto == "ESCALAR"
    assert ev(m, hacer_extraccion(fecha_emision=date(2026, 8, 1), fecha_vencimiento=date(2026, 8, 18)))["R11"].veredicto == "OK"
    assert ev(m)["R11"].veredicto == "OK"


def test_r12_extraccion_inconsistente_escala(m):
    assert ev(m, consistente=False)["R12"].veredicto == "ESCALAR"
    assert ev(m)["R12"].veredicto == "OK"


def test_r13_remitente_del_correo(m):
    r = ev(m, canal="correo", remitente="billing@nubeandina-co.com")["R13"]
    assert r.veredicto == "ESCALAR" and "nubeandina-co.com" in r.mensaje
    assert ev(m, canal="correo", remitente="Nube Andina <Facturacion@NubeAndina.co>")["R13"].veredicto == "OK"
    carla = hacer_extraccion(proveedor_tax_id="RIOC850101XY1", proveedor_nombre="Carla Ríos", cuenta_destino="012180001234567890",
                             referencia_acuerdo=None, monto_total=Decimal("2900"))
    assert ev(m, carla, canal="correo", remitente="carla@gmail.com")["R13"].evidencia == {"omitida": "sin dominio en maestro"}
    assert ev(m, canal="pdf")["R13"].veredicto == "OK"


def test_j01_instrucciones_ocultas_frenan(m):
    r = ev(m, jev_=jev(ocultas=0.9))["J01"]
    assert r.veredicto == "FRENAR" and "90" in r.mensaje
    assert ev(m, jev_=jev(ocultas=0.34))["J01"].veredicto == "OK"


def test_j02_no_parece_factura_escala(m):
    assert ev(m, jev_=jev(factura=0.2))["J02"].veredicto == "ESCALAR"
    assert ev(m, jev_=jev(factura=0.5))["J02"].veredicto == "OK"


def test_j03_riesgo_escala_o_frena(m):
    assert ev(m, jev_=jev(riesgo=1.0))["J03"].veredicto == "OK"
    assert ev(m, jev_=jev(riesgo=1.5))["J03"].veredicto == "ESCALAR"
    assert ev(m, jev_=jev(riesgo=2.7))["J03"].veredicto == "FRENAR"


def test_j04_no_coincide_con_lo_acordado(m):
    assert ev(m, jev_=jev(acuerdo="differs_concept", confianza=0.8))["J04"].veredicto == "ESCALAR"
    assert ev(m, jev_=jev(acuerdo="differs_concept", confianza=0.4))["J04"].veredicto == "OK"  # sin confianza suficiente
    assert ev(m, jev_=jev(acuerdo="matches"))["J04"].veredicto == "OK"
    sin_ref = ev(m, hacer_extraccion(referencia_acuerdo=None), jev_=jev(acuerdo="no_agreement"))["J04"]
    assert sin_ref.evidencia == {"omitida": "sin acuerdo"}


def test_sin_jev_las_j_quedan_ok_con_marca(m):
    r = ev(m, jev_=None)
    assert all(r[k].veredicto == "OK" and r[k].evidencia == {"sin_jev": True} for k in ("J01", "J02", "J03", "J04"))

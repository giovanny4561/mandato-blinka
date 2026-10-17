"""Set de inyecciones (data/inyecciones.json, de scripts/generar_inyecciones.py), offline.

Peor caso: el extractor y JEV cayeron por completo (cuenta, número, monto o acuerdo del atacante; JEV sin alarma).
Aun así ninguna termina en PAGAR. Se evalúa contra las 24 decisiones del set principal tal cual (así I08/I10 ven
F01) y otra vez con el presupuesto del mes libre: con el historial real R07 escala casi todo y taparía el freno real.
"""
import json
from datetime import date

import pytest

from app import config, mandato, motor
from app.modelos import Decision, Extraccion, RespuestasJEV

HOY = date(2026, 10, 17)
CASOS = config.RAIZ / "data" / "casos.json"
INYECCIONES = json.loads((config.RAIZ / "data" / "inyecciones.json").read_text(encoding="utf-8"))


def historial_set_principal(m):
    """Las 24 decisiones del set principal, en orden, como en tests/test_casos.py."""
    historial = []
    for i, caso in enumerate(json.loads(CASOS.read_text(encoding="utf-8"))):
        e = Extraccion(**caso["extraccion_esperada"])
        decision, regla, reglas = motor.decidir_offline(
            e, RespuestasJEV(**caso["jev_esperado"]), m, historial, canal=caso["canal"],
            remitente=caso.get("remitente"), consistente=True, factura_hash=caso["archivo"], hoy=HOY,
        )
        prov = mandato.proveedor_por(e, m)
        d = Decision(factura_hash=caso["archivo"], archivo=caso["archivo"], canal=caso["canal"],
                     proveedor_id=prov.id if prov else None, extraccion=e, reglas=reglas, decision=decision,
                     regla_decisiva=regla, explicacion="", mandato_hash="", ts="2026-10-17T00:00:00")
        historial.append(motor.a_historial(d, i + 1))
    return historial


def test_el_set_tiene_diez_ataques_y_ninguno_espera_pagar():
    assert [i["archivo"][:3] for i in INYECCIONES] == [f"I{n:02d}" for n in range(1, 11)]
    assert sum(i["canal"] == "correo" for i in INYECCIONES) == 2
    assert all(i["esperado"] == "NO_PAGAR" for i in INYECCIONES)
    for i in INYECCIONES:
        assert (config.RAIZ / "data" / "facturas" / "inyecciones" / i["archivo"]).is_file()


@pytest.mark.parametrize("iny", INYECCIONES, ids=[f"{i['archivo'][:3]}-{i['freno_esperado']}" for i in INYECCIONES])
def test_inyeccion_con_extractor_y_jev_engañados_no_termina_en_pagar(iny):
    m = mandato.cargar()
    historial = historial_set_principal(m)
    presupuesto_libre = [h.model_copy(update={"ts": "2026-09-30T00:00:00"}) for h in historial]  # R07 cuenta por mes de pago
    e = Extraccion(**iny["extraccion_engañada"])
    assert str(getattr(e, iny["campo_atacado"])) == iny["valor_atacante"], "la extracción no trae el valor del atacante"
    jev = RespuestasJEV(**iny["jev_engañado"])
    salidas = {}
    for nombre, h in (("historial real", historial), ("presupuesto libre", presupuesto_libre)):
        decision, regla, reglas = motor.decidir_offline(
            e, jev, m, h, canal=iny["canal"], remitente=iny["remitente"], consistente=True,
            factura_hash=iny["archivo"], hoy=HOY,
        )
        salidas[nombre] = (decision, regla, [r.regla_id for r in reglas if r.veredicto != "OK"])
    informe = f"{iny['archivo']} ({iny['tecnica']}): " + " · ".join(
        f"{k}: {d} por {r} (fallan {', '.join(f)})" for k, (d, r, f) in salidas.items())
    assert all(d != "PAGAR" for d, _, _ in salidas.values()), informe
    assert not any(f.startswith("J") for _, _, fallan in salidas.values() for f in fallan), "JEV no está engañado: " + informe
    assert salidas["presupuesto libre"][1] == iny["freno_esperado"], informe

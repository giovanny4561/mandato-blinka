"""Rieles de Blinka (app/rieles.py) y facturas en la moneda del proveedor medidas en moneda base."""
from datetime import date
from decimal import Decimal

import pytest

from app import mandato as mod_mandato, reglas, rieles
from app.modelos import Mandato
from tests.conftest import hacer_decision, hacer_extraccion


@pytest.mark.parametrize("valido,invalido,fn", [
    ("021000021", "021000022", rieles.aba),
    ("032180000118359719", "032180000118359710", rieles.clabe),
    ("2850590940090418135201", "2850590940090418135202", rieles.cbu),
    ("DE89370400440532013000", "DE89370400440532013001", rieles.iban),
    ("COLOCOBM", "COLO1OBM", rieles.bic),
    ("11.222.333/0001-81", "11.222.333/0001-82", rieles.chave_pix),
    ("@DATOSVALLE2201", "DATOS VALLE", rieles.llave_breb),
])
def test_validadores_con_ejemplos_de_los_estandares(valido, invalido, fn):
    assert fn(valido) and not fn(invalido)


def test_problemas_por_riel_pais_moneda_y_cuenta():
    assert rieles.problemas("spei", "MX", "MXN", "032180000118359719", None, None, None) == []
    errores = rieles.problemas("spei", "CO", "USD", "032180000118359710", None, None, None)
    assert len(errores) == 3 and any("no paga a CO" in x for x in errores)
    assert rieles.problemas("ach", "US", "USD", "483920175", "123456789", None, None) == ["ACH necesita un número de ruta ABA válido"]
    assert rieles.problemas("usdc", "CO", "USD", "x", None, "7EcDhSYGxXyscszYEp35KHN8vvw3svAuLKTzXwCFLtV", "solana") == []
    assert rieles.problemas("usdc", "CO", "USD", "x", None, "0x123", "base") == ["la billetera USDC no es una dirección válida de base"]
    assert "riel desconocido" in rieles.problemas("paypal", "US", "USD", "x", None, None, None)[0]


def _mandato_con(**proveedor) -> dict:
    datos = mod_mandato.cargar().model_dump(mode="json")
    datos["proveedores_permitidos"][0] |= proveedor
    return datos


def test_el_mandato_rechaza_un_maestro_que_no_se_puede_pagar():
    with pytest.raises(ValueError, match="CLABE|cuenta no es válida"):
        Mandato.model_validate(_mandato_con(pais="MX", moneda="MXN", riel="spei", cuenta="032180000118359710")
                               | {"tasas_referencia": {"MXN": "18"}})
    with pytest.raises(ValueError, match="sin tasa de referencia"):
        Mandato.model_validate(_mandato_con(pais="MX", moneda="MXN", riel="spei", cuenta="032180000118359719"))


def _mandato_cop() -> Mandato:
    datos = _mandato_con(moneda="COP", riel="bre_b", cuenta="@NUBEANDINA01") | {"tasas_referencia": {"COP": "4000"}}
    datos["acuerdos"] = [a | {"moneda": "COP", "monto": str(Decimal(a["monto"]) * 4000)} if a["proveedor_id"] == "PRV-001" else a
                         for a in datos["acuerdos"]]
    return Mandato.model_validate(datos)


def _ev(m, e):
    return {r.regla_id: r for r in reglas.evaluar(e, m, [], canal="pdf", remitente=None, consistente=True,
                                                  hoy=date(2026, 10, 17), jev=None, factura_hash="h")}


def test_factura_en_pesos_se_mide_en_dolares_con_la_tasa_del_mandato():
    m = _mandato_cop()
    ok = _ev(m, hacer_extraccion(moneda="COP", monto_total=Decimal("12800000"), cuenta_destino="NUBEANDINA01"))
    assert all(ok[k].veredicto == "OK" for k in ("R03", "R06", "R08", "R09")), {k: ok[k].mensaje for k in ok if ok[k].veredicto != "OK"}
    tope = _ev(m, hacer_extraccion(moneda="COP", monto_total=Decimal("44000000"), cuenta_destino="NUBEANDINA01",
                                   referencia_acuerdo=None))
    assert tope["R06"].veredicto == "FRENAR" and "COP 44.000.000,00" in tope["R06"].mensaje and "USD" in tope["R06"].mensaje
    umbral = _ev(m, hacer_extraccion(moneda="COP", monto_total=Decimal("24000000"), cuenta_destino="NUBEANDINA01",
                                     referencia_acuerdo=None))
    assert umbral["R08"].veredicto == "ESCALAR" and umbral["R09"].veredicto == "ESCALAR"  # USD 6.000: umbral y sin acuerdo
    dolares = _ev(m, hacer_extraccion(moneda="USD", cuenta_destino="NUBEANDINA01"))
    assert dolares["R03"].veredicto == "ESCALAR"  # el proveedor factura en COP


def test_riel_sugerido_y_orden_por_riel():
    m = _mandato_cop()
    nube = m.proveedores_permitidos[0]
    assert rieles.etiqueta(nube) == "Bre-B (COP)"
    d = hacer_decision(extraccion=hacer_extraccion(moneda="COP", monto_total=Decimal("12800000")))
    o = rieles.orden(d, nube)
    assert o["riel"] == "Bre-B" and o["moneda_pago"] == "COP" and o["estado"].startswith("simulada")
    assert o["beneficiario"]["cuenta"] == "****NA01"
    assert rieles.orden(hacer_decision(decision="ESCALAR"), nube) is None

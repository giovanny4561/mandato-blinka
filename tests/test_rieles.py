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


# ---------- pain.001.001.09 (ISO 20022) ----------
import xml.etree.ElementTree as ET

from fastapi.testclient import TestClient

from app import registro
from app.main import app
from app.modelos import Empresa, Proveedor

NS = {"p": "urn:iso:std:iso:20022:tech:xsd:pain.001.001.09"}
EMPRESA = Empresa(nombre="Andina Labs Inc.", pais="US", moneda_base="USD")
SELLO = "ab12" * 16


def _prov(**cambios) -> Proveedor:
    return Proveedor(**(dict(id="PRV-X", nombre="Pixel Forge LLC", tax_id="87-1234567", pais="US", moneda="USD",
                             banco="Chase", cuenta="483920175", riel="ach", codigo_banco="021000021") | cambios))


def _pain(prov, **cambios):
    d = hacer_decision(id=7, sello=SELLO, fecha_pago_programada=date(2026, 10, 20), **cambios)
    return ET.fromstring(rieles.pain001(d, prov, EMPRESA))


def _t(doc, ruta):
    return doc.findtext(ruta, namespaces=NS)


def test_pain001_swift_lleva_bic_monto_fecha_y_referencia():
    nube = next(p for p in mod_mandato.cargar().proveedores_permitidos if p.id == "PRV-001")
    doc = _pain(nube)
    assert doc.tag == "{urn:iso:std:iso:20022:tech:xsd:pain.001.001.09}Document"
    assert _t(doc, "p:CstmrCdtTrfInitn/p:GrpHdr/p:NbOfTxs") == "1"
    assert _t(doc, "p:CstmrCdtTrfInitn/p:GrpHdr/p:CtrlSum") == "3200.00"
    assert _t(doc, "p:CstmrCdtTrfInitn/p:GrpHdr/p:InitgPty/p:Nm") == "Andina Labs Inc."
    assert _t(doc, ".//p:PmtInf/p:PmtMtd") == "TRF" and _t(doc, ".//p:PmtInf/p:ReqdExctnDt/p:Dt") == "2026-10-20"
    assert _t(doc, ".//p:Dbtr/p:Nm") == "Andina Labs Inc." and _t(doc, ".//p:DbtrAcct/p:Id/p:Othr/p:Id") == "SIMULADA"
    monto = doc.find(".//p:CdtTrfTxInf/p:Amt/p:InstdAmt", NS)
    assert monto.text == "3200.00" and monto.get("Ccy") == "USD"
    assert _t(doc, ".//p:CdtTrfTxInf/p:PmtId/p:EndToEndId") == SELLO[:35]
    assert _t(doc, ".//p:CdtrAgt/p:FinInstnId/p:BICFI") == "COLOCOBM"
    assert _t(doc, ".//p:Cdtr/p:Nm") == "Nube Andina SAS" and _t(doc, ".//p:CdtrAcct/p:Id/p:Othr/p:Id") == nube.cuenta
    assert "NA-2026-0917" in _t(doc, ".//p:RmtInf/p:Ustrd")


@pytest.mark.parametrize("riel", ["ach", "wire"])
def test_pain001_ach_y_wire_identifican_el_banco_por_aba(riel):
    doc = _pain(_prov(riel=riel))
    assert _t(doc, ".//p:CdtrAgt/p:FinInstnId/p:ClrSysMmbId/p:ClrSysId/p:Cd") == "USABA"
    assert _t(doc, ".//p:CdtrAgt/p:FinInstnId/p:ClrSysMmbId/p:MmbId") == "021000021"
    assert doc.find(".//p:BICFI", NS) is None and _t(doc, ".//p:CdtrAcct/p:Id/p:Othr/p:Id") == "483920175"


def test_pain001_sepa_lleva_iban_y_euros():
    doc = _pain(_prov(pais="DE", moneda="EUR", riel="sepa", cuenta="DE89 3704 0044 0532 0130 00", codigo_banco=None),
                extraccion=hacer_extraccion(moneda="EUR", monto_total=Decimal("1234.5")))
    assert _t(doc, ".//p:CdtrAcct/p:Id/p:IBAN") == "DE89370400440532013000"
    monto = doc.find(".//p:InstdAmt", NS)
    assert monto.text == "1234.50" and monto.get("Ccy") == "EUR" and _t(doc, ".//p:GrpHdr/p:CtrlSum") == "1234.50"


def test_pain001_escapa_el_texto_del_proveedor_y_de_la_factura():
    doc = _pain(_prov(nombre="Pixel & <Forge>"), extraccion=hacer_extraccion(numero_factura="A&B<1>"))
    assert _t(doc, ".//p:Cdtr/p:Nm") == "Pixel & <Forge>" and "A&B<1>" in _t(doc, ".//p:RmtInf/p:Ustrd")


def test_pain001_no_aplica_a_usdc_bre_b_ni_a_lo_que_no_es_pagar():
    usdc = _prov(riel="usdc", red_usdc="base", billetera_usdc="0x" + "1" * 40, codigo_banco=None)
    breb = _prov(pais="CO", moneda="COP", riel="bre_b", cuenta="@NUBEANDINA01", codigo_banco=None)
    assert rieles.pain001(hacer_decision(), usdc, EMPRESA) is None
    assert rieles.pain001(hacer_decision(), breb, EMPRESA) is None
    assert rieles.pain001(hacer_decision(decision="FRENAR"), _prov(), EMPRESA) is None
    assert rieles.pain001(hacer_decision(), None, EMPRESA) is None


def test_api_pain001_descarga_el_xml_o_404():
    registro.insertar(hacer_decision())  # Nube Andina: SWIFT
    registro.insertar(hacer_decision(factura_hash="otra", decision="FRENAR", regla_decisiva="R02"))
    registro.insertar(hacer_decision(factura_hash="usdc", proveedor_id="PRV-002"))  # Pixel Forge: USDC en Base
    with TestClient(app) as c:
        r = c.get("/api/decisiones/1/pain001")
        assert r.status_code == 200 and r.headers["content-type"].startswith("application/xml")
        assert "attachment" in r.headers["content-disposition"]
        assert ET.fromstring(r.content).find(".//p:BICFI", NS).text == "COLOCOBM"
        for id_ in (2, 3, 999):
            assert c.get(f"/api/decisiones/{id_}/pain001").status_code == 404
        assert "Descargar pain.001 (ISO 20022)" in c.get("/decisiones/1").text
        assert "pain.001" not in c.get("/decisiones/3").text

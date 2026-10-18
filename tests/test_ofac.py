"""Lista SDN de OFAC dentro de R01 (app/ofac.py) y al guardar el mandato. Las pruebas inyectan entradas falsas."""
import json
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import config, ofac, reglas
from app import mandato as mod_mandato
from app.main import app
from app.modelos import Mandato
from tests.conftest import RAIZ, hacer_extraccion

FALSO = "Banco Falso de Prueba S.A."


@pytest.fixture
def lista(monkeypatch):
    """Lista inyectada: una entrada falsa, sin depender de data/ofac_sdn.txt."""
    monkeypatch.setattr(ofac, "_nombres", lambda ruta: frozenset({"BANCOFALSODEPRUEBASA"}))


def r01(m, e):
    return next(r for r in reglas.evaluar(e, m, [], canal="pdf", remitente=None, consistente=True, hoy=date(2026, 10, 17),
                                          jev=None, factura_hash="h") if r.regla_id == "R01")


def test_normaliza_como_alfanumerico_sin_tildes():
    assert ofac.normalizar("Logística Sur S.p.A.") == "LOGISTICASURSPA" and ofac.normalizar(None) == ""


def test_r01_frena_si_el_nombre_que_leyo_el_extractor_figura_en_ofac(lista):
    m = mod_mandato.cargar()
    r = r01(m, hacer_extraccion(proveedor_nombre="BANCO FALSO DE PRUEBA, S.A."))  # el tax id sigue siendo de Nube Andina
    assert r.veredicto == "FRENAR" and "figura en la lista de sanciones OFAC (SDN)" in r.mensaje
    assert r01(m, hacer_extraccion()).veredicto == "OK"


def test_r01_frena_si_el_proveedor_del_mandato_figura_en_ofac(monkeypatch):
    m = mod_mandato.cargar()
    monkeypatch.setattr(ofac, "_nombres", lambda ruta: frozenset({"NUBEANDINASAS"}))
    r = r01(m, hacer_extraccion(proveedor_nombre=None))  # encontrado por tax id
    assert r.veredicto == "FRENAR" and "Nube Andina SAS" in r.mensaje and "OFAC (SDN)" in r.mensaje


def test_sin_archivo_r01_funciona_como_antes(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "ofac_path", lambda: tmp_path / "no-existe.txt")
    m = mod_mandato.cargar()
    assert r01(m, hacer_extraccion(proveedor_nombre=FALSO)).veredicto == "OK"
    r = r01(m, hacer_extraccion(proveedor_nombre=FALSO, proveedor_tax_id="999"))
    assert r.veredicto == "FRENAR" and "no está entre los beneficiarios permitidos" in r.mensaje


def _con_sancionado() -> dict:
    datos = json.loads((RAIZ / "data" / "mandato.json").read_text(encoding="utf-8"))
    datos["proveedores_permitidos"].append(datos["proveedores_permitidos"][0] | {"id": "PRV-099", "nombre": FALSO,
                                                                                 "tax_id": "900000001"})
    return datos


def test_el_mandato_rechaza_guardar_un_proveedor_sancionado(lista):
    with pytest.raises(ValueError, match="PRV-099.*OFAC"):
        Mandato.model_validate(_con_sancionado())
    with TestClient(app) as c:
        r = c.post("/mandato", data={"json": json.dumps(_con_sancionado()), "pin": "123456"})
        assert r.status_code == 400 and "figura en la lista de sanciones OFAC" in r.text
    assert "PRV-099" not in {p.id for p in mod_mandato.cargar().proveedores_permitidos}


def test_ningun_proveedor_de_la_demo_figura_en_la_lista_real():
    lineas = (RAIZ / "data" / "ofac_sdn.txt").read_text(encoding="utf-8").splitlines()
    assert lineas[0].startswith("#") and "ofac.treas.gov" in "\n".join(lineas[:3])
    nombres = {x for x in lineas if not x.startswith("#")}
    assert len(nombres) > 5000 and "BANCONACIONALDECUBA" in nombres
    for archivo in ("demo/mandato.json", "data/mandato.json"):
        for p in json.loads((RAIZ / archivo).read_text(encoding="utf-8"))["proveedores_permitidos"]:
            assert ofac.normalizar(p["nombre"]) not in nombres, (archivo, p["nombre"])

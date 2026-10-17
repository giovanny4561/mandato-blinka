from fastapi.testclient import TestClient

from app import registro
from app.main import app
from tests.conftest import hacer_decision


def test_health():
    with TestClient(app) as c:
        assert c.get("/health").json() == {"ok": True}


def test_panel_vacio_renderiza():
    with TestClient(app) as c:
        r = c.get("/")
        assert r.status_code == 200 and "Mandato" in r.text and "Todavía no hay decisiones" in r.text


def test_api_metricas_y_decisiones():
    registro.insertar(hacer_decision())
    with TestClient(app) as c:
        assert c.get("/api/metricas").json()["total"] == 1
        assert "pagos fuera de reglas" in c.get("/").text
        assert c.get("/api/decisiones").json()[0]["decision"] == "PAGAR"
        assert c.get("/decisiones").status_code == 200
        assert "Por qué" in c.get("/decisiones/1").text
        assert c.get("/decisiones/999").status_code == 404


def test_pendientes_exige_pin_y_resuelve():
    esc = registro.insertar(hacer_decision(decision="ESCALAR", regla_decisiva="R08", explicacion="ESCALAR · R08 Umbral"))
    with TestClient(app) as c:
        assert "R08" in c.get("/pendientes").text
        r = c.post(f"/pendientes/{esc}/resolver", data={"accion": "aprobar", "pin": "mal", "nota": ""})
        assert r.status_code == 403
        r = c.post(f"/pendientes/{esc}/resolver", data={"accion": "aprobar", "pin": "123456", "nota": "ok"}, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/decisiones/2"
        assert registro.pendientes() == []
        r = c.post(f"/pendientes/{esc}/resolver", data={"accion": "rechazar", "pin": "123456", "nota": ""})
        assert r.status_code == 400  # ya resuelta


# ---------- bandeja y mandato (extractor y JEV simulados) ----------
import json
import shutil

import pytest

from app import decisor, extractor
from app.modelos import RespuestaJEV, RespuestasJEV
from tests.conftest import RAIZ, hacer_extraccion

CHAT = ("[5/10/26, 9:12 a. m.] Carla Ríos: Hola! Te paso mi cuenta de cobro CR-010: USD 2.900. "
        "RFC RIOC850101XY1. BBVA México, CLABE 012180001234567890.").encode()


def jev_simulado(_state):
    return RespuestasJEV(backend="llm", modelo="simulado", latencia_ms=5, respuestas={
        "is_invoice": RespuestaJEV(tipo="noul", probabilidad=0.98),
        "hidden_instructions": RespuestaJEV(tipo="noul", probabilidad=0.01),
        "matches_agreement": RespuestaJEV(tipo="choice", valor="no_agreement", confianza=0.9,
                                          probabilidades={"no_agreement": 0.9, "matches": 0.1}),
        "payment_risk": RespuestaJEV(tipo="score", valor=0.1, confianza=0.9),
        "expense_category": RespuestaJEV(tipo="choice", valor="contractor_payroll", confianza=0.8,
                                         probabilidades={"contractor_payroll": 0.8, "other": 0.2}),
    })


@pytest.fixture
def simulados(monkeypatch):
    carla = hacer_extraccion(proveedor_nombre="Carla Ríos", proveedor_tax_id="RIOC850101XY1", numero_factura="CR-010",
                             monto_total=2900, banco="BBVA México", cuenta_destino="012180001234567890",
                             referencia_acuerdo=None, conceptos=["Diseño UX"])
    monkeypatch.setattr(extractor, "extraer", lambda doc, hoy=None: (carla, True, 0.001))
    monkeypatch.setattr(decisor, "preguntar_factura", jev_simulado)


def test_subir_factura_decide_y_redirige(simulados):
    with TestClient(app) as c:
        assert c.post("/bandeja", files={"archivo": ("F04.txt", CHAT, "text/plain")}, data={"pin": "mal"}).status_code == 403
        r = c.post("/bandeja", files={"archivo": ("F04_carla_rios.txt", CHAT, "text/plain")}, data={"pin": "123456"}, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/decisiones/1"
        d = registro.obtener(1)
        assert d.decision == "PAGAR" and d.canal == "chat" and d.jev.backend == "llm"
        assert c.post("/bandeja", files={"archivo": ("x.exe", b"MZ", "application/octet-stream")}, data={"pin": "123456"}).status_code == 400
        otra = c.post("/api/procesar", files={"archivo": ("F22.txt", CHAT, "text/plain")}, data={"pin": "123456"}).json()
        assert otra["decision"] == "FRENAR" and otra["regla_decisiva"] == "R04"  # misma factura otra vez: duplicado
        assert otra["extraccion"]["cuenta_destino"] == "****7890"  # la API no expone la cuenta completa
        assert c.get("/api/decisiones?limite=0").status_code == 422


def test_demo_procesa_en_segundo_plano(simulados, entorno):
    casos = json.loads((RAIZ / "data" / "casos.json").read_text())
    (entorno / "facturas").mkdir()
    for c_ in casos:
        if c_["archivo"].endswith(".txt"):
            shutil.copy(RAIZ / "data" / "facturas" / c_["archivo"], entorno / "facturas" / c_["archivo"])
    shutil.copy(RAIZ / "data" / "casos.json", entorno / "casos.json")
    with TestClient(app) as c:
        assert "2 facturas" in c.get("/bandeja").text
        assert c.post("/bandeja/demo", data={"pin": "nope"}).status_code == 403
        r = c.post("/bandeja/demo", data={"pin": "123456"}, follow_redirects=False)
        assert r.status_code == 303
        assert registro.metricas()["total"] == 2  # TestClient ejecuta las tareas de fondo al responder


def test_mandato_ver_y_guardar_con_pin():
    with TestClient(app) as c:
        r = c.get("/mandato")
        assert r.status_code == 200 and "Nube Andina SAS" in r.text
        bueno = json.loads((RAIZ / "data" / "mandato.json").read_text())
        bueno["limites"]["tope_por_pago"] = 12000
        assert c.post("/mandato", data={"json": json.dumps(bueno), "pin": "mal"}).status_code == 403
        r = c.post("/mandato", data={"json": json.dumps(bueno), "pin": "123456"}, follow_redirects=False)
        assert r.status_code == 303
        assert "12.000" in c.get("/mandato").text
        r = c.post("/mandato", data={"json": '{"version": "x"}', "pin": "123456"})
        assert r.status_code == 400 and "no es válido" in r.text



def test_pin_de_ejemplo_o_corto_deshabilita_todo(monkeypatch):
    esc = registro.insertar(hacer_decision(decision="ESCALAR", regla_decisiva="R08"))
    for malo in ("cambiar-esto", "1234", ""):
        monkeypatch.setenv("APROBADOR_PIN", malo)
        with TestClient(app) as c:
            r = c.post(f"/pendientes/{esc}/resolver", data={"accion": "aprobar", "pin": malo, "nota": ""})
            assert r.status_code in (403, 422)  # 422 cuando el PIN llega vacío: igual de cerrado

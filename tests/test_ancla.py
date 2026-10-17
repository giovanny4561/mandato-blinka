"""Anclaje en OpenTimestamps con calendarios simulados (sin red)."""
import hashlib

import httpx
import pytest
from fastapi.testclient import TestClient

from app import ancla, registro
from app.main import app
from tests.conftest import hacer_decision

URI = b"https://alice.btc.calendar.opentimestamps.org"
# una rama válida: append(4 bytes) → sha256 → atestación pendiente en el calendario
RAMA = b"\xf0\x04abcd\x08\x00" + bytes.fromhex("83dfe30d2ef90c8e") + bytes([len(URI) + 1, len(URI)]) + URI


@pytest.fixture
def calendarios(monkeypatch):
    caidos = set()

    def post(url, **kw):
        if any(url.startswith(c) for c in caidos):
            raise httpx.ConnectError("caído")
        return httpx.Response(200, content=RAMA, request=httpx.Request("POST", url))
    monkeypatch.setattr(ancla.httpx, "post", post)
    return caidos


def test_sellar_arma_un_ots_con_una_rama_por_calendario(calendarios):
    ots, usados = ancla.sellar(b"cabeza")
    digest = hashlib.sha256(b"cabeza").digest()
    assert ots == ancla.MAGIA + b"\x01\x08" + digest + b"\xff" + RAMA + b"\xff" + RAMA + RAMA
    assert usados == list(ancla.CALENDARIOS)
    calendarios.update(ancla.CALENDARIOS[:2])
    assert ancla.sellar(b"cabeza")[0].endswith(digest + RAMA)  # con uno vivo basta
    calendarios.add(ancla.CALENDARIOS[2])
    with pytest.raises(RuntimeError):
        ancla.sellar(b"cabeza")


def test_anclar_solo_cuando_la_cabeza_cambia(calendarios):
    assert ancla.anclar_cabeza() is None  # registro vacío
    registro.insertar(hacer_decision())
    a = ancla.anclar_cabeza()
    assert a["fila_id"] == registro.verificar()["cabeza"]["id"]
    assert ancla.anclar_cabeza()["id"] == a["id"]  # misma cabeza: no vuelve a enviar
    registro.insertar(hacer_decision(archivo="otra.pdf"))
    assert ancla.anclar_cabeza()["id"] != a["id"]
    assert len(registro.anclajes()) == 2


def test_api_de_anclajes(calendarios):
    registro.insertar(hacer_decision())
    with TestClient(app) as c:
        assert c.post("/api/anclajes", data={"pin": "mal"}).status_code == 403
        a = c.post("/api/anclajes", data={"pin": "123456"}).json()
        lista = c.get("/api/anclajes").json()
        assert lista[0]["id"] == a["id"] and "ots_b64" not in lista[0]
        archivo = c.get(lista[0]["archivo"]).content
        ots = c.get(lista[0]["prueba"]).content
        assert ots.startswith(ancla.MAGIA) and hashlib.sha256(archivo).digest() in ots
        assert "Anclada en Bitcoin" in c.get("/").text


BLOQUE = 967123
CONFIRMADA = b"\xf0\x02zz\x08\x00" + ancla.BITCOIN + bytes([3]) + bytes([0xd3, 0x83, 0x3b])  # altura 967123 en varuint


def test_completar_cambia_la_rama_pendiente_por_la_confirmada(calendarios, monkeypatch):
    pedidos = []

    def get(url, **kw):
        pedidos.append(url)
        return httpx.Response(200, content=CONFIRMADA, request=httpx.Request("GET", url))
    monkeypatch.setattr(ancla.httpx, "get", get)
    ots, _ = ancla.sellar(b"cabeza")
    nuevo, bloque = ancla.completar(ots)
    assert bloque == BLOQUE and nuevo.count(CONFIRMADA) == 3 and ancla.PENDIENTE not in nuevo
    commit = hashlib.sha256(hashlib.sha256(b"cabeza").digest() + b"abcd").hexdigest()  # append "abcd" y sha256
    assert pedidos[0] == f"{URI.decode()}/timestamp/{commit}"
    assert ancla.completar(nuevo) == (nuevo, BLOQUE)  # ya confirmada: no cambia


def test_completar_pendientes_guarda_el_bloque_y_respeta_lo_que_sigue_pendiente(calendarios, monkeypatch):
    registro.insertar(hacer_decision())
    a = ancla.anclar_cabeza()
    monkeypatch.setattr(ancla.httpx, "get", lambda url, **kw: httpx.Response(404, request=httpx.Request("GET", url)))
    assert ancla.completar_pendientes() == 0 and registro.anclaje(a["id"])["bloque"] is None
    monkeypatch.setattr(ancla.httpx, "get", lambda url, **kw: httpx.Response(200, content=CONFIRMADA, request=httpx.Request("GET", url)))
    assert ancla.completar_pendientes() == 1 and registro.anclaje(a["id"])["bloque"] == BLOQUE
    with TestClient(app) as c:
        assert f"confirmada en el bloque {BLOQUE}" in c.get("/").text

"""Arena de mutaciones (scripts/arena.py): más de mil ataques con extractor y JEV engañados; ninguno termina en PAGAR."""
import importlib.util

from app import config


def test_arena_de_mutaciones_ningun_ataque_se_paga():
    spec = importlib.util.spec_from_file_location("arena", config.RAIZ / "scripts" / "arena.py")
    arena = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(arena)
    r = arena.correr()
    assert r["ataques"] >= 1000 and r["bases"] >= 40
    assert r["pagados"] == 0, r["pagados_detalle"]
    assert len(r["por_mutacion"]) == 11  # cada tipo de ataque se probó solo


def test_pagina_arena_muestra_los_ataques_y_cero_pagados():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        r = c.get("/arena")
        assert r.status_code == 200
        assert '<span class="cifra">0</span> pagados' in r.text
        assert "Cuenta bancaria cambiada" in r.text and "R02" in r.text and "Cuenta coincide con el maestro" in r.text
        assert 'href="/arena"' in c.get("/").text and "/arena" in c.get("/llms.txt").text

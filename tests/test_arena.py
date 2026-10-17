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

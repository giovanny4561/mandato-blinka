"""Demo de 132 facturas (demo/, producida por scripts/generar_demo.py) repetida offline con las reglas reales."""
import importlib.util
import json

from app import config
from app.modelos import Mandato

DEMO = config.RAIZ / "demo"


def test_demo_132_ninguna_trampa_se_paga_y_ninguna_legitima_se_frena():
    casos = json.loads((DEMO / "casos.json").read_text(encoding="utf-8"))
    assert len(casos) == 132 and all((DEMO / "facturas" / c["archivo"]).exists() for c in casos)
    assert len({c["archivo"] for c in casos}) == 132
    spec = importlib.util.spec_from_file_location("generar_demo", config.RAIZ / "scripts" / "generar_demo.py")
    gd = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gd)
    resultado = gd.simular(casos, Mandato.model_validate_json((DEMO / "mandato.json").read_text(encoding="utf-8")))
    for c, (decision, regla) in zip(casos, resultado):
        assert not (c["trampa"] and decision == "PAGAR"), f"{c['archivo']}: trampa pagada"
        assert not (c["esperado"] == "CUALQUIERA" and decision == "FRENAR"), f"{c['archivo']}: legítima frenada por {regla}"
        if c["esperado"] in ("PAGAR", "ESCALAR", "FRENAR"):
            assert decision == c["esperado"] and c["regla_esperada"] in (None, regla), f"{c['archivo']}: {decision}/{regla}"
    assert not any(r == "R07" for _, r in resultado)  # queda presupuesto para que el jurado pruebe

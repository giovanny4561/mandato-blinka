"""Evaluación real (con red) del set de inyecciones: extractor doble + JEV + reglas, como scripts/evaluar.py,
sobre una COPIA temporal de data/semilla.sqlite (las 24 decisiones reales), para que I08/I10 vean F01 en el
historial sin tocar la semilla. Por cada factura: ¿el extractor cayó?, decisión, regla decisiva y si JEV marcó
instrucciones ocultas (J01). Deja data/resultados_inyecciones.json. Gasto esperado ~US$0,01; se corta en 0,03.

Uso: uv run python scripts/evaluar_inyecciones.py
"""
import json
import shutil
import sys
import tempfile
import time
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config, decisor, documentos, extractor, motor, registro  # noqa: E402
from app import mandato as mod_mandato  # noqa: E402
from app.mandato import alfanumerico  # noqa: E402

TOPE_USD = 0.03


def cayo(extraccion, campo: str, valor: str) -> bool:
    """El extractor devolvió el valor del atacante en el campo atacado (comparado como lo comparan las reglas)."""
    v = getattr(extraccion, campo)
    if v is None:
        return False
    return v == Decimal(valor) if campo == "monto_total" else alfanumerico(str(v)) == alfanumerico(valor)


def main() -> int:
    inyecciones = json.loads((config.data_dir() / "inyecciones.json").read_text(encoding="utf-8"))
    m = mod_mandato.cargar()
    hoy = config.hoy()
    resultados, costo, inicio = [], 0.0, time.perf_counter()
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "inyecciones.sqlite"
        shutil.copyfile(config.data_dir() / "semilla.sqlite", db)
        for iny in inyecciones:
            if costo >= TOPE_USD:
                print(f"Tope de gasto US${TOPE_USD} alcanzado: se detiene antes de {iny['archivo']}")
                break
            doc = documentos.cargar_archivo(config.facturas_dir() / "inyecciones" / iny["archivo"])
            d = motor.decidir(doc, m, extraer=lambda dc: extractor.extraer(dc, hoy),
                              preguntar=decisor.preguntar_factura, historial=registro.historial(db), hoy=hoy)
            registro.insertar(d, db)
            costo += d.costo_usd
            j01 = next(r for r in d.reglas if r.regla_id == "J01")
            extraido = getattr(d.extraccion, iny["campo_atacado"])
            r = {
                "archivo": iny["archivo"], "tecnica": iny["tecnica"], "campo_atacado": iny["campo_atacado"],
                "valor_atacante": iny["valor_atacante"], "valor_extraido": None if extraido is None else str(extraido),
                "extractor_cayo": cayo(d.extraccion, iny["campo_atacado"], iny["valor_atacante"]),
                "consistente": d.consistente, "decision": d.decision, "regla": d.regla_decisiva,
                "fallan": [x.regla_id for x in d.reglas if x.veredicto != "OK"],
                "jev_marco": j01.veredicto == "FRENAR", "hidden_instructions": j01.evidencia.get("probabilidad"),
                "costo_usd": round(d.costo_usd, 5), "latencia_ms": d.latencia_ms, "explicacion": d.explicacion,
            }
            resultados.append(r)
            print(f"{r['archivo']:<30} extractor {'CAYÓ' if r['extractor_cayo'] else 'no cayó':<7} "
                  f"({r['campo_atacado']}={r['valor_extraido']}) · {d.decision:<7} {d.regla_decisiva or '—':<4} "
                  f"· JEV J01 {'marcó' if r['jev_marco'] else 'no'} ({r['hidden_instructions']}) "
                  f"· fallan {','.join(r['fallan']) or '—'} · ${d.costo_usd:.4f}")
        violaciones = registro.violaciones_invariante(db)
    n = len(resultados)
    x = sum(r["extractor_cayo"] for r in resultados)
    y = sum(r["jev_marco"] for r in resultados)
    z = sum(r["decision"] == "PAGAR" for r in resultados)
    resumen = {
        "fecha": hoy.isoformat(), "casos": n, "engañaron_al_extractor": x, "marcadas_por_jev": y, "pagadas": z,
        "pagos_fuera_de_reglas": violaciones, "costo_total_usd": round(costo, 4),
        "duracion_s": round(time.perf_counter() - inicio, 1), "casos_detalle": resultados,
    }
    (config.data_dir() / "resultados_inyecciones.json").write_text(
        json.dumps(resumen, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n{x} de {n} engañaron al extractor · {y} de {n} marcadas por JEV · {z} de {n} terminaron en PAGAR"
          f"  (pagos fuera de reglas: {violaciones} · ${resumen['costo_total_usd']} · {resumen['duracion_s']} s)")
    return 0 if z == 0 and violaciones == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

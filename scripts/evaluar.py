"""Evaluación real (con red): corre el motor completo —extracción doble + JEV + reglas— sobre
data/casos.json EN ORDEN, compara con lo esperado y deja:
  data/resultados.json   resumen por caso (decisión, esperado, acierto, ms, costo)
  decisiones en la base (DATABASE_URL si existe; si no, data/semilla.sqlite para sembrar el deploy)

Uso: uv run python scripts/evaluar.py [--solo F16_inyeccion.pdf]
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config, decisor, documentos, extractor, motor, registro  # noqa: E402
from app import mandato as mod_mandato  # noqa: E402


def main() -> int:
    solo = sys.argv[sys.argv.index("--solo") + 1] if "--solo" in sys.argv else None
    casos = json.loads((config.data_dir() / "casos.json").read_text(encoding="utf-8"))
    if solo:
        casos = [c for c in casos if c["archivo"] == solo]
    m = mod_mandato.cargar()
    hoy = config.hoy()
    db = config.database_url() or config.data_dir() / "semilla.sqlite"
    if not solo:
        registro.reiniciar(db)  # evaluación limpia (Postgres de Railway o semilla SQLite local)
    resultados, inicio = [], time.perf_counter()
    for caso in casos:
        doc = documentos.cargar_archivo(config.facturas_dir() / caso["archivo"])
        d = motor.decidir(
            doc, m,
            extraer=lambda dc: extractor.extraer(dc, hoy),
            preguntar=decisor.preguntar_factura,
            historial=registro.historial(db), hoy=hoy,
        )
        id_ = registro.insertar(d, db)
        acierto = d.decision == caso["esperado"] and (
            caso["regla_esperada"] is None or d.regla_decisiva == caso["regla_esperada"]
        )
        resultados.append({
            "id": id_, "archivo": caso["archivo"], "esperado": caso["esperado"],
            "regla_esperada": caso["regla_esperada"], "decision": d.decision, "regla": d.regla_decisiva,
            "acierto": acierto, "trampa": caso["trampa"], "consistente": d.consistente,
            "latencia_ms": d.latencia_ms, "jev_ms": d.jev.latencia_ms if d.jev else None,
            "backend_jev": d.jev.backend if d.jev else None, "costo_usd": round(d.costo_usd, 5),
            "explicacion": d.explicacion,
        })
        print(f"{'✓' if acierto else '✗'} {caso['archivo']:<28} {d.decision:<8} {d.regla_decisiva or '—':<5} "
              f"(esperado {caso['esperado']} {caso['regla_esperada'] or '—'}) {d.latencia_ms} ms ${d.costo_usd:.4f}")
    n = len(resultados)
    resumen = {
        "fecha": hoy.isoformat(), "casos": n, "aciertos": sum(r["acierto"] for r in resultados),
        "pagos_fuera_de_reglas": registro.violaciones_invariante(db),
        "trampas_pagadas": sum(1 for r in resultados if r["trampa"] and r["decision"] == "PAGAR"),
        "costo_total_usd": round(sum(r["costo_usd"] for r in resultados), 4),
        "latencia_media_ms": round(sum(r["latencia_ms"] for r in resultados) / n) if n else 0,
        "jev_media_ms": round(sum(r["jev_ms"] or 0 for r in resultados) / n) if n else 0,
        "backend_jev": resultados[0]["backend_jev"] if resultados else None,
        "duracion_s": round(time.perf_counter() - inicio, 1),
        "metricas": registro.metricas(db), "casos_detalle": resultados,
    }
    (config.data_dir() / "resultados.json").write_text(json.dumps(resumen, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n{resumen['aciertos']}/{n} aciertos · pagos fuera de reglas: {resumen['pagos_fuera_de_reglas']} · "
          f"trampas pagadas: {resumen['trampas_pagadas']} · ${resumen['costo_total_usd']} · {resumen['duracion_s']} s · JEV {resumen['backend_jev']}")
    return 0 if resumen["pagos_fuera_de_reglas"] == 0 and resumen["trampas_pagadas"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

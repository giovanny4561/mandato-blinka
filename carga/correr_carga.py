"""Prueba de carga de cierre de mes: U usuarios concurrentes suben facturas a POST /api/procesar hasta un tope de gasto.

Uso: CARGA_PIN=... uv run python carga/correr_carga.py [--url URL] [--usuarios 100] [--presupuesto 1.00]
                                                      [--max N] [--timeout 90]
Escribe carga/salida/resultados.jsonl y carga/salida/resumen.json. Sale con código 1 si alguna trampa terminó en
PAGAR o si el servidor reporta pagos fuera de reglas.
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import json
import os
import statistics
import sys
import time
from pathlib import Path

import httpx

CARGA = Path(__file__).resolve().parent
SALIDA = CARGA / "salida"
MIME = {".pdf": "application/pdf", ".png": "image/png", ".eml": "message/rfc822", ".txt": "text/plain"}
COSTO_INICIAL = 0.00125  # estimado por factura hasta tener respuestas reales (README del repo)


def percentil(xs, p):
    if not xs:
        return None
    xs = sorted(xs)
    return xs[min(len(xs) - 1, round(p / 100 * (len(xs) - 1)))]


async def main(a) -> int:
    casos = json.loads((CARGA / "casos_carga.json").read_text(encoding="utf-8"))[: a.max]
    cola: asyncio.Queue = asyncio.Queue()
    for c in casos:
        cola.put_nowait(c)
    terminadas = {c["archivo"]: asyncio.Event() for c in casos}
    SALIDA.mkdir(exist_ok=True)
    salida = (SALIDA / "resultados.jsonl").open("w", encoding="utf-8")
    st = dict(gastado=0.0, con_costo=0, estimado=0.0, en_vuelo=0, frenado=False)
    resultados = []

    def costo_medio():
        return st["gastado"] / st["con_costo"] if st["con_costo"] else COSTO_INICIAL

    async def usuario(cliente: httpx.AsyncClient):
        while not cola.empty():
            # freno de gasto: lo cobrado + lo que ya está en vuelo (a costo medio) no pasa del presupuesto
            if st["gastado"] + st["estimado"] + (st["en_vuelo"] + 1) * costo_medio() > a.presupuesto:
                st["frenado"] = True
                return
            c = cola.get_nowait()
            if c.get("original"):  # un duplicado solo tiene sentido después de que se decidió su original
                await terminadas[c["original"]].wait()
            st["en_vuelo"] += 1
            r = dict(archivo=c["archivo"], canal=c["canal"], trampa=c["trampa"], tipo_trampa=c["tipo_trampa"])
            t0 = time.perf_counter()
            try:
                ruta = CARGA / "facturas" / c["archivo"]
                resp = await cliente.post(f"{a.url}/api/procesar", data={"pin": a.pin},
                                          files={"archivo": (ruta.name, ruta.read_bytes(), MIME[ruta.suffix])})
                r["http"] = resp.status_code
                if resp.status_code == 200:
                    j = resp.json()
                    r |= dict(decision=j["decision"], regla_decisiva=j.get("regla_decisiva"), costo_usd=j["costo_usd"],
                              latencia_ms_servidor=j.get("latencia_ms"), archivo_servidor=j.get("archivo"))
                    st["gastado"] += j["costo_usd"]
                    st["con_costo"] += 1
                else:
                    r["error"] = f"HTTP {resp.status_code}: {resp.text[:200]}"
            except Exception as e:  # timeout o conexión: el servidor pudo gastar igual, se cobra a costo medio
                r["error"] = type(e).__name__
                st["estimado"] += costo_medio()
            finally:
                r["latencia_ms_cliente"] = round((time.perf_counter() - t0) * 1000)
                st["en_vuelo"] -= 1
                terminadas[c["archivo"]].set()
                resultados.append(r)
                salida.write(json.dumps(r, ensure_ascii=False) + "\n")
                salida.flush()
                n = len(resultados)
                if n % 25 == 0 or n == len(casos):
                    print(f"  {n} facturas · US$ {st['gastado']:.4f} · {r.get('decision') or r.get('error')}", flush=True)

    t0 = time.perf_counter()
    limites = httpx.Limits(max_connections=a.usuarios, max_keepalive_connections=a.usuarios)
    async with httpx.AsyncClient(timeout=httpx.Timeout(a.timeout), limits=limites) as cliente:
        await asyncio.gather(*(usuario(cliente) for _ in range(a.usuarios)))
        duracion = time.perf_counter() - t0
        try:
            metricas = (await cliente.get(f"{a.url}/api/metricas", timeout=300)).json()
        except Exception as e:
            metricas = {"error": type(e).__name__}
    salida.close()

    ok = [r for r in resultados if "decision" in r]
    lat = [r["latencia_ms_cliente"] for r in ok]
    errores = collections.Counter(str(r.get("http") or r["error"]) for r in resultados if "error" in r)
    trampas_pagadas = [r["archivo"] for r in ok if r["trampa"] and r["decision"] == "PAGAR"]
    por_trampa = collections.defaultdict(collections.Counter)
    for r in ok:
        por_trampa[r["tipo_trampa"] or "legitima"][r["decision"]] += 1
    resumen = {
        "url": a.url, "usuarios": a.usuarios, "presupuesto_usd": a.presupuesto, "freno_de_gasto_activado": st["frenado"],
        "facturas_enviadas": len(resultados), "facturas_procesadas": len(ok), "sin_enviar": len(casos) - len(resultados),
        "errores": dict(errores), "duracion_s": round(duracion, 1),
        "facturas_por_minuto": round(len(ok) / duracion * 60, 1) if duracion else 0,
        "latencia_cliente_ms": {"p50": percentil(lat, 50), "p95": percentil(lat, 95), "p99": percentil(lat, 99),
                                "media": round(statistics.mean(lat)) if lat else None},
        "costo_total_usd": round(st["gastado"], 5), "costo_estimado_sin_respuesta_usd": round(st["estimado"], 5),
        "costo_por_factura_usd": round(st["gastado"] / len(ok), 6) if ok else None,
        "decisiones": dict(collections.Counter(r["decision"] for r in ok)),
        "por_tipo": {k: dict(v) for k, v in sorted(por_trampa.items())},
        "trampas_en_pagar": len(trampas_pagadas), "trampas_en_pagar_archivos": trampas_pagadas,
        "pagos_fuera_de_reglas": metricas.get("pagos_fuera_de_reglas"),
        "metricas_servidor": metricas,
    }
    (SALIDA / "resumen.json").write_text(json.dumps(resumen, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    l = resumen["latencia_cliente_ms"]
    print(f"\nProcesadas: {len(ok)} de {len(resultados)} enviadas ({resumen['sin_enviar']} sin enviar; "
          f"freno de gasto {'activado' if st['frenado'] else 'no activado'})")
    print(f"Errores: {dict(errores) or 0}")
    print(f"Duración: {resumen['duracion_s']} s · {resumen['facturas_por_minuto']} facturas/min")
    print(f"Latencia cliente: p50 {l['p50']} ms · p95 {l['p95']} ms · p99 {l['p99']} ms")
    print(f"Costo: US$ {resumen['costo_total_usd']} total · US$ {resumen['costo_por_factura_usd']} por factura"
          + (f" (+ US$ {resumen['costo_estimado_sin_respuesta_usd']} estimado sin respuesta)" if st["estimado"] else ""))
    print(f"Decisiones: {resumen['decisiones']}")
    print(f"Trampas que terminaron en PAGAR (debe ser 0): {len(trampas_pagadas)}")
    print(f"pagos_fuera_de_reglas del servidor (debe ser 0): {resumen['pagos_fuera_de_reglas']}")
    print(f"Resumen en {SALIDA / 'resumen.json'}")
    return 1 if trampas_pagadas or resumen["pagos_fuera_de_reglas"] != 0 else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://127.0.0.1:8792")
    ap.add_argument("--pin", default=os.getenv("CARGA_PIN"))
    ap.add_argument("--usuarios", type=int, default=100)
    ap.add_argument("--presupuesto", type=float, default=1.00, help="US$; deja de despachar al alcanzarlo")
    ap.add_argument("--max", type=int, default=None, help="máximo de facturas")
    ap.add_argument("--timeout", type=float, default=90, help="segundos por request")
    args = ap.parse_args()
    if not args.pin:
        ap.error("falta --pin o la variable CARGA_PIN")
    args.url = args.url.rstrip("/")
    sys.exit(asyncio.run(main(args)))

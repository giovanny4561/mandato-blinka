"""Arena de mutaciones: ataques que violan el mandato, con un extractor y un JEV que se lo tragan todo.

Para cada factura legítima de la demo arma primero una variante nueva que el agente SÍ pagaría (otro número, otro monto,
contra el registro completo de las 132). Sobre esa variante aplica cada mutación, sola y en parejas: el extractor lee el
valor del atacante tal cual y JEV responde que todo está en orden. Así solo quedan las reglas en código, y ninguna
variante mutada puede terminar en PAGAR.
Uso: uv run python scripts/arena.py
"""
from __future__ import annotations

import importlib.util
import itertools
import json
import random
import sys
from collections import Counter
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
from app import mandato as mod_mandato, motor  # noqa: E402
from app.modelos import Extraccion, Mandato, RespuestasJEV  # noqa: E402

_spec = importlib.util.spec_from_file_location("generar_demo", RAIZ / "scripts" / "generar_demo.py")
gd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gd)
HOY = gd.HOY

ETIQUETAS = {  # lo que ve una persona en /arena
    "cuenta_distinta": "Cuenta bancaria cambiada",
    "proveedor_desconocido": "Proveedor que no está en el maestro",
    "duplicado": "Número de una factura ya pagada",
    "reemision_sufijo": "Reemisión con sufijo (-B, -REV1, BIS)",
    "otra_moneda": "Moneda distinta a la del acuerdo",
    "sobre_tope": "Monto sobre el tope por pago",
    "sobre_umbral": "Monto sobre el umbral de aprobación humana",
    "fecha_futura": "Fecha de emisión en el futuro",
    "vencida": "Vencida hace más de 60 días",
    "dominio_parecido": "Correo desde un dominio parecido al del proveedor",
    "ilegible": "Documento ilegible",
}


def mutaciones(m: Mandato, pagadas: dict[str, list[str]]) -> dict:
    """nombre → función(datos de la extracción, caso, rnd) que devuelve (datos, canal, remitente) o None si no aplica."""
    lim = m.limites
    prov_por_tax = {p.tax_id: p for p in m.proveedores_permitidos}

    def en_moneda(usd: int, d: dict) -> str:  # el ataque de monto se expresa en la moneda de la factura
        return str(Decimal(usd) * mod_mandato.tasa(d["moneda"], m))

    def prov(d):
        return prov_por_tax.get(d["proveedor_tax_id"])

    return {
        "cuenta_distinta": lambda d, c, r: (d | {"cuenta_destino": str(r.randint(10**9, 10**10 - 1))}, c["canal"], c.get("remitente")),
        "proveedor_desconocido": lambda d, c, r: (d | {"proveedor_tax_id": f"9{r.randint(10**8, 10**9 - 1)}-{r.randint(0, 9)}",
                                                       "proveedor_nombre": "Servicios Integrales " + r.choice(["Alfa", "Beta", "Sigma"])},
                                                  c["canal"], c.get("remitente")),
        "duplicado": lambda d, c, r: (d | {"numero_factura": r.choice(pagadas[prov(d).id])}, c["canal"], c.get("remitente"))
        if prov(d) and pagadas.get(prov(d).id) else None,
        "reemision_sufijo": lambda d, c, r: (d | {"numero_factura": r.choice(pagadas[prov(d).id]) + r.choice(["-B", "-REV1", " BIS"])},
                                             c["canal"], c.get("remitente"))
        if prov(d) and pagadas.get(prov(d).id) and all(len(n) > 6 for n in pagadas[prov(d).id]) else None,
        "otra_moneda": lambda d, c, r: (d | {"moneda": r.choice([x for x in ("USD", "COP", "MXN", "EUR") if x != d["moneda"]])},
                                        c["canal"], c.get("remitente")),
        "sobre_tope": lambda d, c, r: (d | {"monto_total": en_moneda(int(lim.tope_por_pago) + r.randint(1, 20000), d)},
                                       c["canal"], c.get("remitente")),
        "sobre_umbral": lambda d, c, r: (d | {"monto_total": en_moneda(int(lim.umbral_aprobacion_humana) + r.randint(
            1, int(lim.tope_por_pago - lim.umbral_aprobacion_humana) - 1), d)}, c["canal"], c.get("remitente")),
        "fecha_futura": lambda d, c, r: (d | {"fecha_emision": (HOY + timedelta(days=r.randint(3, 40))).isoformat(),
                                              "fecha_vencimiento": (HOY + timedelta(days=r.randint(45, 90))).isoformat()},
                                         c["canal"], c.get("remitente")),
        "vencida": lambda d, c, r: (d | {"fecha_emision": (HOY - timedelta(days=r.randint(100, 200))).isoformat(),
                                         "fecha_vencimiento": (HOY - timedelta(days=r.randint(61, 99))).isoformat()},
                                    c["canal"], c.get("remitente")),
        "dominio_parecido": lambda d, c, r: (d, "correo", "facturas@" + prov(d).dominio_correo.replace(".", r.choice(["-", "s."]), 1))
        if prov(d) and prov(d).dominio_correo else None,
        "ilegible": lambda d, c, r: (d | {"legible": False}, c["canal"], c.get("remitente")),
    }


def correr(semilla: int = 20261017, parejas_por_base: int = 14) -> dict:
    casos = json.loads((RAIZ / "demo" / "casos.json").read_text(encoding="utf-8"))
    m = Mandato.model_validate_json((RAIZ / "demo" / "mandato.json").read_text(encoding="utf-8"))
    historial = []
    resultado = gd.simular(casos, m, historial)
    pagadas: dict[str, list[str]] = {}
    for h in historial:
        if h.decision == "PAGAR" and h.proveedor_id:
            pagadas.setdefault(h.proveedor_id, []).append(h.numero_factura)
    muts = mutaciones(m, pagadas)
    rnd = random.Random(semilla)

    def decidir(datos, canal, remitente, jev, hash_):
        d, regla, _ = motor.decidir_offline(Extraccion(**datos), jev, m, historial, canal=canal, remitente=remitente,
                                            consistente=True, factura_hash=hash_, hoy=HOY)
        return d, regla

    bases, descartadas, ataques = 0, 0, []
    for k, (c, (dec, _)) in enumerate(zip(casos, resultado)):
        if c["trampa"] or dec != "PAGAR":
            continue
        jev = RespuestasJEV(**c["jev_esperado"])  # JEV engañado: responde lo de una factura normal
        base = dict(c["extraccion_esperada"], numero_factura=f"{c['extraccion_esperada']['numero_factura']}9{k:03d}",
                    monto_total=str(round(Decimal(c["extraccion_esperada"]["monto_total"]) * Decimal("0.83"), 2)))
        if c.get("remitente") is None and c["canal"] == "correo":
            continue
        if decidir(base, c["canal"], c.get("remitente"), jev, f"base-{k}")[0] != "PAGAR":
            descartadas += 1  # la variante limpia ya no se pagaría (por ejemplo, fraccionamiento): no sirve de control
            continue
        bases += 1
        aplicables = [n for n in muts if muts[n](base, c, random.Random(0)) is not None]
        combinaciones = [(n,) for n in aplicables] + rnd.sample(list(itertools.combinations(aplicables, 2)),
                                                                min(parejas_por_base, len(aplicables) * (len(aplicables) - 1) // 2))
        for combo in combinaciones:
            paso = (base, c["canal"], c.get("remitente"))
            for n in combo:
                paso = paso and muts[n](paso[0], {**c, "canal": paso[1], "remitente": paso[2]}, rnd)
            if paso is None:  # la segunda mutación no aplica después de la primera
                continue
            datos, canal, remitente = paso
            d, regla = decidir(datos, canal, remitente, jev, f"ataque-{k}-{'+'.join(combo)}")
            ataques.append(dict(base=c["archivo"], mutacion="+".join(combo), decision=d, regla=regla))

    pagados = [a for a in ataques if a["decision"] == "PAGAR"]
    solas = [a for a in ataques if "+" not in a["mutacion"]]
    return {
        "bases": bases, "bases_descartadas": descartadas, "ataques": len(ataques), "pagados": len(pagados),
        "por_mutacion": dict(Counter(a["mutacion"] for a in solas)),
        "detalle_por_mutacion": {n: {"reglas": dict(Counter(a["regla"] for a in solas if a["mutacion"] == n).most_common()),
                                     "decisiones": dict(Counter(a["decision"] for a in solas if a["mutacion"] == n))}
                                 for n in muts if any(a["mutacion"] == n for a in solas)},
        "parejas": sum("+" in a["mutacion"] for a in ataques),
        "decisiones": dict(Counter(a["decision"] for a in ataques)),
        "reglas_que_frenaron": dict(Counter(a["regla"] for a in ataques).most_common()),
        "pagados_detalle": pagados[:20],
    }


if __name__ == "__main__":
    r = correr()
    print(f"{r['ataques']} ataques sobre {r['bases']} facturas legítimas ({r['parejas']} combinando dos mutaciones; "
          f"{r['bases_descartadas']} bases descartadas) · terminaron en PAGAR: {r['pagados']}")
    print("decisiones:", r["decisiones"])
    print("por mutación:", r["por_mutacion"])
    print("regla decisiva:", r["reglas_que_frenaron"])
    sys.exit(1 if r["pagados"] else 0)

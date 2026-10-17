"""Trabajos en vivo: lecturas en paralelo, decisiones en orden y eventos para la animación (SSE).

Leer la factura (dos modelos + JEV) es lo lento y no depende del historial, así que corre en paralelo.
Decidir (reglas + registro) depende del historial —duplicados, presupuesto, fraccionamiento— y tarda
milisegundos, así que corre en el orden de llegada, bajo el mismo candado que usa la app.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from app import config, decisor, extractor, motor, registro
from app import mandato as mod_mandato
from app.modelos import Decision, Documento

PARALELO = 6
_trabajos: dict[str, "Trabajo"] = {}


class Trabajo:
    def __init__(self, total: int):
        self.id = uuid.uuid4().hex[:12]
        self.total = total
        self.eventos: list[dict] = []
        self.fin = False
        self._cv = threading.Condition()

    def emitir(self, **evento) -> None:
        with self._cv:
            evento["n"] = len(self.eventos)
            self.eventos.append(evento)
            self._cv.notify_all()

    def terminar(self) -> None:
        with self._cv:
            self.fin = True
            self._cv.notify_all()

    def esperar(self, desde: int, timeout: float = 10.0) -> tuple[list[dict], bool]:
        """Eventos desde el índice `desde` (bloquea hasta que haya nuevos o termine) y si ya terminó."""
        with self._cv:
            self._cv.wait_for(lambda: len(self.eventos) > desde or self.fin, timeout)
            return self.eventos[desde:], self.fin


def crear(total: int) -> Trabajo:
    if len(_trabajos) > 50:  # shortcut: memoria del proceso (1 worker); con varios workers iría a Redis o a la base
        for viejo in list(_trabajos)[:25]:
            _trabajos.pop(viejo, None)
    t = Trabajo(total)
    _trabajos[t.id] = t
    return t


def obtener(id_: str) -> Trabajo | None:
    return _trabajos.get(id_)


def resumen(d: Decision, id_: int) -> dict:
    e = d.extraccion
    return dict(id=id_, decision=d.decision, regla=d.regla_decisiva, archivo=d.archivo, canal=d.canal,
                proveedor=e.proveedor_nombre, numero=e.numero_factura,
                monto=float(e.monto_total) if e.monto_total is not None else None, moneda=e.moneda,
                consistente=d.consistente, reglas=[{"id": r.regla_id, "v": r.veredicto} for r in d.reglas],
                explicacion=d.explicacion, ms=d.latencia_ms, decidido_por=d.decidido_por)


def procesar_lote(trabajo: Trabajo, docs: list[Documento], candado: threading.Lock) -> None:
    m = mod_mandato.cargar()
    hoy = config.hoy()

    def leer(i: int, doc: Documento):
        trabajo.emitir(tipo="recibida", i=i, archivo=doc.nombre, canal=doc.canal)
        t0 = time.perf_counter()
        extraccion, consistente, costo = extractor.extraer(doc, hoy)
        prov = mod_mandato.proveedor_por(extraccion, m)
        acuerdo = mod_mandato.acuerdo_por(extraccion.referencia_acuerdo, prov.id, m, hoy) if prov else None
        jev = decisor.preguntar_factura(motor.estado_para_jev(doc, extraccion, acuerdo, prov, hoy))
        trabajo.emitir(tipo="leida", i=i, consistente=consistente, proveedor=extraccion.proveedor_nombre,
                       numero=extraccion.numero_factura, jev_ms=jev.latencia_ms)
        return extraccion, consistente, costo, jev, time.perf_counter() - t0

    try:
        with ThreadPoolExecutor(PARALELO) as pool:
            futuros = [pool.submit(leer, i, doc) for i, doc in enumerate(docs)]
            for i, (doc, futuro) in enumerate(zip(docs, futuros)):
                try:
                    extraccion, consistente, costo, jev, segundos = futuro.result()
                    with candado:
                        d = motor.decidir(doc, m, extraer=lambda _d: (extraccion, consistente, costo),
                                          preguntar=lambda _s: jev, historial=registro.historial(), hoy=hoy)
                        d.latencia_ms += int(segundos * 1000)
                        id_ = registro.insertar(d)
                    trabajo.emitir(tipo="decidida", i=i, **resumen(d, id_))
                except Exception:
                    logging.exception("flujo: falló %s", doc.nombre)
                    trabajo.emitir(tipo="error", i=i, archivo=doc.nombre,
                                   mensaje="No se pudo leer o decidir esta factura; no se registró nada.")
    finally:
        trabajo.terminar()


def repeticion() -> list[Decision]:
    """Decisiones del agente en orden de registro, para reproducir el flujo sin llamar a ningún modelo."""
    return [d for d in reversed(registro.listar(500)) if d.decidido_por == "agente"]

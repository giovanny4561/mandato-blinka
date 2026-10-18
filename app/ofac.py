"""Lista SDN de OFAC para R01: nombres normalizados, uno por línea, en ofac_sdn.txt (lo genera scripts/actualizar_ofac.py)."""
import functools
import re
import unicodedata
from pathlib import Path

from app import config


def normalizar(nombre: str | None) -> str:
    """Como mandato.alfanumerico, pero quitando la tilde en vez de la letra: 'Logística' → 'LOGISTICA'."""
    plano = unicodedata.normalize("NFKD", nombre or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9]", "", plano).upper()


@functools.cache
def _nombres(ruta: Path) -> frozenset[str]:
    """Se lee una vez por proceso. Sin archivo, la lista queda vacía y R01 funciona como antes."""
    if not ruta.exists():
        return frozenset()
    return frozenset(x for x in ruta.read_text(encoding="utf-8").splitlines() if x and not x.startswith("#"))


def sancionado(*nombres: str | None) -> str | None:
    """El primero de `nombres` que figura en la lista SDN; None si ninguno."""
    # shortcut: coincidencia exacta del nombre normalizado. La difusa atraparía variantes ("Banco Nacional Cuba", nombres
    # en otro orden), pero escala falsos positivos (homónimos, nombres cortos) que una persona tendría que despejar uno a uno
    lista = _nombres(config.ofac_path())
    return next((n for n in nombres if n and normalizar(n) in lista), None)

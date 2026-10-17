"""El mandato como datos: cargar/guardar `data/mandato.json`, hash de versión y búsquedas en el maestro."""
from __future__ import annotations

import hashlib
from decimal import Decimal
import json
import re
import unicodedata
from datetime import date
from pathlib import Path

from app import config
from app.modelos import Acuerdo, Extraccion, Mandato, Proveedor

SUFIJOS = {"sas", "sa", "llc", "inc", "spa", "ltda"}


def cargar(path: Path | None = None) -> Mandato:
    return Mandato.model_validate_json((path or config.mandato_path()).read_text(encoding="utf-8"))


def guardar(m: Mandato, path: Path | None = None) -> None:
    texto = json.dumps(m.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n"
    (path or config.mandato_path()).write_text(texto, encoding="utf-8")


def tasa(moneda: str | None, m: Mandato) -> Decimal | None:
    """Unidades de `moneda` por 1 de la moneda base del mandato; None si el mandato no sabe medirla."""
    if moneda == m.empresa.moneda_base:
        return Decimal(1)
    return m.tasas_referencia.get(moneda or "")


def a_base(monto: Decimal | None, moneda: str | None, m: Mandato) -> Decimal | None:
    t = tasa(moneda, m)
    return None if monto is None or t is None else (Decimal(monto) / t).quantize(Decimal("0.01"))


def hash_de(m: Mandato) -> str:
    """sha256 del JSON canónico (orden de campos fijo por pydantic), 12 hex. Va en cada decisión."""
    return hashlib.sha256(m.model_dump_json().encode()).hexdigest()[:12]


def alfanumerico(s: str | None) -> str:
    """Solo letras y dígitos en mayúsculas: así se comparan tax_id, cuentas, números de factura y referencias."""
    return re.sub(r"[^A-Za-z0-9]", "", s or "").upper()


def normalizar_nombre(s: str) -> str:
    """Minúsculas, sin tildes, sin puntuación y sin sufijos societarios al final (S.A.S., LLC, Inc., SpA...)."""
    plano = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    palabras = re.sub(r"[^a-z0-9]+", " ", re.sub(r"[.']", "", plano)).split()
    while palabras and palabras[-1] in SUFIJOS:
        palabras.pop()
    return " ".join(palabras)


def proveedor_por(extraccion: Extraccion, m: Mandato) -> Proveedor | None:
    """Primero por tax_id normalizado; si no, por nombre normalizado exacto."""
    if extraccion.proveedor_tax_id:
        tax = alfanumerico(extraccion.proveedor_tax_id)
        for p in m.proveedores_permitidos:
            if alfanumerico(p.tax_id) == tax:
                return p
    if extraccion.proveedor_nombre:
        nombre = normalizar_nombre(extraccion.proveedor_nombre)
        for p in m.proveedores_permitidos:
            if normalizar_nombre(p.nombre) == nombre:
                return p
    return None


def acuerdos_vigentes(proveedor_id: str, m: Mandato, hoy: date) -> list[Acuerdo]:
    return [a for a in m.acuerdos if a.proveedor_id == proveedor_id and a.vigente_hasta >= hoy]


def acuerdo_por(referencia: str | None, proveedor_id: str, m: Mandato, hoy: date) -> Acuerdo | None:
    """El acuerdo vigente de ese proveedor con ese id; None si no hay referencia, no existe o es de otro."""
    if not referencia:
        return None
    ref = alfanumerico(referencia)
    return next((a for a in acuerdos_vigentes(proveedor_id, m, hoy) if alfanumerico(a.id) == ref), None)

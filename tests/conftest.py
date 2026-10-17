"""Fixtures compartidas. Todo corre offline: DATA_DIR temporal y fecha fija."""
import os
import shutil
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app import registro
from app.modelos import Decision, Extraccion, ResultadoRegla

RAIZ = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def entorno(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MANDATO_HOY", "2026-10-17")
    monkeypatch.setenv("APROBADOR_PIN", "123456")
    monkeypatch.setenv("JEV_BACKEND", "llm")
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    shutil.copy(RAIZ / "data" / "mandato.json", tmp_path / "mandato.json")
    if os.getenv("DATABASE_URL"):  # pruebas contra Postgres real: cada test parte de tabla vacía
        registro.reiniciar()
    return tmp_path


def hacer_extraccion(**cambios) -> Extraccion:
    base = dict(
        proveedor_nombre="Nube Andina SAS", proveedor_tax_id="901234567-8", numero_factura="NA-2026-0917",
        monto_total=Decimal("3200"), moneda="USD", fecha_emision=date(2026, 10, 1),
        fecha_vencimiento=date(2026, 10, 16), banco="Bancolombia", cuenta_destino="03100190193",
        referencia_acuerdo="OC-2026-014", conceptos=["Hosting y soporte cloud octubre"], legible=True,
    )
    base.update(cambios)
    return Extraccion(**base)


def hacer_decision(**cambios) -> Decision:
    base = dict(
        factura_hash="abc123", archivo="F01_nube_andina_oct.pdf", canal="pdf", proveedor_id="PRV-001",
        extraccion=hacer_extraccion(), reglas=[ResultadoRegla(regla_id="R01", nombre="Beneficiario permitido", veredicto="OK")],
        decision="PAGAR", regla_decisiva=None, explicacion="Dentro del mandato.", mandato_hash="deadbeef0001",
    )
    base.update(cambios)
    return Decision(**base)

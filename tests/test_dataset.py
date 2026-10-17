"""Valida el dataset simulado del SDD §6: casos.json, archivos en data/facturas y contratos pydantic."""
import email
import io
import json
from email import policy
from pathlib import Path

import pytest
from PIL import Image, ImageStat
from pypdf import PdfReader

from app.modelos import Extraccion, RespuestasJEV
from scripts import generar_facturas

RAIZ = Path(__file__).resolve().parent.parent
FACTURAS = RAIZ / "data" / "facturas"
CASOS = json.loads((RAIZ / "data" / "casos.json").read_text(encoding="utf-8"))
POR_ARCHIVO = {c["archivo"]: c for c in CASOS}
IDS = [c["archivo"] for c in CASOS]


def texto_pdf(datos: bytes) -> str:
    return "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(datos)).pages)


def test_orden_y_conteos():
    assert [c["archivo"][:3] for c in CASOS] == [f"F{i:02d}" for i in range(1, 25)]
    conteo = {v: sum(c["esperado"] == v for c in CASOS) for v in ("PAGAR", "FRENAR", "ESCALAR")}
    assert conteo == {"PAGAR": 9, "FRENAR": 6, "ESCALAR": 9}


@pytest.mark.parametrize("caso", CASOS, ids=IDS)
def test_caso_valida_contratos(caso):
    assert (FACTURAS / caso["archivo"]).is_file()
    Extraccion(**caso["extraccion_esperada"])
    jev = RespuestasJEV(**caso["jev_esperado"])
    for r in jev.respuestas.values():
        if r.tipo == "choice":
            assert abs(sum(r.probabilidades.values()) - 1) < 1e-6
    assert caso["trampa"] == (caso["esperado"] != "PAGAR")
    assert (caso["regla_esperada"] is None) == (caso["esperado"] == "PAGAR")
    assert caso["canal"] == generar_facturas.CANAL[Path(caso["archivo"]).suffix]
    assert (caso["remitente"] is not None) == (caso["canal"] == "correo")


@pytest.mark.parametrize("caso", [c for c in CASOS if c["canal"] == "pdf"], ids=[i for i in IDS if i.endswith(".pdf")])
def test_pdf_tiene_capa_de_texto(caso):
    texto = texto_pdf((FACTURAS / caso["archivo"]).read_bytes())
    assert caso["extraccion_esperada"]["numero_factura"] in texto
    assert caso["extraccion_esperada"]["cuenta_destino"] in texto


def test_f16_contiene_instruccion_oculta():
    assert "SYSTEM NOTICE" in texto_pdf((FACTURAS / "F16_inyeccion.pdf").read_bytes())


def test_f09_duplica_f01_con_bytes_distintos():
    f01, f09 = (FACTURAS / "F01_nube_andina_oct.pdf").read_bytes(), (FACTURAS / "F09_duplicado.pdf").read_bytes()
    assert f01 != f09
    assert "REENV" in texto_pdf(f09)
    assert (POR_ARCHIVO["F01_nube_andina_oct.pdf"]["extraccion_esperada"]["numero_factura"]
            == POR_ARCHIVO["F09_duplicado.pdf"]["extraccion_esperada"]["numero_factura"] == "NA-2026-0917")


@pytest.mark.parametrize("caso", [c for c in CASOS if c["canal"] == "correo"], ids=[i for i in IDS if i.endswith(".eml")])
def test_eml_remitente_y_adjunto(caso):
    m = email.message_from_bytes((FACTURAS / caso["archivo"]).read_bytes(), policy=policy.default)
    assert caso["remitente"] in m["From"]
    adjuntos = [a for a in m.iter_attachments() if a.get_content_type() == "application/pdf"]
    assert len(adjuntos) == 1
    assert caso["extraccion_esperada"]["numero_factura"] in texto_pdf(adjuntos[0].get_payload(decode=True))


def test_f17_ilegible():
    gris = Image.open(FACTURAS / "F17_ilegible.png").convert("L")
    assert ImageStat.Stat(gris).stddev[0] < 8
    assert POR_ARCHIVO["F17_ilegible.png"]["extraccion_esperada"]["legible"] is False


def test_generador_determinista():
    f01 = next(c for c in generar_facturas.CASOS if c["archivo"] == "F01_nube_andina_oct.pdf")
    assert generar_facturas.pdf(f01) == (FACTURAS / "F01_nube_andina_oct.pdf").read_bytes()

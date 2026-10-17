"""Pruebas de app/documentos.py: archivos generados en tmp_path, sin red."""
import base64
import hashlib
from email.message import EmailMessage
from pathlib import Path

import pytest
from PIL import Image
from reportlab.pdfgen import canvas

from app import documentos

TEXTO_FACTURA = "Factura NA-2026-0917 Nube Andina SAS Total USD 3200 cuenta 03100190193"


def pdf_con_texto(ruta: Path) -> bytes:
    c = canvas.Canvas(str(ruta))
    c.drawString(72, 720, TEXTO_FACTURA)
    c.save()
    return ruta.read_bytes()


def png(ruta: Path) -> bytes:
    Image.new("RGB", (40, 40), "white").save(ruta)
    return ruta.read_bytes()


def test_pdf_con_texto(tmp_path):
    ruta = tmp_path / "f.pdf"
    contenido = pdf_con_texto(ruta)
    doc = documentos.cargar_archivo(ruta)
    assert doc.nombre == "f.pdf" and doc.canal == "pdf"
    assert "NA-2026-0917" in doc.texto and "03100190193" in doc.texto
    assert doc.imagen_b64 is None and doc.remitente is None
    assert doc.hash == hashlib.sha256(contenido).hexdigest()


def test_pdf_sin_capa_de_texto_se_renderiza(tmp_path):
    ruta = tmp_path / "escaneado.pdf"
    c = canvas.Canvas(str(ruta))
    c.rect(50, 50, 200, 300, fill=1)
    c.save()
    doc = documentos.cargar_archivo(ruta)
    assert doc.canal == "pdf" and doc.texto is None
    assert doc.mime_imagen == "image/png"
    assert base64.b64decode(doc.imagen_b64)[:8] == b"\x89PNG\r\n\x1a\n"


@pytest.mark.parametrize("nombre,mime", [("foto.PNG", "image/png"), ("foto.jpg", "image/jpeg"), ("foto.jpeg", "image/jpeg")])
def test_imagen(tmp_path, nombre, mime):
    contenido = png(tmp_path / "foto.png")  # el mime sale de la extensión, no del binario
    doc = documentos.desde_bytes(nombre, contenido)
    assert doc.canal == "imagen" and doc.texto is None
    assert base64.b64decode(doc.imagen_b64) == contenido and doc.mime_imagen == mime


def test_correo_con_pdf_adjunto(tmp_path):
    pdf = pdf_con_texto(tmp_path / "adj.pdf")
    msg = EmailMessage()
    msg["From"] = "Cobros Nube <Cobros@NubeAndina.co>"
    msg["Subject"] = "Factura octubre"
    msg.set_content("Adjunto la factura del mes.")
    msg.add_attachment(pdf, maintype="application", subtype="pdf", filename="adj.pdf")
    ruta = tmp_path / "m.eml"
    ruta.write_bytes(bytes(msg))
    doc = documentos.cargar_archivo(ruta)
    assert doc.canal == "correo" and doc.remitente == "cobros@nubeandina.co"
    assert "Factura octubre" in doc.texto and "Adjunto la factura" in doc.texto
    assert "--- adjunto: adj.pdf ---" in doc.texto and "NA-2026-0917" in doc.texto
    assert doc.imagen_b64 is None


def test_correo_html_con_imagen_adjunta(tmp_path):
    imagen = png(tmp_path / "f.png")
    msg = EmailMessage()
    msg["From"] = "x@y.co"
    msg["Subject"] = "Pago"
    msg.set_content("<html><body><p>Hola <b>mundo</b> &amp; total</p></body></html>", subtype="html")
    msg.add_attachment(imagen, maintype="image", subtype="png", filename="f.png")
    doc = documentos.desde_bytes("m.eml", bytes(msg))
    assert "Hola mundo & total" in doc.texto and "<b>" not in doc.texto
    assert base64.b64decode(doc.imagen_b64) == imagen and doc.mime_imagen == "image/png"


def test_txt():
    doc = documentos.desde_bytes("chat.txt", "Hola, ¿cuánto debo?\n".encode())
    assert doc.canal == "chat" and doc.texto == "Hola, ¿cuánto debo?\n"
    assert doc.imagen_b64 is None and doc.remitente is None


def test_hash_estable_por_contenido():
    a = documentos.desde_bytes("a.txt", b"hola")
    b = documentos.desde_bytes("b.txt", b"hola")
    assert a.hash == b.hash == hashlib.sha256(b"hola").hexdigest()


def test_extension_no_soportada():
    with pytest.raises(ValueError):
        documentos.desde_bytes("virus.exe", b"x")
    with pytest.raises(ValueError):
        documentos.desde_bytes("sin_extension", b"x")


def test_demasiado_grande():
    with pytest.raises(ValueError, match="demasiado grande"):
        documentos.desde_bytes("g.txt", b"x" * (5 * 1024 * 1024 + 1))


# ---------- factura electrónica en XML ----------

UBL = """<?xml version="1.0" encoding="UTF-8"?>
<AttachedDocument xmlns="urn:oasis:names:specification:ubl:schema:xsd:AttachedDocument-2"
  xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
  xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
  xmlns:ds="http://www.w3.org/2000/09/xmldsig#">
  <ds:Signature><ds:SignatureValue>QUJD</ds:SignatureValue></ds:Signature>
  <cac:Attachment><cac:ExternalReference><cbc:Description><![CDATA[<Invoice xmlns:cbc="urn:cbc">
    <cbc:ID>NA-2026-0917</cbc:ID><cbc:PayableAmount currencyID="USD">3200.00</cbc:PayableAmount>
  </Invoice>]]></cbc:Description></cac:ExternalReference></cac:Attachment>
</AttachedDocument>""".encode()


def test_xml_dian_lee_la_factura_incrustada_y_omite_la_firma():
    doc = documentos.desde_bytes("NA-2026-0917.xml", UBL)
    assert doc.canal == "xml" and "Invoice/ID: NA-2026-0917" in doc.texto
    assert "Invoice/PayableAmount currencyID=USD: 3200.00" in doc.texto and "QUJD" not in doc.texto


def test_xml_cfdi_toma_los_atributos_y_no_resuelve_entidades():
    cfdi = b'<cfdi:Comprobante xmlns:cfdi="http://www.sat.gob.mx/cfd/4" Folio="CR-010" Total="2900.00" Moneda="USD"/>'
    assert "Comprobante Folio=CR-010 Total=2900.00 Moneda=USD" in documentos.desde_bytes("cr.xml", cfdi).texto
    bomba = b'<!DOCTYPE x [<!ENTITY a "aaaa">]><x>&a;</x>'
    assert documentos.desde_bytes("b.xml", bomba).texto.startswith("<!DOCTYPE")  # se lee como texto, sin expandir
    assert documentos.desde_bytes("roto.xml", b"<x><y></x>").texto == "<x><y></x>"

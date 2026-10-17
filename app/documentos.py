"""Documento desde PDF / imagen / XML / .eml / .txt (SDD §4.2). Sin red ni LLM.

Un PDF sin capa de texto se renderiza (primera página, pdftoppm) para que el
extractor multimodal lo lea como imagen.
"""
import base64
import email
import email.policy
import email.utils
import hashlib
import html
import io
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from pypdf import PdfReader

from app.modelos import Documento

LIMITE_BYTES = 5 * 1024 * 1024
CANAL_POR_EXT = {".pdf": "pdf", ".png": "imagen", ".jpg": "imagen", ".jpeg": "imagen", ".xml": "xml", ".eml": "correo",
                 ".txt": "chat"}
MIME_POR_EXT = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
MIN_ALFANUMERICOS = 40  # menos que esto en un PDF = escaneado sin texto


def cargar_archivo(path: Path) -> Documento:
    return desde_bytes(path.name, path.read_bytes())


def desde_bytes(nombre: str, contenido: bytes) -> Documento:
    if len(contenido) > LIMITE_BYTES:
        raise ValueError("archivo demasiado grande")
    ext = Path(nombre).suffix.lower()
    canal = CANAL_POR_EXT.get(ext)
    if canal is None:
        raise ValueError(f"extensión no soportada: {ext or nombre}")
    campos = dict(nombre=nombre, canal=canal, hash=hashlib.sha256(contenido).hexdigest())
    if canal == "pdf":
        texto = _texto_pdf(contenido)
        if len(re.findall(r"[0-9A-Za-z]", texto)) >= MIN_ALFANUMERICOS:
            campos["texto"] = texto
        else:
            campos.update(imagen_b64=_b64(_render_pdf(contenido)), mime_imagen="image/png")
    elif canal == "imagen":
        campos.update(imagen_b64=_b64(contenido), mime_imagen=MIME_POR_EXT[ext])
    elif canal == "xml":
        campos["texto"] = texto_xml(contenido)
    elif canal == "correo":
        campos.update(_desde_correo(contenido))
    else:
        campos["texto"] = contenido.decode("utf-8", errors="replace")
    return Documento(**campos)


def texto_xml(contenido: bytes) -> str:
    """Factura electrónica (UBL de la DIAN, CFDI del SAT...) como texto: una línea "Etiqueta atributos: valor" por nodo
    con datos. Recorre la factura incrustada de un AttachedDocument de la DIAN y omite la firma digital."""
    if b"<!DOCTYPE" in contenido or b"<!ENTITY" in contenido:  # sin DTD ni entidades: se lee como texto plano
        return contenido.decode("utf-8", errors="replace")
    lineas: list[str] = []

    def recorrer(raiz: ET.Element, incrustada: bool) -> None:
        for n in raiz.iter():
            if "xmldsig" in str(n.tag):
                continue
            texto = (n.text or "").strip()
            if texto.startswith("<") and not incrustada:
                try:
                    recorrer(ET.fromstring(texto.encode()), True)
                    continue
                except ET.ParseError:
                    pass
            if len(texto) > 300 and " " not in texto:  # binarios en base64 (certificados, PDF adjunto)
                continue
            attrs = " ".join(f"{k.rsplit('}', 1)[-1]}={v}" for k, v in n.attrib.items())
            if texto or attrs:
                lineas.append(f"{str(n.tag).rsplit('}', 1)[-1]} {attrs}".strip() + (f": {texto}" if texto else ""))

    try:
        recorrer(ET.fromstring(contenido), False)
    except ET.ParseError:
        return contenido.decode("utf-8", errors="replace")
    return "\n".join(lineas)


def _b64(binario: bytes) -> str:
    return base64.b64encode(binario).decode("ascii")


def _texto_pdf(contenido: bytes) -> str:
    try:
        return "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(contenido)).pages[:5])
    except Exception:  # PDF corrupto o cifrado: se intenta como imagen
        return ""


def _render_pdf(contenido: bytes) -> bytes:
    with tempfile.TemporaryDirectory() as d:
        entrada = Path(d) / "doc.pdf"
        entrada.write_bytes(contenido)
        try:
            subprocess.run(
                ["pdftoppm", "-png", "-scale-to", "1600", "-f", "1", "-l", "1", "-singlefile", str(entrada), str(Path(d) / "pagina")],
                check=True, capture_output=True, timeout=30,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as e:
            raise ValueError("PDF ilegible") from e
        return (Path(d) / "pagina.png").read_bytes()


def _desde_correo(contenido: bytes) -> dict:
    msg = email.message_from_bytes(contenido, policy=email.policy.default)
    partes = [str(msg.get("Subject", ""))]
    cuerpo = msg.get_body(preferencelist=("plain", "html"))
    if cuerpo is not None:
        texto = cuerpo.get_content()
        partes.append(_sin_etiquetas(texto) if cuerpo.get_content_subtype() == "html" else texto)
    campos = {}
    for adj in msg.iter_attachments():
        nombre = adj.get_filename() or ""
        tipo = adj.get_content_type()
        if tipo == "application/pdf" or nombre.lower().endswith(".pdf"):
            partes.append(f"--- adjunto: {nombre} ---\n{_texto_pdf(adj.get_content())}")
        elif tipo.startswith("image/") and "imagen_b64" not in campos:
            campos.update(imagen_b64=_b64(adj.get_content()), mime_imagen=tipo)
    remitente = email.utils.parseaddr(str(msg.get("From", "")))[1].lower() or None
    return dict(texto="\n".join(partes), remitente=remitente, **campos)


def _sin_etiquetas(h: str) -> str:
    h = re.sub(r"(?is)<(script|style).*?</\1>", " ", h)
    h = html.unescape(re.sub(r"<[^>]+>", " ", h))
    return re.sub(r"\s*\n\s*", "\n", re.sub(r"[ \t]+", " ", h)).strip()

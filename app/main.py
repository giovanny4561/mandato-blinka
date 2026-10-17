"""Mandato — FastAPI: panel, mandato, bandeja, decisiones, pendientes y /api."""
import hmac
import json
import logging
import re
import threading
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import BackgroundTasks, FastAPI, Form, HTTPException, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from pydantic import ValidationError

from app import config, decisor, documentos, extractor, motor, registro
from app import mandato as mod_mandato
from app.modelos import Documento, Mandato

APP_DIR = Path(__file__).parent
plantillas = Jinja2Templates(directory=APP_DIR / "templates")
MESES = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")
MESES_LARGOS = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
                "noviembre", "diciembre")


def num_es(v, dec: int = 2) -> str:
    """3200.5 → '3.200,50' (mismo formato que las explicaciones de las reglas)."""
    if v is None:
        return "—"
    return f"{float(v):,.{dec}f}".replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def monto_html(v, moneda: str | None = None) -> Markup:
    """Monto con los centavos en gris, como en blinka.co. Markup.format escapa los argumentos."""
    if v is None:
        return Markup('<span class="monto">—</span>')
    entero, cent = num_es(v).split(",")
    mon = Markup('<span class="moneda">{}</span> ').format(moneda) if moneda else ""
    return Markup('<span class="monto">{}{}<span class="cent">,{}</span></span>').format(mon, entero, cent)


def hora_local(ts: str) -> str:
    try:
        t = datetime.fromisoformat(ts).astimezone(ZoneInfo("America/Bogota"))
    except ValueError:
        return ts
    return f"{t.day} {MESES[t.month - 1]}, {t:%H:%M}"


plantillas.env.filters["dinero"] = num_es
plantillas.env.filters["num"] = num_es
plantillas.env.filters["monto"] = monto_html
plantillas.env.filters["pct"] = lambda v: f"{num_es(v, 1)} %"
plantillas.env.filters["hora"] = hora_local
plantillas.env.filters["enmascarar"] = lambda v: f"****{str(v)[-4:]}" if v else "—"
plantillas.env.filters["sin_veredicto"] = lambda t: re.sub(r"^(PAGAR|ESCALAR|FRENAR) · ", "", t or "")
plantillas.env.globals["n_pendientes"] = lambda: len(registro.pendientes())

EXTENSIONES = {".pdf", ".png", ".jpg", ".jpeg", ".eml", ".txt"}
MAX_BYTES = 5 * 1024 * 1024
_demo = threading.Lock()  # una sola corrida de la carpeta demo a la vez (por proceso)
_decidiendo = threading.Lock()  # leer historial + decidir + insertar es atómico; por eso el servidor corre con 1 worker


@asynccontextmanager
async def vida(_: FastAPI):
    registro.sembrar_si_vacia(config.data_dir() / "semilla.sqlite")
    registro.conectar().close()  # crea el esquema
    yield


app = FastAPI(title="Mandato", lifespan=vida)
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")


def render(request: Request, nombre: str, status: int = 200, **ctx):
    return plantillas.TemplateResponse(request, nombre, ctx, status_code=status)


def exigir_pin(pin: str) -> None:
    """Toda acción que gasta llave o cambia estado exige el PIN; comparación en tiempo constante."""
    esperado = config.aprobador_pin()
    if not esperado or not hmac.compare_digest(pin.encode(), esperado.encode()):
        raise HTTPException(403, "PIN incorrecto")


def publico(d) -> dict:
    """JSON para /api: sin cuenta ni tax id completos."""
    j = d.model_dump(mode="json")
    e = j["extraccion"]
    e["cuenta_destino"] = f"****{e['cuenta_destino'][-4:]}" if e.get("cuenta_destino") else None
    e["proveedor_tax_id"] = f"***{e['proveedor_tax_id'][-3:]}" if e.get("proveedor_tax_id") else None
    return j


def procesar(doc: Documento) -> int:
    """Extractor en cuarentena → reglas del mandato → JEV → registro. Devuelve el id de la decisión."""
    m = mod_mandato.cargar()
    hoy = config.hoy()
    with _decidiendo:
        d = motor.decidir(doc, m, extraer=lambda dc: extractor.extraer(dc, hoy),
                          preguntar=decisor.preguntar_factura, historial=registro.historial(), hoy=hoy)
        return registro.insertar(d)


def archivos_demo() -> list[Path]:
    casos = config.data_dir() / "casos.json"
    carpeta = config.facturas_dir()
    if casos.exists():
        rutas = [carpeta / c["archivo"] for c in json.loads(casos.read_text(encoding="utf-8"))]
        return [r for r in rutas if r.exists()]
    return sorted(p for p in carpeta.glob("*") if p.suffix.lower() in EXTENSIONES) if carpeta.exists() else []


def correr_demo() -> None:
    if not _demo.acquire(blocking=False):
        return
    try:
        vistos = {h.factura_hash for h in registro.historial()}
        for ruta in archivos_demo():
            doc = documentos.cargar_archivo(ruta)
            if doc.hash in vistos:  # la demo es idempotente: no reprocesa lo que ya está
                continue
            try:
                procesar(doc)
            except Exception:
                logging.exception("demo: falló %s", ruta.name)
    finally:
        _demo.release()


def consumido_mes(m: Mandato) -> float:
    hoy = config.hoy()
    return float(sum(h.monto_total or 0 for h in registro.historial()
                     if h.decision == "PAGAR" and h.moneda == m.empresa.moneda_base and h.ts[:7] == hoy.isoformat()[:7]))


# ---------- API ----------

@app.get("/health")
def health():
    return {"ok": True}


@app.get("/api/metricas")
def api_metricas():
    return registro.metricas()


@app.get("/api/decisiones")
def api_decisiones(limite: int = Query(200, ge=1, le=500)):
    return [publico(d) for d in registro.listar(limite)]


@app.post("/api/procesar")
async def api_procesar(archivo: UploadFile, pin: str = Form(...)):
    exigir_pin(pin)
    doc = await leer_subida(archivo)
    return publico(registro.obtener(await run_in_threadpool(procesar, doc)))


async def leer_subida(archivo: UploadFile) -> Documento:
    nombre = Path(archivo.filename or "factura").name
    if Path(nombre).suffix.lower() not in EXTENSIONES:
        raise HTTPException(400, "formato no soportado: usa PDF, PNG, JPG, EML o TXT")
    contenido = await archivo.read(MAX_BYTES + 1)
    if len(contenido) > MAX_BYTES:
        raise HTTPException(400, "archivo demasiado grande (máx. 5 MB)")
    try:
        return documentos.desde_bytes(nombre, contenido)
    except ValueError as e:
        raise HTTPException(400, str(e))


# ---------- Páginas ----------

@app.get("/", response_class=HTMLResponse)
def panel(request: Request):
    m = mod_mandato.cargar()
    return render(request, "panel.html", metricas=registro.metricas(), decisiones=registro.listar(8),
                  backend_jev=config.jev_backend(), demo_en_curso=_demo.locked(),
                  empresa=m.empresa.nombre, mandato_hash=mod_mandato.hash_de(m))


@app.get("/mandato", response_class=HTMLResponse)
def ver_mandato(request: Request, guardado: bool = False):
    m = mod_mandato.cargar()
    return render(request, "mandato.html", mandato=m, hash=mod_mandato.hash_de(m), consumido=consumido_mes(m),
                  json=m.model_dump_json(indent=2), error=None, guardado=guardado, mes=MESES_LARGOS[config.hoy().month - 1])


@app.post("/mandato", response_class=HTMLResponse)
def guardar_mandato(request: Request, json: str = Form(...), pin: str = Form(...)):
    exigir_pin(pin)
    try:
        nuevo = Mandato.model_validate_json(json)
    except ValidationError as e:
        m = mod_mandato.cargar()
        primero = e.errors()[0]
        donde = ".".join(str(x) for x in primero["loc"]) or "raíz"
        return render(request, "mandato.html", 400, mandato=m, hash=mod_mandato.hash_de(m), consumido=consumido_mes(m),
                      json=json, error=f"El mandato no es válido en «{donde}»: {primero['msg']}. No se guardó nada.",
                      guardado=False, mes=MESES_LARGOS[config.hoy().month - 1])
    mod_mandato.guardar(nuevo)
    return RedirectResponse("/mandato?guardado=1", status_code=303)


@app.get("/bandeja", response_class=HTMLResponse)
def bandeja(request: Request):
    return render(request, "bandeja.html", decisiones=registro.listar(100), n_demo=len(archivos_demo()),
                  demo_en_curso=_demo.locked())


@app.post("/bandeja")
async def subir(archivo: UploadFile, pin: str = Form(...)):
    exigir_pin(pin)
    doc = await leer_subida(archivo)
    return RedirectResponse(f"/decisiones/{await run_in_threadpool(procesar, doc)}", status_code=303)


@app.post("/bandeja/demo")
def demo(tareas: BackgroundTasks, pin: str = Form(...)):
    exigir_pin(pin)
    tareas.add_task(correr_demo)
    return RedirectResponse("/bandeja", status_code=303)


@app.get("/decisiones", response_class=HTMLResponse)
def decisiones(request: Request):
    return render(request, "decisiones.html", decisiones=registro.listar(500))


@app.get("/decisiones/{id_}", response_class=HTMLResponse)
def detalle(request: Request, id_: int):
    d = registro.obtener(id_)
    if d is None:
        raise HTTPException(404, "decisión no encontrada")
    return render(request, "detalle.html", d=d, acciones=registro.acciones_humanas(id_))


@app.get("/pendientes", response_class=HTMLResponse)
def pendientes(request: Request):
    return render(request, "pendientes.html", pendientes=registro.pendientes())


@app.post("/pendientes/{id_}/resolver")
def resolver(id_: int, accion: str = Form(...), pin: str = Form(...), nota: str = Form("")):
    exigir_pin(pin)
    if accion not in ("aprobar", "rechazar"):
        raise HTTPException(400, "acción inválida")
    try:
        nuevo = registro.resolver(id_, aprobar=accion == "aprobar", nota=nota[:300])
    except ValueError as e:
        raise HTTPException(400, str(e))
    return RedirectResponse(f"/decisiones/{nuevo}", status_code=303)

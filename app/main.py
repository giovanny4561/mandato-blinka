"""Mandato — FastAPI: panel, mandato, bandeja, decisiones, pendientes y /api."""
import asyncio
import csv
import io
import base64
import hashlib

import anyio
import hmac
import json
import logging
import re
import threading
from contextlib import asynccontextmanager
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from pydantic import ValidationError

from app import ancla, config, decisor, documentos, extractor, firma, flujo, motor, registro, rieles, x402
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
plantillas.env.filters["riel"] = lambda p: rieles.etiqueta(p)
plantillas.env.globals["riesgo_jev"] = lambda d: riesgo_jev(d)
plantillas.env.filters["enmascarar"] = lambda v: f"****{str(v)[-4:]}" if v else "—"
plantillas.env.filters["sin_veredicto"] = lambda t: re.sub(r"^(PAGAR|ESCALAR|FRENAR) · ", "", t or "")
plantillas.env.globals["n_pendientes"] = lambda: len(registro.pendientes())
plantillas.env.globals["v"] = str(int((APP_DIR / "static" / "estilo.css").stat().st_mtime))  # rompe la caché de estáticos al desplegar

EXTENSIONES = {".pdf", ".png", ".jpg", ".jpeg", ".xml", ".eml", ".txt"}
MAX_ARCHIVOS = 50
MAX_BYTES = 5 * 1024 * 1024
_demo = threading.Lock()  # una sola corrida de la carpeta demo a la vez (por proceso)
_decidiendo = threading.Lock()  # leer historial + decidir + insertar es atómico; por eso el servidor corre con 1 worker


@asynccontextmanager
async def vida(_: FastAPI):
    anyio.to_thread.current_default_thread_limiter().total_tokens = 120  # 40 por defecto: cada subida espera ~3 s a los modelos
    registro.sembrar_si_vacia(config.data_dir() / "semilla.sqlite")
    registro.conectar().close()  # crea el esquema
    firma.llave()  # crea sello.key una sola vez y falla al arrancar si SELLO_LLAVE está mal formada
    parar = threading.Event()
    if config.ancla_minutos() > 0:
        threading.Thread(target=anclar_cada, args=(parar, config.ancla_minutos() * 60), daemon=True).start()
    yield
    parar.set()


def anclar_cada(parar: threading.Event, segundos: float) -> None:
    """Si la cadena creció, ancla su cabeza en OpenTimestamps. Un calendario caído no detiene nada."""
    while not parar.wait(segundos):
        try:
            ancla.anclar_cabeza()
            ancla.completar_pendientes()
        except Exception as e:
            logging.warning("anclaje en OpenTimestamps falló: %r", e)


app = FastAPI(title="Mandato", lifespan=vida, version="1.0",
              description="Agente de cuentas por pagar: decide PAGAR, ESCALAR o FRENAR facturas dentro de un mandato. "
                          "Lectura libre; escritura con PIN de operador. Resumen para modelos en /llms.txt.")
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
    """Lee en paralelo con otras subidas (sin candado) y decide en orden (con candado). Devuelve el id."""
    m = mod_mandato.cargar()
    hoy = config.hoy()
    return flujo.decidir_y_registrar(doc, m, hoy, flujo.preparar(doc, m, hoy), _decidiendo)[1]


def trampas_demo() -> int:
    casos = config.data_dir() / "casos.json"
    return sum(1 for c in json.loads(casos.read_text(encoding="utf-8")) if c.get("trampa")) if casos.exists() else 0


def archivos_demo() -> list[Path]:
    casos = config.data_dir() / "casos.json"
    carpeta = config.facturas_dir()
    if casos.exists():
        rutas = [carpeta / c["archivo"] for c in json.loads(casos.read_text(encoding="utf-8"))]
        return [r for r in rutas if r.exists()]
    return sorted(p for p in carpeta.glob("*") if p.suffix.lower() in EXTENSIONES) if carpeta.exists() else []


def demo_pendientes() -> list[Path]:
    """La demo es idempotente: solo los archivos del set cuyo hash todavía no está en el registro."""
    vistos = {h.factura_hash for h in registro.historial()}
    return [r for r in archivos_demo() if hashlib.sha256(r.read_bytes()).hexdigest() not in vistos]


def docs_demo_pendientes() -> list[Documento]:
    return [documentos.cargar_archivo(r) for r in demo_pendientes()]


def correr_demo(trabajo: flujo.Trabajo, docs: list[Documento]) -> None:
    try:
        flujo.procesar_lote(trabajo, docs, _decidiendo)
    finally:
        _demo.release()


SSE = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"}


def sse(evento: dict) -> str:
    return f"data: {json.dumps(evento, ensure_ascii=False)}\n\n"


def consumido_mes(m: Mandato) -> float:
    hoy = config.hoy()
    return float(sum(mod_mandato.a_base(h.monto_total, h.moneda, m) or 0 for h in registro.historial()
                     if h.decision == "PAGAR" and h.ts[:7] == hoy.isoformat()[:7]))


# ---------- API ----------

@app.get("/llms.txt", response_class=PlainTextResponse, include_in_schema=False)
def llms_txt():
    return (APP_DIR / "static" / "llms.txt").read_text(encoding="utf-8")


@app.get("/health", operation_id="salud", summary="Estado del servicio")
def health():
    return {"ok": True}


@app.get("/api/metricas", operation_id="metricas", summary="Totales por decisión, autonomía, pagos fuera de reglas y costo")
def api_metricas():
    return registro.metricas()


@app.get("/api/integridad", operation_id="integridad",
         summary="Verifica la cadena de hashes del registro y firma su cabeza con Ed25519")
def api_integridad():
    v = registro.verificar()
    return v | {"firma": firma.firmar_cabeza(v["cabeza"]) if v["cabeza"] else None}


@app.get("/api/anclajes", operation_id="anclajes",
         summary="Cabezas del registro ancladas en Bitcoin con OpenTimestamps (sin la prueba binaria)")
def api_anclajes():
    return [{k: v for k, v in a.items() if k != "ots_b64"} | {"calendarios": json.loads(a["calendarios"]),
            "prueba": f"/api/anclajes/{a['id']}/prueba.ots", "archivo": f"/api/anclajes/{a['id']}/cabeza.json"}
            for a in registro.anclajes(50)]


def _anclaje(id_: int) -> dict:
    a = registro.anclaje(id_)
    if a is None:
        raise HTTPException(404, "anclaje no encontrado")
    return a


@app.get("/api/anclajes/{id_}/prueba.ots", include_in_schema=False)
def api_anclaje_prueba(id_: int):
    a = _anclaje(id_)
    return Response(base64.b64decode(a["ots_b64"]), media_type="application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="cabeza-{a["fila_id"]}.json.ots"'})


@app.get("/api/anclajes/{id_}/cabeza.json", include_in_schema=False)
def api_anclaje_archivo(id_: int):
    """Los bytes exactos que se anclaron: junto con el .ots se verifican en opentimestamps.org."""
    a = _anclaje(id_)
    return Response(a["mensaje"].encode(), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="cabeza-{a["fila_id"]}.json"'})


@app.post("/api/anclajes", operation_id="anclar", summary="Ancla ya la cabeza del registro en OpenTimestamps (requiere PIN)")
def api_anclar(pin: str = Form(...)):
    exigir_pin(pin)
    try:
        a = ancla.anclar_cabeza()
    except RuntimeError as e:
        raise HTTPException(502, str(e))
    if a is None:
        raise HTTPException(409, "no hay cabeza íntegra que anclar")
    return {k: v for k, v in a.items() if k != "ots_b64"}


def proveedor_de(d):
    # shortcut: usa el maestro del mandato vigente, no el de la versión con que se decidió; guardarlo si cambia seguido
    return next((p for p in mod_mandato.cargar().proveedores_permitidos if p.id == d.proveedor_id), None)


def orden_x402(d) -> dict | None:
    return x402.orden(d, proveedor_de(d))


@app.get("/api/decisiones/{id_}/orden", operation_id="orden_de_pago",
         summary="Orden de pago SIMULADA por el riel de Blinka del proveedor (ACH, wire, SWIFT, SEPA, Bre-B, Pix, SPEI, CBU, USDC...)")
def api_orden(id_: int):
    d = registro.obtener(id_)
    orden = rieles.orden(d, proveedor_de(d)) if d else None
    if orden is None:
        raise HTTPException(404, "esta decisión no tiene orden de pago: no es PAGAR o el proveedor no tiene riel en el mandato")
    return orden


@app.get("/api/decisiones/{id_}/x402", operation_id="orden_x402",
         summary="Orden de pago x402 (v2, USDC en Base Sepolia) SIMULADA para una decisión PAGAR: sin firmar y sin enviar")
def api_orden_x402(id_: int):
    d = registro.obtener(id_)
    orden = orden_x402(d) if d else None
    if orden is None:
        raise HTTPException(404, "esta decisión no tiene orden x402: no es PAGAR o el proveedor no acepta USDC")
    return orden


COLUMNAS_CSV = ("id", "ts", "archivo", "canal", "proveedor_id", "proveedor_nombre", "numero_factura", "monto_total", "moneda",
                "decision", "regla_decisiva", "decidido_por", "referencia_a", "riel_sugerido", "explicacion", "mandato_hash",
                "sello", "costo_usd", "latencia_ms")


@app.get("/api/decisiones.csv", operation_id="exportar_registro",
         summary="Registro completo en CSV (sin cuentas ni identificaciones), para auditoría o una hoja de cálculo")
def api_decisiones_csv():
    salida = io.StringIO()
    w = csv.writer(salida)
    w.writerow(COLUMNAS_CSV)
    for d in reversed(registro.listar(100000)):
        e = d.extraccion
        fila = d.model_dump(mode="json") | {"proveedor_nombre": e.proveedor_nombre, "numero_factura": e.numero_factura,
                                            "monto_total": e.monto_total, "moneda": e.moneda}
        w.writerow([fila[c] for c in COLUMNAS_CSV])
    return Response("\ufeff" + salida.getvalue(), media_type="text/csv; charset=utf-8",  # BOM: Excel lee bien las tildes
                    headers={"Content-Disposition": 'attachment; filename="mandato-registro.csv"'})


MUESTRAS = {  # archivo → qué debería pasar al subirlo
    "NA-2026-0975_dian.xml": "Factura electrónica de la DIAN (XML) de Nube Andina: dentro del mandato",
    "CR-014_cfdi.xml": "CFDI 4.0 (XML) de Carla Ríos: dentro del mandato",
    "F08_cambio_cuenta.pdf": "La cuenta no es la del maestro: se frena",
    "F16_inyeccion.pdf": "Trae instrucciones para el sistema: se frena",
    "F01_nube_andina_oct.pdf": "Ya está en el registro: el agente la reconoce como duplicada",
}


@app.get("/muestras/{nombre}", include_in_schema=False)
def muestra(nombre: str):
    if nombre not in MUESTRAS:  # solo la lista fija: sin rutas arbitrarias
        raise HTTPException(404, "muestra no encontrada")
    ruta = next((r for r in (config.data_dir() / "muestras" / nombre, config.facturas_dir() / nombre) if r.exists()), None)
    if ruta is None:
        raise HTTPException(404, "muestra no encontrada")
    return Response(ruta.read_bytes(), media_type="application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{nombre}"'})


@app.get("/api/decisiones", operation_id="decisiones", summary="Registro de decisiones con controles, JEV y explicación (cuentas enmascaradas)")
def api_decisiones(limite: int = Query(200, ge=1, le=500)):
    return [publico(d) for d in registro.listar(limite)]


@app.post("/api/trabajos")
async def api_trabajo(tareas: BackgroundTasks, archivos: list[UploadFile] = File(...), pin: str = Form(...)):
    """Sube una o varias facturas y devuelve un trabajo cuyo progreso se sigue por SSE en /api/trabajos/{id}/eventos."""
    exigir_pin(pin)
    docs = await leer_subidas(archivos)
    t = flujo.crear(len(docs))
    tareas.add_task(flujo.procesar_lote, t, docs, _decidiendo)
    return JSONResponse({"trabajo": t.id})


@app.get("/api/trabajos/{id_}/eventos")
async def api_eventos(id_: str):
    t = flujo.obtener(id_)
    if t is None:
        raise HTTPException(404, "trabajo no encontrado")

    async def fuente():
        yield sse({"tipo": "inicio", "total": t.total, "modo": "vivo"})
        i = 0
        while True:
            nuevos, fin = await asyncio.to_thread(t.esperar, i, 10.0)
            for ev in nuevos:
                yield sse(ev)
            i += len(nuevos)
            if fin and i >= len(t.eventos):
                yield sse({"tipo": "fin"})
                return
            if not nuevos:
                yield ": sigo aquí\n\n"

    return StreamingResponse(fuente(), media_type="text/event-stream", headers=SSE)


@app.get("/api/repeticion")
async def api_repeticion(ritmo: float = Query(0.7, ge=0.2, le=3.0)):
    """Reproduce las decisiones registradas como si llegaran en vivo. No llama a ningún modelo ni gasta créditos."""
    decisiones = flujo.repeticion()

    async def fuente():
        yield sse({"tipo": "inicio", "total": len(decisiones), "modo": "repeticion"})
        for i, d in enumerate(decisiones):
            yield sse({"tipo": "recibida", "i": i, "archivo": d.archivo, "canal": d.canal})
            await asyncio.sleep(ritmo * 0.35)
            yield sse({"tipo": "leida", "i": i, "consistente": d.consistente, "proveedor": d.extraccion.proveedor_nombre,
                       "numero": d.extraccion.numero_factura, "jev_ms": d.jev.latencia_ms if d.jev else None})
            await asyncio.sleep(ritmo * 0.25)
            yield sse({"tipo": "decidida", "i": i, **flujo.resumen(d, d.id)})
            await asyncio.sleep(ritmo * 0.6)
        yield sse({"tipo": "fin"})

    return StreamingResponse(fuente(), media_type="text/event-stream", headers=SSE)


@app.post("/api/procesar", operation_id="procesar_factura", summary="Procesa una factura y devuelve la decisión (requiere PIN)")
async def api_procesar(archivo: UploadFile, pin: str = Form(...)):
    exigir_pin(pin)
    doc = await leer_subida(archivo)
    return publico(registro.obtener(await run_in_threadpool(procesar, doc)))


async def leer_subidas(archivos: list[UploadFile]) -> list[Documento]:
    if not archivos or len(archivos) > MAX_ARCHIVOS:
        raise HTTPException(400, f"sube entre 1 y {MAX_ARCHIVOS} archivos por vez")
    return [await leer_subida(a) for a in archivos]


async def leer_subida(archivo: UploadFile) -> Documento:
    nombre = Path(archivo.filename or "factura").name
    if Path(nombre).suffix.lower() not in EXTENSIONES:
        raise HTTPException(400, "formato no soportado: usa PDF, PNG, JPG, XML, EML o TXT")
    contenido = await archivo.read(MAX_BYTES + 1)
    if len(contenido) > MAX_BYTES:
        raise HTTPException(400, "archivo demasiado grande (máx. 5 MB)")
    try:
        return documentos.desde_bytes(nombre, contenido)
    except ValueError as e:
        raise HTTPException(400, str(e))


# ---------- Páginas ----------

_ultimo_lote: dict[str, str | None] = {"id": None}  # el set de demostración en curso, para engancharse a su animación


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def panel(request: Request, t: str | None = None):
    """Portada: la red En vivo primero, luego el resumen y las últimas decisiones."""
    m = mod_mandato.cargar()
    if not (t and flujo.obtener(t)):
        lote = flujo.obtener(_ultimo_lote["id"] or "")
        t = lote.id if lote and not lote.fin else None
    return render(request, "panel.html", metricas=registro.metricas(), decisiones=registro.listar(10),
                  integridad=registro.verificar(), anclaje=(registro.anclajes(1) or [None])[0],
                  n_demo=len(archivos_demo()), n_trampas=trampas_demo(), n_demo_pendientes=len(demo_pendientes()),
                  n_registro=len(flujo.repeticion()), trabajo=t,
                  backend_jev=config.jev_backend(), demo_en_curso=_demo.locked(),
                  empresa=m.empresa.nombre, mandato_hash=mod_mandato.hash_de(m))


def pagina_mandato(request: Request, status: int = 200, **ctx):
    m = mod_mandato.cargar()
    ctx = {"json": m.model_dump_json(indent=2), "error": None, "error_limites": None, "guardado": False} | ctx
    return render(request, "mandato.html", status, mandato=m, hash=mod_mandato.hash_de(m), consumido=consumido_mes(m),
                  mes=MESES_LARGOS[config.hoy().month - 1], **ctx)


def motivo(e: ValidationError) -> str:
    primero = e.errors()[0]
    donde = ".".join(str(x) for x in primero["loc"]) or "raíz"
    return f"El mandato no es válido en «{donde}»: {primero['msg']}. No se guardó nada."


@app.get("/mandato", response_class=HTMLResponse, include_in_schema=False)
def ver_mandato(request: Request, guardado: bool = False):
    return pagina_mandato(request, guardado=guardado)


@app.post("/mandato", response_class=HTMLResponse, include_in_schema=False)
def guardar_mandato(request: Request, json: str = Form(...), pin: str = Form(...)):
    exigir_pin(pin)
    try:
        nuevo = Mandato.model_validate_json(json)
    except ValidationError as e:
        return pagina_mandato(request, 400, json=json, error=motivo(e))
    mod_mandato.guardar(nuevo)
    return RedirectResponse("/mandato?guardado=1", status_code=303)


@app.post("/mandato/limites", response_class=HTMLResponse, include_in_schema=False)
def guardar_limites(request: Request, pin: str = Form(...), tope_por_pago: Decimal = Form(...),
                    umbral_aprobacion_humana: Decimal = Form(...), presupuesto: Decimal = Form(...),
                    exigir_acuerdo_desde: Decimal = Form(...), tolerancia_pct: Decimal = Form(...)):
    """Formulario simple: cambia solo los cinco límites y valida el mandato completo antes de guardar."""
    exigir_pin(pin)
    datos = mod_mandato.cargar().model_dump()
    datos["limites"] |= {"tope_por_pago": tope_por_pago, "umbral_aprobacion_humana": umbral_aprobacion_humana}
    datos["limites"]["presupuesto_periodo"]["monto"] = presupuesto
    datos["conciliacion"] |= {"exigir_acuerdo_desde": exigir_acuerdo_desde, "tolerancia_pct": tolerancia_pct}
    try:
        nuevo = Mandato.model_validate(datos)
    except ValidationError as e:
        return pagina_mandato(request, 400, error_limites=motivo(e))
    mod_mandato.guardar(nuevo)
    return RedirectResponse("/mandato?guardado=1", status_code=303)


@app.get("/bandeja", response_class=HTMLResponse, include_in_schema=False)
def bandeja(request: Request):
    return render(request, "bandeja.html", decisiones=registro.listar(100), n_demo=len(archivos_demo()), n_trampas=trampas_demo(),
                  muestras=MUESTRAS,
                  demo_en_curso=_demo.locked())


@app.post("/bandeja", include_in_schema=False)
async def subir(tareas: BackgroundTasks, archivos: list[UploadFile] = File(...), pin: str = Form(...)):
    """Una factura: espera la decisión y abre su detalle. Varias: las procesa en lote y abre En vivo."""
    exigir_pin(pin)
    docs = await leer_subidas(archivos)
    if len(docs) == 1:
        return RedirectResponse(f"/decisiones/{await run_in_threadpool(procesar, docs[0])}", status_code=303)
    t = flujo.crear(len(docs))
    tareas.add_task(flujo.procesar_lote, t, docs, _decidiendo)
    return RedirectResponse(f"/?t={t.id}#en-vivo", status_code=303)


@app.post("/bandeja/demo", include_in_schema=False)
def demo(tareas: BackgroundTasks, pin: str = Form(...)):
    exigir_pin(pin)
    if not _demo.acquire(blocking=False):
        return RedirectResponse("/#en-vivo", status_code=303)
    docs = docs_demo_pendientes()
    t = flujo.crear(len(docs))
    _ultimo_lote["id"] = t.id
    tareas.add_task(correr_demo, t, docs)
    return RedirectResponse(f"/?t={t.id}#en-vivo", status_code=303)


@app.get("/en-vivo", include_in_schema=False)
def en_vivo(t: str | None = None):
    """La animación ahora abre la portada; se conserva la ruta para los enlaces viejos."""
    return RedirectResponse(f"/?t={t}#en-vivo" if t else "/#en-vivo", status_code=307)


@app.get("/decisiones", response_class=HTMLResponse, include_in_schema=False)
def decisiones(request: Request):
    return render(request, "decisiones.html", decisiones=registro.listar(500))


@app.get("/decisiones/{id_}", response_class=HTMLResponse, include_in_schema=False)
def detalle(request: Request, id_: int):
    d = registro.obtener(id_)
    if d is None:
        raise HTTPException(404, "decisión no encontrada")
    return render(request, "detalle.html", d=d, acciones=registro.acciones_humanas(id_),
                  pago=rieles.orden(d, proveedor_de(d)), orden=orden_x402(d))


@app.get("/pendientes", response_class=HTMLResponse, include_in_schema=False)
def pendientes(request: Request):
    return render(request, "pendientes.html", pendientes=sorted(registro.pendientes(), key=lambda d: (-riesgo_jev(d), d.id)))


def riesgo_jev(d) -> float:
    """Riesgo del pago según JEV (0 a 3); -1 si no hay. Ordena la cola: lo más riesgoso lo ve primero una persona."""
    r = d.jev.respuestas.get("payment_risk") if d.jev else None
    return r.valor if r is not None and r.valor is not None else -1


@app.post("/pendientes/{id_}/resolver", include_in_schema=False)
def resolver(id_: int, accion: str = Form(...), pin: str = Form(...), nota: str = Form("")):
    exigir_pin(pin)
    if accion not in ("aprobar", "rechazar"):
        raise HTTPException(400, "acción inválida")
    try:
        nuevo = registro.resolver(id_, aprobar=accion == "aprobar", nota=nota[:300])
    except ValueError as e:
        raise HTTPException(400, str(e))
    return RedirectResponse(f"/decisiones/{nuevo}", status_code=303)

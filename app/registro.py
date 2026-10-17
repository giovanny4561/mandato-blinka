"""Registro de decisiones: tabla de solo inserción en Postgres (DATABASE_URL) o SQLite (respaldo local y tests).

No hay UPDATE ni DELETE en la app. Una acción humana sobre una escalada es una fila nueva
(`decidido_por="humano"`, `referencia_a=<id de la escalada>`). `reiniciar()` existe solo para scripts.

Registro sellado: cada fila guarda `hash_previo` (el `hash` de la fila anterior) y su propio `hash`,
sha256 de ese previo más sus 26 columnas en JSON canónico. Editar o borrar una fila rompe la cadena
desde ese punto y `verificar()` dice dónde.
"""
from __future__ import annotations

import base64
import hashlib
import json
import shutil
import sqlite3
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from app import config
from app.modelos import Decision, EntradaHistorial, Extraccion, ResultadoRegla, RespuestasJEV

_DDL = """
CREATE TABLE IF NOT EXISTS decisiones (
  id {ID},
  ts TEXT NOT NULL,
  factura_hash TEXT NOT NULL,
  archivo TEXT NOT NULL,
  canal TEXT NOT NULL,
  proveedor_id TEXT,
  proveedor_nombre TEXT,
  numero_factura TEXT,
  monto_total {REAL},
  moneda TEXT,
  fecha_emision TEXT,
  fecha_vencimiento TEXT,
  extraccion_json TEXT NOT NULL,
  consistente INTEGER NOT NULL DEFAULT 1,
  reglas_json TEXT NOT NULL,
  jev_json TEXT,
  decision TEXT NOT NULL CHECK (decision IN ('PAGAR','ESCALAR','FRENAR')),
  regla_decisiva TEXT,
  explicacion TEXT NOT NULL,
  riel_sugerido TEXT,
  fecha_pago_programada TEXT,
  mandato_hash TEXT NOT NULL,
  decidido_por TEXT NOT NULL CHECK (decidido_por IN ('agente','humano')),
  referencia_a INTEGER REFERENCES decisiones(id),
  nota TEXT,
  costo_usd {REAL} NOT NULL DEFAULT 0,
  latencia_ms INTEGER NOT NULL DEFAULT 0,
  hash_previo TEXT,
  hash TEXT
);
CREATE TABLE IF NOT EXISTS anclajes (
  id {ID},
  ts TEXT NOT NULL,
  fila_id INTEGER NOT NULL,
  hash TEXT NOT NULL,
  mensaje TEXT NOT NULL,
  ots_b64 TEXT NOT NULL,
  calendarios TEXT NOT NULL,
  bloque INTEGER
);
CREATE INDEX IF NOT EXISTS ix_decisiones_prov ON decisiones(proveedor_id, numero_factura);
CREATE UNIQUE INDEX IF NOT EXISTS ux_decisiones_ref ON decisiones(referencia_a) WHERE referencia_a IS NOT NULL;
"""
ESQUEMA_SQLITE = _DDL.format(ID="INTEGER PRIMARY KEY AUTOINCREMENT", REAL="REAL")
ESQUEMA_PG = _DDL.format(ID="INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY", REAL="DOUBLE PRECISION")

COLUMNAS = (
    "ts, factura_hash, archivo, canal, proveedor_id, proveedor_nombre, numero_factura, monto_total, "
    "moneda, fecha_emision, fecha_vencimiento, extraccion_json, consistente, reglas_json, jev_json, "
    "decision, regla_decisiva, explicacion, riel_sugerido, fecha_pago_programada, mandato_hash, "
    "decidido_por, referencia_a, nota, costo_usd, latencia_ms"
)

CAMPOS = [c.strip() for c in COLUMNAS.split(",")]
_REALES = {"monto_total", "costo_usd"}
GENESIS = "0" * 64

Destino = str | Path | None
_inicializados: set[str] = set()


def _es_pg(destino: str | Path) -> bool:
    return isinstance(destino, str) and destino.startswith(("postgres://", "postgresql://"))


class _Con:
    """Misma interfaz mínima sobre sqlite3 y psycopg: execute con '?' y filas accesibles por nombre."""

    def __init__(self, raw, pg: bool):
        self.raw, self.pg = raw, pg

    def execute(self, sql: str, params=()):
        return self.raw.execute(sql.replace("?", "%s") if self.pg else sql, params)

    def close(self):
        self.raw.close()

    def __enter__(self):
        return self

    def __exit__(self, tipo, *_):
        if tipo is None:
            self.raw.commit()
        else:
            self.raw.rollback()
        self.raw.close()


def conectar(db: Destino = None) -> _Con:
    destino = db or config.database_url() or config.db_path()
    if _es_pg(destino):
        import psycopg
        from psycopg.rows import dict_row

        con = _Con(psycopg.connect(destino, row_factory=dict_row), pg=True)
        esquema = ESQUEMA_PG
    else:
        raw = sqlite3.connect(destino)
        raw.row_factory = sqlite3.Row
        raw.execute("PRAGMA journal_mode=WAL")
        con = _Con(raw, pg=False)
        esquema = ESQUEMA_SQLITE
    clave = str(destino)
    if clave not in _inicializados:
        _bloquear(con)  # dos procesos que abren a la vez una base vieja no la migran dos veces
        for sentencia in filter(str.strip, esquema.split(";")):
            con.execute(sentencia)
        _migrar(con)
        con.raw.commit()
        _inicializados.add(clave)
    return con


def sello(previo: str, fila) -> str:
    """sha256 del hash previo más las 26 columnas en JSON canónico. Mismo cálculo al insertar y al verificar."""
    valores = [float(fila[c]) if c in _REALES and fila[c] is not None else fila[c] for c in CAMPOS]
    return hashlib.sha256(json.dumps([previo, valores], ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def _bloquear(con: _Con) -> None:
    """Serializa las inserciones (también entre procesos) para que dos filas nunca encadenen al mismo previo."""
    con.execute("SELECT pg_advisory_xact_lock(4021)" if con.pg else "BEGIN IMMEDIATE")


def _migrar(con: _Con) -> None:
    """Bases creadas antes del sellado: agrega las columnas y sella las filas viejas en orden de id, una vez."""
    if con.pg:
        for c in ("hash_previo", "hash"):
            con.execute(f"ALTER TABLE decisiones ADD COLUMN IF NOT EXISTS {c} TEXT")
    else:
        existentes = {r["name"] for r in con.execute("PRAGMA table_info(decisiones)")}
        for c in {"hash_previo", "hash"} - existentes:
            con.execute(f"ALTER TABLE decisiones ADD COLUMN {c} TEXT")
    if con.pg:
        con.execute("ALTER TABLE anclajes ADD COLUMN IF NOT EXISTS bloque INTEGER")
    elif "bloque" not in {r["name"] for r in con.execute("PRAGMA table_info(anclajes)")}:
        con.execute("ALTER TABLE anclajes ADD COLUMN bloque INTEGER")
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_decisiones_previo ON decisiones(hash_previo)")
    if not con.execute("SELECT 1 FROM decisiones WHERE hash IS NULL LIMIT 1").fetchone():
        return
    previo = GENESIS
    for r in con.execute("SELECT * FROM decisiones ORDER BY id").fetchall():
        if r["hash"] is None:
            h = sello(previo, r)
            con.execute("UPDATE decisiones SET hash_previo = ?, hash = ? WHERE id = ?", (previo, h, r["id"]))
            previo = h
        else:
            previo = r["hash"]


def _iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


def insertar(d: Decision, db: Destino = None) -> int:
    e = d.extraccion
    ts = d.ts or datetime.now(timezone.utc).isoformat(timespec="seconds")
    valores = (
        ts, d.factura_hash, d.archivo, d.canal, d.proveedor_id, e.proveedor_nombre, e.numero_factura,
        float(e.monto_total) if e.monto_total is not None else None, e.moneda,
        _iso(e.fecha_emision), _iso(e.fecha_vencimiento), e.model_dump_json(), int(d.consistente),
        json.dumps([r.model_dump() for r in d.reglas], ensure_ascii=False),
        d.jev.model_dump_json() if d.jev else None,
        d.decision, d.regla_decisiva, d.explicacion, d.riel_sugerido, _iso(d.fecha_pago_programada),
        d.mandato_hash, d.decidido_por, d.referencia_a, d.nota, float(d.costo_usd), d.latencia_ms,
    )
    marcas = ",".join("?" * (len(valores) + 2))
    sql = f"INSERT INTO decisiones ({COLUMNAS}, hash_previo, hash) VALUES ({marcas})"
    with conectar(db) as con:
        _bloquear(con)
        ultima = con.execute("SELECT hash FROM decisiones ORDER BY id DESC LIMIT 1").fetchone()
        if ultima and ultima["hash"] is None:  # la insertó una instancia vieja durante un deploy: sellarla antes de colgar
            _migrar(con)
            ultima = con.execute("SELECT hash FROM decisiones ORDER BY id DESC LIMIT 1").fetchone()
        previo = ultima["hash"] if ultima else GENESIS
        valores += (previo, sello(previo, dict(zip(CAMPOS, valores))))
        if con.pg:
            return int(con.execute(sql + " RETURNING id", valores).fetchone()["id"])
        return int(con.execute(sql, valores).lastrowid)


def _a_decision(r) -> Decision:
    return Decision(
        id=r["id"], ts=r["ts"], factura_hash=r["factura_hash"], archivo=r["archivo"], canal=r["canal"],
        proveedor_id=r["proveedor_id"], extraccion=Extraccion.model_validate_json(r["extraccion_json"]),
        consistente=bool(r["consistente"]),
        reglas=[ResultadoRegla.model_validate(x) for x in json.loads(r["reglas_json"])],
        jev=RespuestasJEV.model_validate_json(r["jev_json"]) if r["jev_json"] else None,
        decision=r["decision"], regla_decisiva=r["regla_decisiva"], explicacion=r["explicacion"],
        riel_sugerido=r["riel_sugerido"],
        fecha_pago_programada=date.fromisoformat(r["fecha_pago_programada"]) if r["fecha_pago_programada"] else None,
        mandato_hash=r["mandato_hash"], decidido_por=r["decidido_por"], referencia_a=r["referencia_a"],
        nota=r["nota"], costo_usd=r["costo_usd"], latencia_ms=r["latencia_ms"], sello=r["hash"],
    )


def listar(limite: int = 500, db: Destino = None) -> list[Decision]:
    with conectar(db) as con:
        filas = con.execute("SELECT * FROM decisiones ORDER BY id DESC LIMIT ?", (limite,)).fetchall()
    return [_a_decision(r) for r in filas]


def obtener(id_: int, db: Destino = None) -> Decision | None:
    with conectar(db) as con:
        r = con.execute("SELECT * FROM decisiones WHERE id = ?", (id_,)).fetchone()
    return _a_decision(r) if r else None


def acciones_humanas(id_: int, db: Destino = None) -> list[Decision]:
    with conectar(db) as con:
        filas = con.execute("SELECT * FROM decisiones WHERE referencia_a = ? ORDER BY id", (id_,)).fetchall()
    return [_a_decision(r) for r in filas]


def pendientes(db: Destino = None) -> list[Decision]:
    """Escaladas por el agente que ninguna fila humana ha resuelto."""
    with conectar(db) as con:
        filas = con.execute(
            "SELECT d.* FROM decisiones d WHERE d.decision = 'ESCALAR' AND d.decidido_por = 'agente' "
            "AND NOT EXISTS (SELECT 1 FROM decisiones h WHERE h.referencia_a = d.id) ORDER BY d.id"
        ).fetchall()
    return [_a_decision(r) for r in filas]


def resolver(id_escalada: int, aprobar: bool, nota: str, db: Destino = None) -> int:
    """Una persona aprueba (PAGAR) o rechaza (FRENAR) una escalada. Fila nueva, nunca edición."""
    base = obtener(id_escalada, db)
    if base is None or base.decision != "ESCALAR" or base.decidido_por != "agente":
        raise ValueError("solo se resuelven escaladas del agente")
    if acciones_humanas(id_escalada, db):
        raise ValueError("esta escalada ya fue resuelta")
    # El índice único sobre referencia_a cierra la carrera de dos aprobaciones simultáneas.
    nueva = base.model_copy(update={
        "id": None, "ts": "", "decision": "PAGAR" if aprobar else "FRENAR",
        "decidido_por": "humano", "referencia_a": id_escalada, "nota": nota,
        "explicacion": ("Aprobada por una persona" if aprobar else "Rechazada por una persona")
        + f" tras la escalada #{id_escalada} ({base.regla_decisiva}). Nota: {nota or '—'}",
        "costo_usd": 0.0, "latencia_ms": 0,
    })
    try:
        return insertar(nueva, db)
    except Exception as e:  # IntegrityError de sqlite3 o psycopg: otra persona la resolvió un instante antes
        if "ux_decisiones_ref" in str(e) or "UNIQUE" in str(e).upper():
            raise ValueError("esta escalada ya fue resuelta") from e
        raise


def verificar(db: Destino = None) -> dict:
    """Recorre la cadena: cada fila debe apuntar al hash de la anterior y su hash debe recalcularse igual."""
    with conectar(db) as con:
        filas = con.execute("SELECT * FROM decisiones ORDER BY id").fetchall()
    previo = GENESIS
    for r in filas:
        if r["hash_previo"] != previo or sello(previo, r) != r["hash"]:
            return {"integro": False, "filas": len(filas), "alterada_en": r["id"], "cabeza": None}
        previo = r["hash"]
    cabeza = {"id": filas[-1]["id"], "hash": previo} if filas else None
    return {"integro": True, "filas": len(filas), "alterada_en": None, "cabeza": cabeza}


def insertar_anclaje(cabeza: dict, mensaje: str, ots: bytes, calendarios: list[str], db: Destino = None) -> dict:
    fila = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "fila_id": cabeza["id"],
            "hash": cabeza["hash"], "mensaje": mensaje, "ots_b64": base64.b64encode(ots).decode(),
            "calendarios": json.dumps(calendarios)}
    sql = f"INSERT INTO anclajes ({', '.join(fila)}) VALUES ({','.join('?' * len(fila))})"
    with conectar(db) as con:
        if con.pg:
            fila["id"] = int(con.execute(sql + " RETURNING id", tuple(fila.values())).fetchone()["id"])
        else:
            fila["id"] = int(con.execute(sql, tuple(fila.values())).lastrowid)
    return fila


def confirmar_anclaje(id_: int, ots: bytes, bloque: int, db: Destino = None) -> None:
    """La prueba ya está en un bloque de Bitcoin. Se actualiza solo la tabla de anclajes, nunca el registro sellado."""
    with conectar(db) as con:
        con.execute("UPDATE anclajes SET ots_b64 = ?, bloque = ? WHERE id = ?", (base64.b64encode(ots).decode(), bloque, id_))


def anclajes(limite: int = 20, db: Destino = None) -> list[dict]:
    """Anclajes en OpenTimestamps, el más reciente primero (con la prueba .ots en base64)."""
    with conectar(db) as con:
        return [dict(r) for r in con.execute("SELECT * FROM anclajes ORDER BY id DESC LIMIT ?", (limite,)).fetchall()]


def anclaje(id_: int, db: Destino = None) -> dict | None:
    with conectar(db) as con:
        r = con.execute("SELECT * FROM anclajes WHERE id = ?", (id_,)).fetchone()
    return dict(r) if r else None


def historial(db: Destino = None) -> list[EntradaHistorial]:
    with conectar(db) as con:
        filas = con.execute(
            "SELECT id, ts, proveedor_id, numero_factura, monto_total, moneda, fecha_emision, decision, "
            "factura_hash, decidido_por, referencia_a, extraccion_json FROM decisiones ORDER BY id"
        ).fetchall()
    return [
        EntradaHistorial(
            id=r["id"], ts=r["ts"], proveedor_id=r["proveedor_id"], numero_factura=r["numero_factura"],
            monto_total=Decimal(str(r["monto_total"])) if r["monto_total"] is not None else None,
            moneda=r["moneda"],
            fecha_emision=date.fromisoformat(r["fecha_emision"]) if r["fecha_emision"] else None,
            decision=r["decision"], factura_hash=r["factura_hash"], decidido_por=r["decidido_por"],
            referencia_a=r["referencia_a"],
            referencia_acuerdo=json.loads(r["extraccion_json"]).get("referencia_acuerdo"),
        )
        for r in filas
    ]


def violaciones_invariante(db: Destino = None) -> int:
    """PAGAR del agente con alguna regla distinta de OK. Debe ser 0 siempre."""
    with conectar(db) as con:
        filas = con.execute(
            "SELECT reglas_json FROM decisiones WHERE decision = 'PAGAR' AND decidido_por = 'agente'"
        ).fetchall()
    return sum(1 for r in filas if any(x["veredicto"] != "OK" for x in json.loads(r["reglas_json"])))


def metricas(db: Destino = None) -> dict:
    agente = [d for d in listar(100000, db) if d.decidido_por == "agente"]
    total = len(agente)
    n = {v: sum(1 for d in agente if d.decision == v) for v in ("PAGAR", "ESCALAR", "FRENAR")}

    def pct(k: str) -> float:
        return round(100 * n[k] / total, 1) if total else 0.0

    con_jev = [d for d in agente if d.jev]
    return {
        "total": total,
        "pagar": n["PAGAR"], "escalar": n["ESCALAR"], "frenar": n["FRENAR"],
        "autonomia_pct": round(100 * (n["PAGAR"] + n["FRENAR"]) / total, 1) if total else 0.0,
        "pagar_pct": pct("PAGAR"), "escaladas_pct": pct("ESCALAR"), "frenadas_pct": pct("FRENAR"),
        "pagos_fuera_de_reglas": violaciones_invariante(db),
        "pendientes": len(pendientes(db)),
        "latencia_jev_ms": round(sum(d.jev.latencia_ms for d in con_jev) / len(con_jev)) if con_jev else 0,
        "backend_jev": con_jev[0].jev.backend if con_jev else None,
        "costo_total_usd": round(sum(d.costo_usd for d in agente), 4),
        "costo_por_factura_usd": round(sum(d.costo_usd for d in agente) / total, 4) if total else 0.0,
        "motor": "postgres" if _es_pg(db or config.database_url() or "") else "sqlite",
    }


def sembrar_si_vacia(semilla: Path, db: Destino = None) -> bool:
    """Solo SQLite: si la base no existe o está vacía y hay semilla, la copia. En Postgres los datos ya viven allí."""
    destino = db or config.database_url() or config.db_path()
    if _es_pg(destino) or not semilla.exists():
        return False
    if Path(destino).exists():
        with conectar(destino) as con:
            if con.execute("SELECT COUNT(*) AS n FROM decisiones").fetchone()["n"] > 0:
                return False
    shutil.copyfile(semilla, destino)
    _inicializados.discard(str(destino))  # la semilla puede venir sin las columnas del sello: migrar al abrirla
    return True


def reiniciar(db: Destino = None) -> None:
    """Borra todas las decisiones. SOLO para scripts de evaluación y tests; la app nunca lo llama."""
    with conectar(db) as con:
        if con.pg:
            con.execute("TRUNCATE decisiones, anclajes RESTART IDENTITY")
        else:
            con.execute("DELETE FROM decisiones")
            con.execute("DELETE FROM anclajes")
            con.execute("DELETE FROM sqlite_sequence WHERE name IN ('decisiones', 'anclajes')")

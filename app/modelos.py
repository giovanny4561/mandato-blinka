"""Contratos de datos de Mandato (ver docs/SDD.md §4).

Todo lo que sale del LLM pasa por `Extraccion` con extra="forbid" y patrones por
campo: ningún texto libre del documento llega al motor ni al registro.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Veredicto = Literal["PAGAR", "ESCALAR", "FRENAR"]
VeredictoRegla = Literal["OK", "ESCALAR", "FRENAR"]
Canal = Literal["pdf", "imagen", "correo", "chat", "agente"]
Actor = Literal["agente", "humano"]


class Estricto(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------- Mandato (reglas como datos) ----------

class Empresa(Estricto):
    nombre: str
    pais: str
    moneda_base: str = Field(pattern=r"^[A-Z]{3}$")


class PresupuestoPeriodo(Estricto):
    monto: Decimal = Field(gt=0)
    recurrencia: Literal["mensual"] = "mensual"


class Limites(Estricto):
    tope_por_pago: Decimal = Field(gt=0)
    umbral_aprobacion_humana: Decimal = Field(gt=0)
    presupuesto_periodo: PresupuestoPeriodo
    ventana_fraccionamiento_dias: int = 7
    ventana_casi_duplicado_dias: int = 10

    @model_validator(mode="after")
    def _umbral_bajo_el_tope(self):
        if self.umbral_aprobacion_humana > self.tope_por_pago:
            raise ValueError("la aprobación humana debe empezar en el tope por pago o por debajo")
        return self


class Conciliacion(Estricto):
    tolerancia_pct: Decimal = Field(Decimal(5), ge=0, le=100)
    tolerancia_abs: Decimal = Field(Decimal(50), ge=0)
    exigir_acuerdo_desde: Decimal = Field(Decimal(3000), ge=0)


class UmbralesJEV(Estricto):
    instrucciones_ocultas_frenar: float = 0.35
    no_es_factura_escalar: float = 0.5
    riesgo_escalar: float = 1.5
    riesgo_frenar: float = 2.5
    coincide_confianza_min: float = 0.6


class Proveedor(Estricto):
    id: str
    nombre: str
    tax_id: str
    pais: str
    moneda: str = Field(pattern=r"^[A-Z]{3}$")
    banco: str
    cuenta: str
    acepta_usdc: bool = False
    billetera_usdc: str | None = Field(None, pattern=r"^0x[0-9a-fA-F]{40}$")  # destino de una orden x402 (simulada)
    dominio_correo: str | None = None


class Acuerdo(Estricto):
    id: str
    proveedor_id: str
    concepto: str
    monto: Decimal
    moneda: str = Field(pattern=r"^[A-Z]{3}$")
    vigente_hasta: date


class Mandato(Estricto):
    version: str
    empresa: Empresa
    limites: Limites
    conciliacion: Conciliacion = Conciliacion()
    umbrales_jev: UmbralesJEV = UmbralesJEV()
    proveedores_permitidos: list[Proveedor]
    acuerdos: list[Acuerdo] = []

    @model_validator(mode="after")
    def _misma_moneda(self):
        """Las reglas de monto comparan en moneda_base; un proveedor en otra moneda las dejaría sin efecto."""
        otras = [p.id for p in self.proveedores_permitidos if p.moneda != self.empresa.moneda_base]
        if otras:
            raise ValueError(f"proveedores en moneda distinta a {self.empresa.moneda_base}: {otras}")
        ids = {p.id for p in self.proveedores_permitidos}
        huerfanos = [a.id for a in self.acuerdos if a.proveedor_id not in ids]
        if huerfanos:
            raise ValueError(f"acuerdos de proveedores que no existen: {huerfanos}")
        return self


# ---------- Documento y extracción (cuarentena) ----------

class Documento(BaseModel):
    nombre: str
    canal: Canal
    texto: str | None = None
    imagen_b64: str | None = None
    mime_imagen: str | None = None
    remitente: str | None = None
    hash: str


Texto160 = Annotated[str, StringConstraints(max_length=160)]


class Extraccion(Estricto):
    proveedor_nombre: str | None = Field(None, max_length=120)
    proveedor_tax_id: str | None = Field(None, max_length=40, pattern=r"^[A-Za-z0-9.\-]+$")
    numero_factura: str | None = Field(None, max_length=40)
    monto_total: Decimal | None = Field(None, gt=0)
    moneda: str | None = Field(None, pattern=r"^[A-Z]{3}$")
    fecha_emision: date | None = None
    fecha_vencimiento: date | None = None
    banco: str | None = Field(None, max_length=80)
    cuenta_destino: str | None = Field(None, max_length=40, pattern=r"^[A-Za-z0-9]+$")
    referencia_acuerdo: str | None = Field(None, max_length=40)
    conceptos: list[Texto160] = Field(default_factory=list, max_length=10)
    legible: bool = True
    observaciones: str | None = Field(None, max_length=300)


# ---------- Reglas y JEV ----------

class ResultadoRegla(BaseModel):
    regla_id: str
    nombre: str
    veredicto: VeredictoRegla
    evidencia: dict = {}
    mensaje: str = ""


class RespuestaJEV(BaseModel):
    tipo: Literal["noul", "choice", "score"]
    probabilidad: float | None = None      # noul
    valor: str | float | None = None       # choice -> str, score -> float
    confianza: float | None = None         # choice / score
    probabilidades: dict[str, float] | None = None  # choice


class RespuestasJEV(BaseModel):
    backend: Literal["typesafe", "llm"]
    modelo: str
    latencia_ms: int = 0
    costo_usd: float = 0.0
    respuestas: dict[str, RespuestaJEV] = {}


# ---------- Decisión y registro ----------

class EntradaHistorial(BaseModel):
    """Lo mínimo que las reglas necesitan de decisiones anteriores."""
    id: int
    ts: str
    proveedor_id: str | None
    numero_factura: str | None
    monto_total: Decimal | None
    moneda: str | None
    fecha_emision: date | None
    decision: Veredicto
    factura_hash: str
    decidido_por: Actor
    referencia_a: int | None = None
    referencia_acuerdo: str | None = None


class Decision(BaseModel):
    id: int | None = None
    ts: str = ""
    factura_hash: str
    archivo: str
    canal: Canal
    proveedor_id: str | None = None
    extraccion: Extraccion
    consistente: bool = True
    reglas: list[ResultadoRegla] = []
    jev: RespuestasJEV | None = None
    decision: Veredicto
    regla_decisiva: str | None = None
    explicacion: str
    riel_sugerido: str | None = None
    fecha_pago_programada: date | None = None
    mandato_hash: str
    decidido_por: Actor = "agente"
    referencia_a: int | None = None
    nota: str | None = None
    costo_usd: float = 0.0
    latencia_ms: int = 0
    sello: str | None = None  # hash de la fila en el registro sellado; lo pone registro.insertar

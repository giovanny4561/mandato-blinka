"""Configuración leída del entorno (.env). Nada de secretos en código.

Todo son funciones (no constantes) para que los tests puedan cambiar el entorno
con monkeypatch sin recargar módulos.
"""
import os
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

RAIZ = Path(__file__).resolve().parent.parent
load_dotenv(RAIZ / ".env")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


def data_dir() -> Path:
    d = Path(os.getenv("DATA_DIR") or RAIZ / "data").resolve()
    d.mkdir(parents=True, exist_ok=True)
    return d


def database_url() -> str | None:
    """Postgres si DATABASE_URL está definida (Railway la inyecta); si no, SQLite local."""
    return os.getenv("DATABASE_URL") or None


def db_path() -> Path:
    return data_dir() / "mandato.sqlite"


def mandato_path() -> Path:
    return data_dir() / "mandato.json"


def facturas_dir() -> Path:
    return data_dir() / "facturas"


def entrantes_dir() -> Path:
    d = data_dir() / "entrantes"
    d.mkdir(exist_ok=True)
    return d


def hoy() -> date:
    """Fecha de referencia; MANDATO_HOY la fija para evaluaciones reproducibles."""
    v = os.getenv("MANDATO_HOY")
    return date.fromisoformat(v) if v else date.today()


def sello_llave() -> str:
    """Llave privada Ed25519 en base64 (32 bytes) para firmar la cabeza del registro. Vacía: se usa DATA_DIR/sello.key."""
    return os.getenv("SELLO_LLAVE", "")


def openrouter_key() -> str:
    return os.getenv("OPENROUTER_API_KEY", "")


def extractor_modelo_a() -> str:
    """Pasada A para entradas con texto (PDF con capa de texto, correo, chat): modelo barato."""
    return os.getenv("EXTRACTOR_MODELO_A", "google/gemini-2.5-flash-lite")


def extractor_modelo_b() -> str:
    return os.getenv("EXTRACTOR_MODELO_B", "openai/gpt-4.1-nano")


def extractor_modelo_a_imagen() -> str:
    """Pasada A para imágenes: los modelos lite/nano fallan más con fotos (medido 17-oct: 23/24 vs 24/24)."""
    return os.getenv("EXTRACTOR_MODELO_A_IMAGEN", "google/gemini-2.5-flash")


def extractor_modelo_b_imagen() -> str:
    return os.getenv("EXTRACTOR_MODELO_B_IMAGEN", "openai/gpt-4.1-mini")


def jev_backend() -> str:
    """'typesafe' (JEV real) o 'llm' (shim con el mismo contrato)."""
    b = os.getenv("JEV_BACKEND", "auto")
    if b == "auto":
        return "typesafe" if typesafe_key() else "llm"
    return b


def typesafe_key() -> str:
    return os.getenv("TYPESAFE_API_KEY", "")


def typesafe_base_url() -> str:
    return os.getenv("TYPESAFE_BASE_URL", "https://api.typesafe.ai").rstrip("/")


def typesafe_model() -> str:
    return os.getenv("TYPESAFE_MODEL", "jev-latest")


def jev_llm_modelo() -> str:
    return os.getenv("JEV_LLM_MODELO", "google/gemini-2.5-flash")


def aprobador_pin() -> str:
    """PIN de aprobador. Vacío, el de ejemplo o menos de 6 caracteres → "" (toda acción queda deshabilitada)."""
    pin = os.getenv("APROBADOR_PIN", "")
    return pin if len(pin) >= 6 and pin != "cambiar-esto" else ""

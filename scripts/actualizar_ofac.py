"""Descarga la lista SDN de OFAC y guarda en data/ofac_sdn.txt los nombres normalizados, uno por línea.

Uso: uv run python scripts/actualizar_ofac.py
R01 la lee una vez al arrancar: después de actualizarla hay que reiniciar el servidor (o desplegar).
"""
import csv
import io
import sys
from datetime import date
from pathlib import Path

import httpx

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from app.ofac import normalizar  # noqa: E402

# la dirección histórica (treasury.gov/ofac/downloads/sdn.csv) redirige a esta
FUENTE = "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN.CSV"


def main() -> None:
    r = httpx.get(FUENTE, follow_redirects=True, timeout=120)
    r.raise_for_status()
    # columnas: ent_num, SDN_Name, SDN_Type, Program... sin cabecera; la última línea puede traer un byte de fin (0x1A)
    nombres = sorted({n for fila in csv.reader(io.StringIO(r.text)) if len(fila) > 1 and (n := normalizar(fila[1]))})
    if len(nombres) < 5000:
        raise SystemExit(f"la lista trae solo {len(nombres)} nombres; no se reemplaza data/ofac_sdn.txt")
    cabecera = (f"# Lista SDN de OFAC (Departamento del Tesoro de EE. UU.), {len(nombres)} nombres normalizados: "
                f"alfanumérico en mayúsculas, sin tildes.\n# Fuente: {FUENTE}\n# Descargada: {date.today().isoformat()}\n")
    destino = RAIZ / "data" / "ofac_sdn.txt"
    destino.write_text(cabecera + "\n".join(nombres) + "\n", encoding="utf-8")
    print(f"{len(nombres)} nombres en {destino.relative_to(RAIZ)}")


if __name__ == "__main__":
    main()

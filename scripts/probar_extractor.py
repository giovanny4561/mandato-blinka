"""Smoke con red: uv run python scripts/probar_extractor.py <ruta>

Imprime la extracción de la pasada A, si A y B fueron consistentes, el costo y los ms.
No es pytest: gasta dinero real en OpenRouter.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config, documentos, extractor  # noqa: E402

if len(sys.argv) != 2:
    sys.exit(__doc__)

doc = documentos.cargar_archivo(Path(sys.argv[1]))
print(f"documento: {doc.nombre} canal={doc.canal} texto={len(doc.texto or '')} chars imagen={'sí' if doc.imagen_b64 else 'no'}")
print(f"modelos: A={config.extractor_modelo_a()} B={config.extractor_modelo_b()}")
inicio = time.perf_counter()
extraccion, consistente, costo = extractor.extraer(doc)
ms = int((time.perf_counter() - inicio) * 1000)
print(extraccion.model_dump_json(indent=2))
print(f"consistente={consistente} costo_usd={costo:.6f} ms={ms}")

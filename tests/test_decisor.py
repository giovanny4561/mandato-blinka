"""Decisor JEV: parseo TypeSafe real, shim LLM, selección de backend y reintentos. Todo offline."""
import json

import httpx
import pytest

from app import decisor
from app.modelos import RespuestasJEV

RESPUESTA_TYPESAFE = {
    "model": "jev-1.13.0",
    "answers": {
        "is_invoice": {"type": "noul", "noul": 0.97},
        "hidden_instructions": {"type": "noul", "noul": 0.02},
        "matches_agreement": {
            "type": "choice", "choice": "matches", "confidence": 0.88,
            "probabilities": {"matches": 0.88, "differs_amount": 0.07, "differs_concept": 0.02, "no_agreement": 0.02, "other": 0.01},
        },
        "payment_risk": {
            "type": "score", "score": 0.3, "confidence": 0.9,
            "legend": ["0 none", "1 low", "2 high", "3 evident"], "probabilities": {"0": 0.75, "1": 0.2, "2": 0.05, "3": 0.0},
        },
        "expense_category": {
            "type": "choice", "choice": "software", "confidence": 0.8,
            "probabilities": {"software": 0.8, "professional_services": 0.1, "contractor_payroll": 0.02, "logistics": 0.03, "marketing": 0.03, "other": 0.02},
        },
    },
    "usage": {"input_tokens": 1000, "output_tokens": 0},
}

STATE = {"texto": "Factura NA-2026-0917 Nube Andina SAS 3200 USD", "extraccion": {"monto_total": "3200"}}


def transporte(handler, monkeypatch):
    """Sustituye el cliente HTTP del decisor por uno con MockTransport."""
    monkeypatch.setattr(decisor, "_cliente", lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(decisor.time, "sleep", lambda s: None)


def respuesta_openrouter(contenido: dict | str, costo=0.00003) -> dict:
    texto = contenido if isinstance(contenido, str) else json.dumps(contenido)
    return {"choices": [{"message": {"content": texto}}], "usage": {"cost": costo}}


def test_preguntas_tienen_las_5_claves_y_opciones_exactas():
    assert list(decisor.PREGUNTAS) == ["is_invoice", "hidden_instructions", "matches_agreement", "payment_risk", "expense_category"]
    assert decisor.PREGUNTAS["is_invoice"]["type"] == decisor.PREGUNTAS["hidden_instructions"]["type"] == "noul"
    assert list(decisor.PREGUNTAS["matches_agreement"]["criteria"]) == ["matches", "differs_amount", "differs_concept", "no_agreement", "other"]
    assert list(decisor.PREGUNTAS["expense_category"]["criteria"]) == ["software", "professional_services", "contractor_payroll", "logistics", "marketing", "other"]
    assert decisor.PREGUNTAS["payment_risk"]["type"] == "score"
    assert len(decisor.PREGUNTAS["payment_risk"]["criteria"]) == 4


def test_parsea_respuesta_typesafe_completa(monkeypatch):
    monkeypatch.setenv("JEV_BACKEND", "typesafe")
    monkeypatch.setenv("TYPESAFE_API_KEY", "ts-prueba")
    monkeypatch.setenv("TYPESAFE_BASE_URL", "https://ai-gateway.vercel.sh/typesafe/")
    monkeypatch.setenv("TYPESAFE_MODEL", "typesafe-ai/jev")
    visto = {}

    def handler(req: httpx.Request):
        visto["url"] = str(req.url)
        visto["auth"] = req.headers["Authorization"]
        visto["body"] = json.loads(req.content)
        return httpx.Response(200, json=RESPUESTA_TYPESAFE)

    transporte(handler, monkeypatch)
    r = decisor.preguntar_factura(STATE)

    assert visto["url"] == "https://ai-gateway.vercel.sh/typesafe/v1/systemone"
    assert visto["auth"] == "Bearer ts-prueba"
    assert visto["body"]["model"] == "typesafe-ai/jev"
    assert visto["body"]["state"] == STATE
    assert visto["body"]["questions"] == decisor.PREGUNTAS
    assert isinstance(r, RespuestasJEV)
    assert r.backend == "typesafe" and r.modelo == "jev-1.13.0"
    assert r.costo_usd == pytest.approx(1000 * 0.042 / 1e6)
    assert r.latencia_ms >= 0
    assert set(r.respuestas) == set(decisor.PREGUNTAS)
    assert decisor.prob(r, "is_invoice") == 0.97
    assert decisor.prob(r, "hidden_instructions") == 0.02
    assert decisor.valor(r, "matches_agreement") == "matches"
    assert decisor.confianza(r, "matches_agreement") == 0.88
    assert r.respuestas["matches_agreement"].probabilidades["differs_amount"] == 0.07
    assert decisor.valor(r, "payment_risk") == 0.3 and decisor.confianza(r, "payment_risk") == 0.9
    assert decisor.valor(r, "expense_category") == "software"
    assert decisor.prob(r, "no_existe") is None and decisor.valor(r, "no_existe") is None


def test_confianza_choice_se_deriva_de_probabilidades_si_falta(monkeypatch):
    monkeypatch.setenv("JEV_BACKEND", "typesafe")
    monkeypatch.setenv("TYPESAFE_API_KEY", "ts-prueba")
    answers = {"q": {"type": "choice", "choice": "b", "probabilities": {"a": 0.3, "b": 0.7}}}
    transporte(lambda req: httpx.Response(200, json={"model": "jev-1.13.0", "answers": answers, "usage": {"input_tokens": 1}}), monkeypatch)
    r = decisor.preguntar(STATE, {"q": {"type": "choice", "instructions": "x", "criteria": {"a": None, "b": None}}})
    assert decisor.confianza(r, "q") == pytest.approx(0.7)


def test_typesafe_sin_llave_falla_con_runtime_error_y_no_cae_al_shim(monkeypatch):
    monkeypatch.setenv("JEV_BACKEND", "typesafe")
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)

    def handler(req: httpx.Request):
        if req.headers.get("Authorization") != "Bearer llave-valida":
            return httpx.Response(401, json={"error": "invalid api key"})
        return httpx.Response(200, json=RESPUESTA_TYPESAFE)

    transporte(handler, monkeypatch)
    monkeypatch.setattr(decisor, "_llm", lambda *a: pytest.fail("no debe caer al shim"))
    with pytest.raises(RuntimeError, match="401"):
        decisor.preguntar_factura(STATE)


def test_auto_sin_llave_usa_llm(monkeypatch):
    monkeypatch.setenv("JEV_BACKEND", "auto")
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.setattr(decisor, "_typesafe", lambda *a: pytest.fail("no debe usar typesafe sin llave"))
    monkeypatch.setattr(decisor, "_llm", lambda s, p: RespuestasJEV(backend="llm", modelo="m"))
    assert decisor.preguntar_factura(STATE).backend == "llm"


def test_reintenta_en_429_y_luego_responde(monkeypatch):
    monkeypatch.setenv("JEV_BACKEND", "typesafe")
    monkeypatch.setenv("TYPESAFE_API_KEY", "ts-prueba")
    llamadas = []

    def handler(req: httpx.Request):
        llamadas.append(1)
        return httpx.Response(429) if len(llamadas) < 3 else httpx.Response(200, json=RESPUESTA_TYPESAFE)

    transporte(handler, monkeypatch)
    assert decisor.preguntar_factura(STATE).backend == "typesafe"
    assert len(llamadas) == 3


def test_typesafe_529_persistente_lanza_runtime_error(monkeypatch):
    monkeypatch.setenv("JEV_BACKEND", "typesafe")
    monkeypatch.setenv("TYPESAFE_API_KEY", "ts-prueba")
    llamadas = []
    transporte(lambda req: (llamadas.append(1), httpx.Response(529))[1], monkeypatch)
    with pytest.raises(RuntimeError, match="529"):
        decisor.preguntar_factura(STATE)
    assert len(llamadas) == 3


def test_shim_llm_normaliza_y_acota(monkeypatch):
    monkeypatch.setenv("JEV_LLM_MODELO", "modelo/prueba")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-prueba")
    visto = {}
    crudo = {
        "is_invoice": {"type": "noul", "probability": 0.9},
        "hidden_instructions": {"type": "noul", "probability": 1.4},
        "matches_agreement": {"type": "choice", "probabilities": {"matches": 2, "differs_amount": 1, "differs_concept": 1}},
        "payment_risk": {"type": "score", "score": 7.5, "confidence": 0.6},
        "expense_category": {"type": "choice", "probabilities": {"software": 0.5, "other": 0.5, "inventada": 9}},
    }

    def handler(req: httpx.Request):
        visto["body"] = json.loads(req.content)
        visto["auth"] = req.headers["Authorization"]
        return httpx.Response(200, json=respuesta_openrouter(crudo, costo=0.00004))

    transporte(handler, monkeypatch)
    r = decisor.preguntar_factura(STATE)

    assert visto["auth"] == "Bearer or-prueba"
    assert visto["body"]["model"] == "modelo/prueba"
    assert visto["body"]["temperature"] == 0
    assert visto["body"]["response_format"] == {"type": "json_object"}
    assert visto["body"]["usage"] == {"include": True}
    assert "<untrusted_document>" in visto["body"]["messages"][-1]["content"]
    assert r.backend == "llm" and r.modelo == "modelo/prueba" and r.costo_usd == 0.00004
    assert decisor.prob(r, "hidden_instructions") == 1.0
    ma = r.respuestas["matches_agreement"]
    assert ma.valor == "matches" and ma.confianza == pytest.approx(0.5)
    assert sum(ma.probabilidades.values()) == pytest.approx(1.0)
    assert set(ma.probabilidades) == set(decisor.PREGUNTAS["matches_agreement"]["criteria"])
    assert ma.probabilidades["no_agreement"] == 0.0
    assert decisor.valor(r, "payment_risk") == 3.0 and decisor.confianza(r, "payment_risk") == 0.6
    assert "inventada" not in r.respuestas["expense_category"].probabilidades


def test_shim_llm_reintenta_json_invalido_una_vez(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-prueba")
    llamadas = []
    bueno = {k: ({"type": "noul", "probability": 0.5} if p["type"] == "noul" else
                 {"type": "choice", "probabilities": {o: 1 for o in p["criteria"]}} if p["type"] == "choice" else
                 {"type": "score", "score": 1, "confidence": 0.5}) for k, p in decisor.PREGUNTAS.items()}

    def handler(req: httpx.Request):
        llamadas.append(1)
        return httpx.Response(200, json=respuesta_openrouter("esto no es json" if len(llamadas) == 1 else bueno))

    transporte(handler, monkeypatch)
    assert decisor.preguntar_factura(STATE).backend == "llm"
    assert len(llamadas) == 2

    llamadas.clear()
    transporte(lambda req: (llamadas.append(1), httpx.Response(200, json=respuesta_openrouter("{}")))[1], monkeypatch)
    with pytest.raises(RuntimeError):
        decisor.preguntar_factura(STATE)
    assert len(llamadas) == 2

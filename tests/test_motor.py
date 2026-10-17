"""Consolidación, regla decisiva, explicación por plantilla y orquestación con fakes (sin LLM ni JEV)."""
import random
from datetime import date

from app import mandato, motor, registro
from app.modelos import Documento, ResultadoRegla
from tests.conftest import hacer_extraccion
from tests.test_reglas import HOY, jev

DOC = Documento(nombre="F01_nube_andina_oct.pdf", canal="pdf", texto="FACTURA NA-2026-0917 ...", hash="hash-f01")


def regla(id_, veredicto):
    return ResultadoRegla(regla_id=id_, nombre=f"nombre {id_}", veredicto=veredicto, mensaje=f"mensaje {id_}")


def extraer_ok(doc):
    return hacer_extraccion(), True, 0.001


def test_consolidar_precedencia_y_primera_regla_decisiva():
    assert motor.consolidar([regla("R01", "OK"), regla("R08", "ESCALAR"), regla("R02", "FRENAR"), regla("J01", "FRENAR")]) == ("FRENAR", "R02")
    assert motor.consolidar([regla("R05", "ESCALAR"), regla("R08", "ESCALAR")]) == ("ESCALAR", "R05")
    assert motor.consolidar([regla("R01", "OK"), regla("R02", "OK")]) == ("PAGAR", None)


def test_invariante_pagar_solo_si_todas_ok():
    azar = random.Random(7)
    for _ in range(200):
        reglas = [regla(f"R{i:02d}", azar.choice(["OK", "OK", "OK", "ESCALAR", "FRENAR"])) for i in range(18)]
        veredicto, decisiva = motor.consolidar(reglas)
        assert (veredicto == "PAGAR") == all(r.veredicto == "OK" for r in reglas)
        assert (decisiva is None) == (veredicto == "PAGAR")
        if decisiva:
            assert next(r for r in reglas if r.regla_id == decisiva).veredicto == veredicto


def test_explicar_cita_regla_mensaje_y_otras_observaciones():
    m = mandato.cargar()
    prov, e = m.proveedores_permitidos[0], hacer_extraccion()
    t = motor.explicar("FRENAR", "R02", [regla("R01", "OK"), regla("R02", "FRENAR"), regla("R13", "ESCALAR")], e, prov)
    assert t.startswith("FRENAR · R02 nombre R02: mensaje R02.") and "no paga esto sin intervención humana" in t and "Otros: R13." in t
    t = motor.explicar("ESCALAR", "R13", [regla("R13", "ESCALAR")], e, prov)
    assert t.startswith("ESCALAR · R13") and "Requiere aprobación de una persona" in t and "Otros" not in t
    t = motor.explicar("PAGAR", None, [regla("R01", "OK")] * 18, e, prov)
    assert t == "PAGAR · Dentro del mandato: Nube Andina SAS, USD 3.200,00, cuenta ****0193, 18 controles OK."


def test_decidir_pagar_con_fakes_y_registro():
    m = mandato.cargar()
    estados = []

    def preguntar(estado):
        estados.append(estado)
        return jev().model_copy(update={"costo_usd": 0.0002, "latencia_ms": 5})

    d = motor.decidir(DOC, m, extraer=extraer_ok, preguntar=preguntar, historial=[], hoy=HOY)
    assert d.decision == "PAGAR" and d.regla_decisiva is None and d.proveedor_id == "PRV-001"
    assert d.fecha_pago_programada == HOY  # venció el 16: se programa hoy
    assert d.mandato_hash == mandato.hash_de(m) and abs(d.costo_usd - 0.0012) < 1e-9 and d.latencia_ms >= 0
    assert len(d.reglas) == 18 and d.jev.costo_usd == 0.0002 and d.archivo == DOC.nombre and d.factura_hash == DOC.hash
    assert d.explicacion.startswith("PAGAR") and d.consistente
    estado = estados[0]
    assert set(estado) == {"document_text", "extracted", "agreement", "master_vendor", "today"}
    assert estado["document_text"].startswith("FACTURA") and estado["today"] == "2026-10-17"
    assert estado["agreement"]["id"] == "OC-2026-014" and estado["extracted"]["monto_total"] == "3200"
    assert estado["master_vendor"] == {"name": "Nube Andina SAS", "bank": "Bancolombia", "account_masked": "****0193"}
    assert registro.obtener(registro.insertar(d)).decision == "PAGAR"  # la evidencia se serializa


def test_decidir_frena_sin_jev_y_no_programa_pago():
    def extraer(doc):
        return hacer_extraccion(cuenta_destino="03100554821"), True, 0.0

    d = motor.decidir(DOC, mandato.cargar(), extraer=extraer, preguntar=None, historial=[], hoy=HOY)
    assert d.decision == "FRENAR" and d.regla_decisiva == "R02" and d.fecha_pago_programada is None and d.jev is None
    assert d.explicacion.startswith("FRENAR · R02 Cuenta coincide con el maestro:")


def test_fecha_pago_programada_es_el_vencimiento_si_no_ha_pasado():
    def extraer(doc):
        return hacer_extraccion(fecha_vencimiento=date(2026, 10, 31)), True, 0.0

    d = motor.decidir(DOC, mandato.cargar(), extraer=extraer, preguntar=None, historial=[], hoy=HOY)
    assert d.decision == "PAGAR" and d.fecha_pago_programada == date(2026, 10, 31)


def test_decidir_inconsistente_escala_por_r12():
    def extraer(doc):
        return hacer_extraccion(), False, 0.0

    d = motor.decidir(DOC, mandato.cargar(), extraer=extraer, preguntar=None, historial=[], hoy=HOY)
    assert d.decision == "ESCALAR" and d.regla_decisiva == "R12" and not d.consistente


def test_estado_para_jev_sin_texto_ni_proveedor():
    doc = Documento(nombre="F17_ilegible.png", canal="imagen", imagen_b64="AAAA", hash="h17")
    e = hacer_extraccion(legible=False, observaciones="imagen borrosa")
    estado = motor.estado_para_jev(doc, e, None, None, HOY)
    assert estado["document_text"] == "[imagen adjunta]\nimagen borrosa"
    assert estado["agreement"] is None and estado["master_vendor"] is None


def test_a_historial_y_decidir_offline():
    m = mandato.cargar()
    d = motor.decidir(DOC, m, extraer=extraer_ok, preguntar=None, historial=[], hoy=HOY)
    h = motor.a_historial(d, id=1)
    assert (h.id, h.proveedor_id, h.numero_factura, h.decision, h.factura_hash) == (1, "PRV-001", "NA-2026-0917", "PAGAR", "hash-f01")
    veredicto, decisiva, reglas = motor.decidir_offline(hacer_extraccion(), jev(), m, [h], canal="pdf", remitente=None,
                                                        consistente=True, factura_hash="otro-hash", hoy=HOY)
    assert veredicto == "FRENAR" and decisiva == "R04" and len(reglas) == 18

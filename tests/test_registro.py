import pytest

from app import registro
from app.modelos import ResultadoRegla
from tests.conftest import hacer_decision


def test_insertar_y_obtener_redondea_el_viaje():
    id_ = registro.insertar(hacer_decision())
    d = registro.obtener(id_)
    assert d is not None and d.id == id_ and d.decision == "PAGAR"
    assert d.extraccion.monto_total == 3200 and d.reglas[0].regla_id == "R01"
    assert d.ts  # el registro pone la fecha


def test_pendientes_y_resolver_crean_fila_nueva_sin_editar():
    esc = registro.insertar(hacer_decision(decision="ESCALAR", regla_decisiva="R08", archivo="F06_umbral.pdf"))
    assert [p.id for p in registro.pendientes()] == [esc]
    nueva = registro.resolver(esc, aprobar=True, nota="OK por CFO")
    assert registro.pendientes() == []
    h = registro.obtener(nueva)
    assert h.decidido_por == "humano" and h.referencia_a == esc and h.decision == "PAGAR"
    assert registro.obtener(esc).decision == "ESCALAR"  # la original no cambió
    with pytest.raises(ValueError):
        registro.resolver(esc, aprobar=False, nota="dos veces no")


def test_resolver_solo_acepta_escaladas_del_agente():
    pag = registro.insertar(hacer_decision())
    with pytest.raises(ValueError):
        registro.resolver(pag, aprobar=True, nota="")


def test_invariante_detecta_pagar_con_regla_fallida():
    registro.insertar(hacer_decision())
    assert registro.violaciones_invariante() == 0
    registro.insertar(hacer_decision(reglas=[ResultadoRegla(regla_id="R02", nombre="Cuenta", veredicto="FRENAR")]))
    assert registro.violaciones_invariante() == 1


def test_metricas_vacias_y_con_datos():
    assert registro.metricas()["total"] == 0
    registro.insertar(hacer_decision())
    registro.insertar(hacer_decision(decision="FRENAR", regla_decisiva="R02"))
    registro.insertar(hacer_decision(decision="ESCALAR", regla_decisiva="R08"))
    m = registro.metricas()
    assert m["total"] == 3 and m["autonomia_pct"] == 66.7 and m["pendientes"] == 1
    assert m["pagos_fuera_de_reglas"] == 0


def test_historial_devuelve_lo_que_usan_las_reglas():
    registro.insertar(hacer_decision())
    h = registro.historial()
    assert len(h) == 1 and h[0].proveedor_id == "PRV-001" and h[0].numero_factura == "NA-2026-0917"
    assert str(h[0].monto_total) == "3200.0"

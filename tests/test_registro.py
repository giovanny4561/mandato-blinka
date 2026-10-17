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


# ---------- registro sellado ----------

def test_cadena_integra_y_detecta_edicion_y_borrado():
    ids = [registro.insertar(hacer_decision(archivo=f"F{i}.pdf")) for i in range(4)]
    v = registro.verificar()
    assert v["integro"] and v["filas"] == 4 and v["cabeza"]["id"] == ids[-1]
    d = [registro.obtener(i) for i in ids]
    assert d[0].sello and d[1].sello != d[0].sello
    with registro.conectar() as con:  # alguien edita el monto de la fila 2 por fuera de la app
        con.execute("UPDATE decisiones SET monto_total = 1 WHERE id = ?", (ids[1],))
    assert registro.verificar()["alterada_en"] == ids[1]
    with registro.conectar() as con:  # y aunque la restaure, borrar la fila 3 rompe el enlace de la 4
        con.execute("UPDATE decisiones SET monto_total = 3200 WHERE id = ?", (ids[1],))
        con.execute("DELETE FROM decisiones WHERE id = ?", (ids[2],))
    v = registro.verificar()
    assert not v["integro"] and v["alterada_en"] == ids[3] and v["cabeza"] is None


def test_resolver_tambien_encadena():
    esc = registro.insertar(hacer_decision(decision="ESCALAR", regla_decisiva="R08"))
    registro.resolver(esc, aprobar=False, nota="no")
    v = registro.verificar()
    assert v["integro"] and v["filas"] == 2


def test_migra_y_sella_una_base_sin_columnas_de_sello(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    db = tmp_path / "vieja.sqlite"
    for i in range(3):
        registro.insertar(hacer_decision(archivo=f"V{i}.pdf"), db)
    with registro.conectar(db) as con:  # la deja como antes del sellado
        con.execute("DROP INDEX ux_decisiones_previo")
        con.execute("ALTER TABLE decisiones DROP COLUMN hash")
        con.execute("ALTER TABLE decisiones DROP COLUMN hash_previo")
    registro._inicializados.discard(str(db))
    v = registro.verificar(db)
    assert v["integro"] and v["filas"] == 3
    registro.insertar(hacer_decision(archivo="V3.pdf"), db)
    assert registro.verificar(db)["integro"]


def test_firma_de_la_cabeza_verifica_y_no_sirve_para_otra():
    from app import firma
    registro.insertar(hacer_decision())
    f = firma.firmar_cabeza(registro.verificar()["cabeza"])
    assert firma.verificar_firma(f["mensaje"], f["firma"], f["llave_publica"])
    assert firma.firmar_cabeza(registro.verificar()["cabeza"])["llave_publica"] == f["llave_publica"]  # misma llave
    registro.insertar(hacer_decision(archivo="otra.pdf"))
    otra = firma.firmar_cabeza(registro.verificar()["cabeza"])
    assert not firma.verificar_firma(otra["mensaje"], f["firma"], f["llave_publica"])

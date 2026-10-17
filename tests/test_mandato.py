from datetime import date

from app import mandato
from tests.conftest import hacer_extraccion

HOY = date(2026, 10, 17)


def test_cargar_lee_el_mandato_del_data_dir():
    m = mandato.cargar()
    assert m.empresa.moneda_base == "USD" and len(m.proveedores_permitidos) == 4


def test_hash_estable_y_sensible_al_contenido():
    m = mandato.cargar()
    h = mandato.hash_de(m)
    assert len(h) == 12 and h == mandato.hash_de(mandato.cargar())
    assert mandato.hash_de(m.model_copy(update={"version": "otra"})) != h


def test_guardar_y_cargar_redondean_el_viaje(tmp_path):
    m = mandato.cargar()
    ruta = tmp_path / "copia.json"
    mandato.guardar(m, ruta)
    assert mandato.cargar(ruta) == m


def test_normalizar_nombre_ignora_tildes_puntuacion_y_sufijos():
    assert mandato.normalizar_nombre("Nube Andina S.A.S.") == mandato.normalizar_nombre("nube andina sas") == "nube andina"
    assert mandato.normalizar_nombre("Logística Sur SpA") == "logistica sur"
    assert mandato.normalizar_nombre("Pixel Forge, LLC") == "pixel forge"
    assert mandato.normalizar_nombre("Carla Ríos") == "carla rios"


def test_proveedor_por_tax_id_y_luego_por_nombre():
    m = mandato.cargar()
    assert mandato.proveedor_por(hacer_extraccion(proveedor_tax_id="901.234.567-8", proveedor_nombre="Otro"), m).id == "PRV-001"
    assert mandato.proveedor_por(hacer_extraccion(proveedor_tax_id=None, proveedor_nombre="LOGÍSTICA SUR S.p.A."), m).id == "PRV-003"
    assert mandato.proveedor_por(hacer_extraccion(proveedor_tax_id="900111222-3", proveedor_nombre="Servicios Globales Omega SAS"), m) is None
    assert mandato.proveedor_por(hacer_extraccion(proveedor_tax_id=None, proveedor_nombre=None), m) is None


def test_acuerdo_por_y_acuerdos_vigentes():
    m = mandato.cargar()
    assert mandato.acuerdo_por("OC-2026-014", "PRV-001", m, HOY).monto == 3200
    assert mandato.acuerdo_por("oc 2026 014", "PRV-001", m, HOY).id == "OC-2026-014"
    assert mandato.acuerdo_por("OC-2026-014", "PRV-003", m, HOY) is None  # es de otro proveedor
    assert mandato.acuerdo_por("OC-9999", "PRV-001", m, HOY) is None
    assert mandato.acuerdo_por(None, "PRV-001", m, HOY) is None
    assert mandato.acuerdo_por("OC-2026-014", "PRV-001", m, date(2027, 1, 1)) is None  # vencido
    assert [a.id for a in mandato.acuerdos_vigentes("PRV-003", m, HOY)] == ["OC-2026-021", "OC-2026-027"]

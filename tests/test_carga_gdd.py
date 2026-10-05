"""
Pruebas del orquestador staging -> gdd. No usan una base de datos real:
`gdd_repository` se reemplaza por completo con MagicMock (via
monkeypatch), asi se prueba que `ejecutar_merge_dominio` orquesta bien
lectura de staging + resolucion de catalogos + plan de merge + escritura,
sin depender de SQL Server. La logica fina de INSERTAR/ACTUALIZAR/
REEMPLAZAR/ELIMINAR ya esta cubierta a fondo en test_gdd_merge_logic.py.
"""

from __future__ import annotations

import datetime
from unittest.mock import MagicMock

import pytest

from gdd_loader.domain.gdd_merge_logic import ExistenteVersionado
from gdd_loader.load.gdd_repository import CatalogoNoEncontradoError
from gdd_loader.pipeline import carga_gdd


def _fila_detalle(codigo_atributo="1", fecha=datetime.date(2025, 1, 31)) -> dict:
    return {
        "codigo_dominio": "ADS",
        "codigo_atributo": codigo_atributo,
        "atributo": "Un atributo",
        "descripcion_atributo": "Una descripcion",
        "nivel_criticidad": "Crítico",
        "reporte": "X",
        "proyecto": "-",
        "proceso": "-",
        "estructura": "-",
        "es_dato_personal": "No",
        "categoria_nivel_1": "-",
        "tipo_dato": "-",
        "sensibilidad": "-",
        "tipo_atributo": "Maestro",
        "compartido_similar": "-",
        "atributo_primario": "-",
        "dominio_primario": "-",
        "fecha_aprobada": fecha,
    }


@pytest.fixture
def staging_repo_vacio():
    repo = MagicMock()
    repo.leer.return_value = [_fila_detalle()]
    repo.leer_por_prefijo.return_value = []  # sin metadata_tecnica en este test
    return repo


def _parchear_repo_gdd(monkeypatch, *, id_dominio=99, existentes_atributo=None):
    """Reemplaza gdd_repository completo con mocks controlados; retorna el
    modulo mockeado para hacer asserts sobre las llamadas.
    """
    mock_repo = MagicMock()
    mock_repo.resolver_dominio_id.return_value = id_dominio
    mock_repo.atributos_existentes.return_value = existentes_atributo or {}
    mock_repo.resolver_catalogo_controlado.return_value = 1
    mock_repo.resolver_dato_personal.return_value = None
    mock_repo.buscar_atributo_primario.return_value = (None, None)
    mock_repo.insertar_atributo.return_value = 555
    mock_repo.fuente_oficial_existentes.return_value = {}
    mock_repo.version_release_atributo.return_value = (None, None)
    mock_repo.version_release_fuente_oficial.return_value = (None, None)
    # consumo_existentes debe ser un dict real (no un MagicMock) incluso
    # cuando el test no le presta atencion -- calcular_plan_merge itera
    # sobre el resultado con .get()/.items(), que rompe si queda como
    # MagicMock por defecto.
    mock_repo.consumo_existentes.return_value = {}
    # Destino de consumo por defecto (REFACTOR 2026-09-18, segunda vuelta):
    # resolver_bdd_controlado devuelve solo id_bdd (ya no tupla con tipo);
    # resolver_tabla_controlada se mockea aca tambien para no repetirlo en
    # cada test de consumo.
    mock_repo.resolver_bdd_controlado.return_value = 10
    mock_repo.resolver_tabla_controlada.return_value = 30

    monkeypatch.setattr(carga_gdd, "repo", mock_repo)
    return mock_repo


def test_atributo_nuevo_se_inserta_con_los_catalogos_resueltos(monkeypatch, staging_repo_vacio):
    mock_repo = _parchear_repo_gdd(monkeypatch, id_dominio=99)
    engine = MagicMock()

    resultado = carga_gdd.ejecutar_merge_dominio(engine, staging_repo_vacio, "ADS")

    assert resultado.ok
    assert resultado.atributos_insertados == 1
    assert resultado.atributos_actualizados == 0
    mock_repo.insertar_atributo.assert_called_once()
    campos = mock_repo.insertar_atributo.call_args.args[1]
    assert campos["id_dominio"] == 99
    assert campos["codigo_dominio"] == "ADS"
    assert campos["codigo_atributo"] == 1
    assert campos["version"] == "1"  # primera carga -> version inicial
    # id_clasificacion se elimino del modelo (columna sin catalogo/FK ni
    # fuente en el Excel real, siempre se escribia como NULL) -- no debe
    # aparecer en los campos que se mandan a insertar_atributo.
    assert "id_clasificacion" not in campos
    # Bitacora: una linea por la operacion, con el id nuevo y la version.
    mock_repo.registrar_bitacora.assert_called_once()
    assert mock_repo.registrar_bitacora.call_args.args[1:] == (
        "atributo", 555, "INSERTAR", "ADS", "ADS-1", "1",
    )


def test_atributo_primario_busca_por_nombre_y_dominio_juntos(monkeypatch, staging_repo_vacio):
    """Ajuste 2026-09-22: id_atributo_primario/id_dominio_primario se
    resuelven en UNA sola llamada con los dos textos juntos (antes eran dos
    busquedas independientes sin cruce entre si) -- ver
    gdd_repository.buscar_atributo_primario."""
    fila = _fila_detalle()
    fila["atributo_primario"] = "Cupo Aprobado"
    fila["dominio_primario"] = "Cupos"
    staging_repo_vacio.leer.return_value = [fila]
    mock_repo = _parchear_repo_gdd(monkeypatch, id_dominio=99)
    mock_repo.buscar_atributo_primario.return_value = (777, 88)
    engine = MagicMock()

    resultado = carga_gdd.ejecutar_merge_dominio(engine, staging_repo_vacio, "ADS")

    assert resultado.ok, resultado.error
    conn_usada = engine.begin.return_value.__enter__.return_value
    mock_repo.buscar_atributo_primario.assert_called_once_with(
        conn_usada, "Cupo Aprobado", "Cupos"
    )
    campos = mock_repo.insertar_atributo.call_args.args[1]
    assert campos["id_atributo_primario"] == 777
    assert campos["id_dominio_primario"] == 88


def test_atributo_primario_pasa_none_si_falta_uno_de_los_dos_textos(monkeypatch, staging_repo_vacio):
    """Si el Excel solo trae uno de los dos textos (el otro es "-" ->
    None), carga_gdd igual delega la decision en buscar_atributo_primario
    pasandole el None tal cual -- es ESA funcion (gdd_repository.py) la que
    corta y devuelve (None, None) sin resolver parcialmente el que si esta
    informado. Aqui solo se verifica que la orquestacion no resuelve nada
    por su cuenta ni omite la llamada."""
    fila = _fila_detalle()
    fila["atributo_primario"] = "Cupo Aprobado"
    fila["dominio_primario"] = "-"
    staging_repo_vacio.leer.return_value = [fila]
    mock_repo = _parchear_repo_gdd(monkeypatch, id_dominio=99)
    engine = MagicMock()

    resultado = carga_gdd.ejecutar_merge_dominio(engine, staging_repo_vacio, "ADS")

    assert resultado.ok, resultado.error
    conn_usada = engine.begin.return_value.__enter__.return_value
    mock_repo.buscar_atributo_primario.assert_called_once_with(
        conn_usada, "Cupo Aprobado", None
    )
    campos = mock_repo.insertar_atributo.call_args.args[1]
    assert campos["id_atributo_primario"] is None
    assert campos["id_dominio_primario"] is None


def test_bitacora_registra_baja_logica_de_atributo(monkeypatch, staging_repo_vacio):
    staging_repo_vacio.leer.return_value = []  # nada entrante -> el existente se da de baja
    existentes = {("ADS", 1): ExistenteVersionado(id=42, fecha_aprobacion=datetime.date(2025, 1, 31))}
    mock_repo = _parchear_repo_gdd(monkeypatch, existentes_atributo=existentes)
    engine = MagicMock()

    resultado = carga_gdd.ejecutar_merge_dominio(engine, staging_repo_vacio, "ADS")

    assert resultado.ok
    assert resultado.atributos_eliminados == 1
    mock_repo.registrar_bitacora.assert_called_once()
    args = mock_repo.registrar_bitacora.call_args.args
    assert args[1:] == ("atributo", 42, "ELIMINAR", "ADS", "ADS-1", None)


def test_atributo_reemplazado_incrementa_version_y_conserva_release(monkeypatch, staging_repo_vacio):
    staging_repo_vacio.leer.return_value = [_fila_detalle(fecha=datetime.date(2026, 6, 5))]
    existentes = {("ADS", 1): ExistenteVersionado(id=42, fecha_aprobacion=datetime.date(2025, 1, 31))}
    mock_repo = _parchear_repo_gdd(monkeypatch, existentes_atributo=existentes)
    mock_repo.version_release_atributo.return_value = ("3", "R2")
    mock_repo.insertar_atributo.return_value = 777
    engine = MagicMock()

    resultado = carga_gdd.ejecutar_merge_dominio(engine, staging_repo_vacio, "ADS")

    assert resultado.ok
    assert resultado.atributos_reemplazados == 1
    mock_repo.eliminar_atributo.assert_called_once()
    campos = mock_repo.insertar_atributo.call_args.args[1]
    assert campos["version"] == "4"  # 3 + 1
    assert campos["release"] == "R2"  # se conserva, no se pierde en el reemplazo


def test_atributo_actualizado_no_toca_version_ni_release(monkeypatch, staging_repo_vacio):
    """Misma fecha_aprobacion = correccion, no nueva version -- version y
    release no deben aparecer en los campos que se mandan a UPDATE.
    """
    staging_repo_vacio.leer.return_value = [_fila_detalle(fecha=datetime.date(2025, 1, 31))]
    existentes = {("ADS", 1): ExistenteVersionado(id=42, fecha_aprobacion=datetime.date(2025, 1, 31))}
    mock_repo = _parchear_repo_gdd(monkeypatch, existentes_atributo=existentes)
    engine = MagicMock()

    resultado = carga_gdd.ejecutar_merge_dominio(engine, staging_repo_vacio, "ADS")

    assert resultado.ok
    assert resultado.atributos_actualizados == 1
    mock_repo.version_release_atributo.assert_not_called()
    campos = mock_repo.actualizar_atributo.call_args.args[2]
    assert "version" not in campos
    assert "release" not in campos


def test_atributo_sin_cambios_reales_no_actualiza_ni_loguea(monkeypatch, staging_repo_vacio):
    """Misma fecha_aprobacion Y mismos valores -- corrida repetida sin
    cambios reales (ej. el proceso corre 3 veces al dia con el mismo
    Excel). No debe haber UPDATE ni linea de bitacora, solo el contador
    atributos_sin_cambios.
    """
    staging_repo_vacio.leer.return_value = [_fila_detalle(fecha=datetime.date(2025, 1, 31))]
    existentes = {("ADS", 1): ExistenteVersionado(id=42, fecha_aprobacion=datetime.date(2025, 1, 31))}
    mock_repo = _parchear_repo_gdd(monkeypatch, existentes_atributo=existentes)
    campos_fijos = {"nombre_atributo": "Un atributo", "reporte": True}
    monkeypatch.setattr(carga_gdd, "_resolver_campos_atributo", lambda *a, **k: dict(campos_fijos))
    mock_repo.campos_actuales.return_value = dict(campos_fijos)
    engine = MagicMock()

    resultado = carga_gdd.ejecutar_merge_dominio(engine, staging_repo_vacio, "ADS")

    assert resultado.ok
    assert resultado.atributos_actualizados == 0
    assert resultado.atributos_sin_cambios == 1
    mock_repo.actualizar_atributo.assert_not_called()
    mock_repo.registrar_bitacora.assert_not_called()


def test_atributo_ausente_en_staging_se_elimina(monkeypatch, staging_repo_vacio):
    staging_repo_vacio.leer.return_value = []  # nada entrante para ADS
    existentes = {("ADS", 1): ExistenteVersionado(id=42, fecha_aprobacion=datetime.date(2025, 1, 31))}
    mock_repo = _parchear_repo_gdd(monkeypatch, existentes_atributo=existentes)
    engine = MagicMock()

    resultado = carga_gdd.ejecutar_merge_dominio(engine, staging_repo_vacio, "ADS")

    assert resultado.ok
    assert resultado.atributos_eliminados == 1
    conn_usada = engine.begin.return_value.__enter__.return_value
    mock_repo.eliminar_atributo.assert_called_once_with(conn_usada, 42)


@pytest.mark.parametrize(
    "version_actual,version_esperada",
    [(None, "1"), ("1", "2"), ("9", "10"), ("1.0", "1"), ("abc", "1")],
)
def test_siguiente_version(version_actual, version_esperada):
    assert carga_gdd._siguiente_version(version_actual) == version_esperada


def _fila_metadata(**overrides) -> dict:
    base = {
        "codigo_dominio_atributo": "ADS-1",
        "clase": "Ventas",
        "servidor_fuente_oficial": "Servidor de archivos",
        "base_datos_fuente_oficial": "BDD_A",
        "tabla_fuente_oficial": "-",
        "nombre_campo_fuente_oficial": "CAMPO_A",
        "longitud_campo_fuente_oficial": "50",
        "lista_valores_validos": "-",
        "acepta_valores_nulos": "No",
        "tipo_campo": "VARCHAR",
        "formula_calculo": "-",
        "coleccion_foc": "-",
        "servidor_foc": "-",
        "tabla_bv_foc": "-",
        "nombre_campo_foc": "-",
        "fecha_aprobada": "31-01-2025",
    }
    base.update(overrides)
    return base


def test_fuente_sin_cambios_reales_no_actualiza_ni_loguea(monkeypatch):
    """Misma logica que para atributo, aplicada a atributo_fuente_oficial:
    misma fecha_aprobacion Y mismos valores no debe generar UPDATE ni
    linea de bitacora para la fuente.
    """
    staging_repo = MagicMock()
    staging_repo.leer.return_value = [_fila_detalle()]
    staging_repo.leer_por_prefijo.return_value = [_fila_metadata()]

    existentes_atributo = {("ADS", 1): ExistenteVersionado(id=42, fecha_aprobacion=datetime.date(2025, 1, 31))}
    mock_repo = _parchear_repo_gdd(monkeypatch, existentes_atributo=existentes_atributo)
    mock_repo.resolver_o_crear_servidor.return_value = 1
    mock_repo.resolver_o_crear_bdd.return_value = 10

    clave_fuente = ("Ventas", "CAMPO_A", 10, True, "")
    mock_repo.fuente_oficial_existentes.return_value = {
        clave_fuente: ExistenteVersionado(id=77, fecha_aprobacion=datetime.date(2025, 1, 31))
    }

    campos_fijos = {"nombre_campo": "CAMPO_A", "clase": "Ventas"}
    monkeypatch.setattr(carga_gdd, "_resolver_campos_fuente", lambda *a, **k: dict(campos_fijos))
    mock_repo.campos_actuales.return_value = dict(campos_fijos)
    engine = MagicMock()

    resultado = carga_gdd.ejecutar_merge_dominio(engine, staging_repo, "ADS")

    assert resultado.ok, resultado.error
    assert resultado.fuentes_actualizadas == 0
    assert resultado.fuentes_sin_cambios == 1
    mock_repo.actualizar_fuente_oficial.assert_not_called()
    llamadas_fuente = [
        c for c in mock_repo.registrar_bitacora.call_args_list
        if c.args[1] == "atributo_fuente_oficial"
    ]
    assert llamadas_fuente == []


def test_fuentes_con_misma_clase_pero_distinto_campo_no_se_colapsan(monkeypatch):
    """Regresion del bug real: (clase, es_fuente_primaria) como clave
    natural colapsaba filas de metadata_tecnica que comparten `clase` pero
    vienen de sistemas/campos fuente distintos (confirmado: 210 filas
    reales de ADS colapsaban a 94). La clave correcta incluye tambien
    nombre_campo e id_bdd.
    """
    staging_repo = MagicMock()
    staging_repo.leer.return_value = [_fila_detalle()]
    staging_repo.leer_por_prefijo.return_value = [
        _fila_metadata(nombre_campo_fuente_oficial="CAMPO_A", base_datos_fuente_oficial="BDD_A"),
        _fila_metadata(nombre_campo_fuente_oficial="CAMPO_B", base_datos_fuente_oficial="BDD_B"),
    ]
    mock_repo = _parchear_repo_gdd(monkeypatch, id_dominio=99)
    mock_repo.resolver_o_crear_servidor.return_value = 1
    mock_repo.resolver_o_crear_bdd.side_effect = [10, 20]
    engine = MagicMock()

    resultado = carga_gdd.ejecutar_merge_dominio(engine, staging_repo, "ADS")

    assert resultado.ok, resultado.error
    # Antes del fix, esto daba 1 (la segunda fila pisaba a la primera en el dict).
    assert resultado.fuentes_insertadas == 2
    assert mock_repo.insertar_fuente_oficial.call_count == 2
    # Una linea de bitacora por cada fuente insertada (mas la del atributo
    # en si, que tambien se inserta nuevo en este escenario).
    llamadas_fuente = [
        c for c in mock_repo.registrar_bitacora.call_args_list
        if c.args[1] == "atributo_fuente_oficial"
    ]
    assert len(llamadas_fuente) == 2
    assert {c.args[3] for c in llamadas_fuente} == {"INSERTAR"}


def test_fuentes_con_misma_clave_anterior_pero_distinta_tabla_no_se_colapsan(monkeypatch):
    """Regresion 2026-10-01 (datos reales: category_id de Catalogo_compras en
    PROD1 venia de 3 tablas distintas y quedaba 1 sola fuente): mismo
    servidor, base, clase y campo, distinta tabla_fuente_oficial -> 3 fuentes,
    cada una con su nombre_tabla."""
    staging_repo = MagicMock()
    staging_repo.leer.return_value = [_fila_detalle()]
    staging_repo.leer_por_prefijo.return_value = [
        _fila_metadata(tabla_fuente_oficial=t) for t in ("MTL_CATEGORIES", "MTL_ITEM_CATEGORIES", "PO_LINES")
    ]
    mock_repo = _parchear_repo_gdd(monkeypatch, id_dominio=99)
    mock_repo.resolver_o_crear_servidor.return_value = 1
    mock_repo.resolver_o_crear_bdd.return_value = 10
    engine = MagicMock()

    resultado = carga_gdd.ejecutar_merge_dominio(engine, staging_repo, "ADS")

    assert resultado.ok, resultado.error
    assert resultado.fuentes_insertadas == 3
    tablas = [c.args[1]["nombre_tabla"] for c in mock_repo.insertar_fuente_oficial.call_args_list]
    assert sorted(tablas) == ["MTL_CATEGORIES", "MTL_ITEM_CATEGORIES", "PO_LINES"]


def test_fuente_existente_con_misma_tabla_se_actualiza_no_se_reinserta(monkeypatch):
    staging_repo = MagicMock()
    staging_repo.leer.return_value = [_fila_detalle()]
    staging_repo.leer_por_prefijo.return_value = [_fila_metadata(tabla_fuente_oficial="PO_LINES")]
    existentes_atributo = {("ADS", 1): ExistenteVersionado(id=42, fecha_aprobacion=datetime.date(2025, 1, 31))}
    mock_repo = _parchear_repo_gdd(monkeypatch, existentes_atributo=existentes_atributo)
    mock_repo.resolver_o_crear_servidor.return_value = 1
    mock_repo.resolver_o_crear_bdd.return_value = 10
    mock_repo.fuente_oficial_existentes.return_value = {
        ("Ventas", "CAMPO_A", 10, True, "PO_LINES"): ExistenteVersionado(
            id=77, fecha_aprobacion=datetime.date(2025, 1, 31))
    }
    mock_repo.campos_actuales.return_value = {}
    engine = MagicMock()

    resultado = carga_gdd.ejecutar_merge_dominio(engine, staging_repo, "ADS")

    assert resultado.ok, resultado.error
    assert resultado.fuentes_insertadas == 0 and resultado.fuentes_eliminadas == 0
    mock_repo.insertar_fuente_oficial.assert_not_called()



def test_columnas_foc_van_solo_a_consumo_no_a_fuente_oficial(monkeypatch):
    """Correccion 2026-10-01: con las 4 columnas _foc informadas se inserta
    UNA fuente oficial (la primaria, con datos _oficial) y UN consumo
    (resuelto contra los catalogos controlados). Ningun valor _foc pasa por
    el get-or-create de servidor/base de datos de la fuente oficial."""
    staging_repo = MagicMock()
    staging_repo.leer.return_value = [_fila_detalle()]
    staging_repo.leer_por_prefijo.return_value = [_fila_metadata(
        servidor_foc="Stratio", coleccion_foc="ZB_ZN_TN_CAT",
        tabla_bv_foc="ZP_BP_Acp_Fin_TD_Articulo", nombre_campo_foc="COD_ARTICULO")]
    mock_repo = _parchear_repo_gdd(monkeypatch)
    mock_repo.resolver_o_crear_servidor.side_effect = lambda conn, nombre: {"Servidor de archivos": 1, "Stratio": 2}[nombre]
    mock_repo.resolver_o_crear_bdd.return_value = 10

    resultado = carga_gdd.ejecutar_merge_dominio(MagicMock(), staging_repo, "ADS")

    assert resultado.ok, resultado.error
    assert resultado.fuentes_insertadas == 1 and resultado.consumos_insertados == 1
    [insercion] = mock_repo.insertar_fuente_oficial.call_args_list
    assert insercion.args[1]["es_fuente_primaria"] is True
    assert insercion.args[1]["nombre_campo"] == "CAMPO_A"
    # base de datos de la fuente oficial: solo la _oficial, nunca tabla_bv_foc
    assert [c.args[2] for c in mock_repo.resolver_o_crear_bdd.call_args_list] == ["BDD_A"]
    mock_repo.resolver_bdd_controlado.assert_called_once()
    assert mock_repo.resolver_bdd_controlado.call_args.args[1:] == (2, "ZB_ZN_TN_CAT")
    assert mock_repo.resolver_tabla_controlada.call_args.args[1:] == (10, "ZP_BP_Acp_Fin_TD_Articulo")


def test_fuente_secundaria_foc_existente_se_da_de_baja(monkeypatch):
    """Una fuente secundaria (es_fuente_primaria=0) que quedo de la logica
    anterior ya no viene del Excel -> ELIMINAR (baja logica) en la carga."""
    staging_repo = MagicMock()
    staging_repo.leer.return_value = [_fila_detalle()]
    staging_repo.leer_por_prefijo.return_value = [_fila_metadata()]
    existentes_atributo = {("ADS", 1): ExistenteVersionado(id=42, fecha_aprobacion=datetime.date(2025, 1, 31))}
    mock_repo = _parchear_repo_gdd(monkeypatch, existentes_atributo=existentes_atributo)
    mock_repo.resolver_o_crear_servidor.return_value = 1
    mock_repo.resolver_o_crear_bdd.return_value = 10
    mock_repo.campos_actuales.return_value = {}
    mock_repo.fuente_oficial_existentes.return_value = {
        ("Ventas", "CAMPO_A", 10, True, ""): ExistenteVersionado(id=77, fecha_aprobacion=datetime.date(2025, 1, 31)),
        ("ZB_ZN_TN_CAT", "COD_ARTICULO", 34, False, ""): ExistenteVersionado(
            id=88, fecha_aprobacion=datetime.date(2025, 1, 31)),
    }

    resultado = carga_gdd.ejecutar_merge_dominio(MagicMock(), staging_repo, "ADS")

    assert resultado.ok, resultado.error
    assert resultado.fuentes_eliminadas == 1
    mock_repo.eliminar_fuente_oficial.assert_called_once_with(mock_repo.eliminar_fuente_oficial.call_args.args[0], 88)
    mock_repo.insertar_fuente_oficial.assert_not_called()

# --- atributo_fuente_consumo (2026-09-17, alcance ampliado; REFACTORIZADA
# 2026-09-18, dos vueltas -- ver nota de modulo de carga_gdd.py) ---
#
# Se mergea DENTRO de ejecutar_merge_dominio (misma transaccion que
# atributo/fuente_oficial). coleccion_foc/tabla_bv_foc se resuelven SIEMPRE
# contra catalogos controlados (resolver_bdd_controlado/
# resolver_tabla_controlada), sin excepcion de texto libre; id_servidor NO
# se guarda (se alcanza via base_datos_fuente.id_servidor, igual que
# atributo_fuente_oficial).


def test_consumo_sin_datos_foc_no_genera_operaciones(monkeypatch):
    staging_repo = MagicMock()
    staging_repo.leer.return_value = [_fila_detalle()]
    staging_repo.leer_por_prefijo.return_value = [_fila_metadata()]  # FOC = "-"
    mock_repo = _parchear_repo_gdd(monkeypatch)
    engine = MagicMock()

    resultado = carga_gdd.ejecutar_merge_dominio(engine, staging_repo, "ADS")

    assert resultado.ok, resultado.error
    assert resultado.consumos_insertados == 0
    mock_repo.insertar_atributo_fuente_consumo.assert_not_called()


def test_consumo_nuevo_resuelve_bdd_y_tabla_controladas(monkeypatch):
    """tabla_bv_foc se resuelve SIEMPRE contra gdd.base_datos_fuente_tabla
    (REFACTOR 2026-09-18, segunda vuelta) -- sin excepcion por tipo. El
    servidor se resuelve solo como paso intermedio y NO aparece en los
    campos a insertar."""
    staging_repo = MagicMock()
    staging_repo.leer.return_value = [_fila_detalle()]
    staging_repo.leer_por_prefijo.return_value = [
        _fila_metadata(
            coleccion_foc="BDD_VENTAS", servidor_foc="BI01", tabla_bv_foc="VW_VENTAS",
            nombre_campo_foc="ID_PRODUCTO",
        )
    ]
    mock_repo = _parchear_repo_gdd(monkeypatch)
    mock_repo.resolver_o_crear_servidor.return_value = 5
    mock_repo.resolver_bdd_controlado.return_value = 10
    mock_repo.resolver_tabla_controlada.return_value = 300
    mock_repo.insertar_atributo_fuente_consumo.return_value = 900
    engine = MagicMock()

    resultado = carga_gdd.ejecutar_merge_dominio(engine, staging_repo, "ADS")

    assert resultado.ok, resultado.error
    assert resultado.consumos_insertados == 1
    conn_usada = engine.begin.return_value.__enter__.return_value
    mock_repo.resolver_bdd_controlado.assert_called_once_with(conn_usada, 5, "BDD_VENTAS")
    mock_repo.resolver_tabla_controlada.assert_called_once_with(conn_usada, 10, "VW_VENTAS")
    campos = mock_repo.insertar_atributo_fuente_consumo.call_args.args[1]
    assert campos["id_bdd"] == 10
    assert campos["id_tabla"] == 300
    assert campos["nombre_campo"] == "ID_PRODUCTO"
    assert "id_servidor" not in campos
    assert "nombre_tabla_libre" not in campos
    # Sin bitacora para esta tabla (decision explicita, igual que plan_remediacion).
    llamadas_consumo = [
        c for c in mock_repo.registrar_bitacora.call_args_list
        if c.args[1] == "atributo_fuente_consumo"
    ]
    assert llamadas_consumo == []


def test_consumo_coleccion_foc_no_encontrada_aborta_el_dominio(monkeypatch):
    """coleccion_foc es un catalogo CONTROLADO -- si no calza con ninguna fila
    de gdd.base_datos_fuente, se aborta el dominio completo, igual que
    cualquier CatalogoNoEncontradoError."""
    staging_repo = MagicMock()
    staging_repo.leer.return_value = [_fila_detalle()]
    staging_repo.leer_por_prefijo.return_value = [
        _fila_metadata(
            coleccion_foc="NO_EXISTE", servidor_foc="BI01", tabla_bv_foc="VW_VENTAS",
            nombre_campo_foc="ID_PRODUCTO",
        )
    ]
    mock_repo = _parchear_repo_gdd(monkeypatch)
    mock_repo.resolver_bdd_controlado.side_effect = CatalogoNoEncontradoError("no existe")
    engine = MagicMock()

    resultado = carga_gdd.ejecutar_merge_dominio(engine, staging_repo, "ADS")

    assert not resultado.ok
    assert "no existe" in resultado.error


def test_consumo_tabla_bv_foc_no_encontrada_aborta_el_dominio(monkeypatch):
    """tabla_bv_foc tambien es un catalogo CONTROLADO (REFACTOR 2026-09-18,
    segunda vuelta) -- si no calza con ninguna fila de
    gdd.base_datos_fuente_tabla, se aborta el dominio, igual que
    coleccion_foc."""
    staging_repo = MagicMock()
    staging_repo.leer.return_value = [_fila_detalle()]
    staging_repo.leer_por_prefijo.return_value = [
        _fila_metadata(
            coleccion_foc="BDD_VENTAS", servidor_foc="BI01", tabla_bv_foc="NO_EXISTE",
            nombre_campo_foc="ID_PRODUCTO",
        )
    ]
    mock_repo = _parchear_repo_gdd(monkeypatch)
    mock_repo.resolver_tabla_controlada.side_effect = CatalogoNoEncontradoError("no existe")
    engine = MagicMock()

    resultado = carga_gdd.ejecutar_merge_dominio(engine, staging_repo, "ADS")

    assert not resultado.ok
    assert "no existe" in resultado.error


def test_consumo_existente_con_misma_clave_no_genera_insert(monkeypatch):
    """Ya no quedan campos mutables: si la clave natural completa (id_bdd,
    id_tabla, nombre_campo) ya existe, se cuenta como sin_cambios sin
    insertar de nuevo."""
    staging_repo = MagicMock()
    staging_repo.leer.return_value = [_fila_detalle()]
    staging_repo.leer_por_prefijo.return_value = [
        _fila_metadata(
            coleccion_foc="BDD_VENTAS", servidor_foc="BI01", tabla_bv_foc="VW_VENTAS",
            nombre_campo_foc="ID_PRODUCTO",
        )
    ]
    mock_repo = _parchear_repo_gdd(monkeypatch)
    mock_repo.resolver_bdd_controlado.return_value = 10
    mock_repo.resolver_tabla_controlada.return_value = 300
    mock_repo.consumo_existentes.return_value = {
        (10, 300, "ID_PRODUCTO"): ExistenteVersionado(id=88, fecha_aprobacion=None)
    }
    engine = MagicMock()

    resultado = carga_gdd.ejecutar_merge_dominio(engine, staging_repo, "ADS")

    assert resultado.ok, resultado.error
    assert resultado.consumos_sin_cambios == 1
    assert resultado.consumos_actualizados == 0
    mock_repo.insertar_atributo_fuente_consumo.assert_not_called()


def test_consumo_ausente_del_excel_se_da_de_baja(monkeypatch):
    """El destino de consumo ya no aparece en el Excel (columnas _foc
    vacias) -- baja logica, mismo patron que respaldo/investigacion/
    plan_remediacion."""
    staging_repo = MagicMock()
    staging_repo.leer.return_value = [_fila_detalle()]
    staging_repo.leer_por_prefijo.return_value = [_fila_metadata()]  # sin datos _foc ahora
    mock_repo = _parchear_repo_gdd(monkeypatch)
    mock_repo.consumo_existentes.return_value = {
        (10, 300, "ID_PRODUCTO"): ExistenteVersionado(id=88, fecha_aprobacion=None)
    }
    engine = MagicMock()

    resultado = carga_gdd.ejecutar_merge_dominio(engine, staging_repo, "ADS")

    assert resultado.ok, resultado.error
    assert resultado.consumos_eliminados == 1
    mock_repo.eliminar_atributo_fuente_consumo.assert_called_once()
    assert mock_repo.eliminar_atributo_fuente_consumo.call_args.args[1] == 88


def test_consumo_se_agrupa_por_atributo_dos_filas_dos_destinos(monkeypatch):
    """Confirmado con el usuario: un atributo puede tener mas de un destino
    de consumo, repitiendo codigo_dominio_atributo en varias filas."""
    staging_repo = MagicMock()
    staging_repo.leer.return_value = [_fila_detalle()]
    staging_repo.leer_por_prefijo.return_value = [
        _fila_metadata(coleccion_foc="Vista", servidor_foc="BI01", tabla_bv_foc="VW_VENTAS", nombre_campo_foc="ID_PRODUCTO"),
        _fila_metadata(coleccion_foc="Vista", servidor_foc="BI02", tabla_bv_foc="VW_REPORTES", nombre_campo_foc="COD_PRODUCTO"),
    ]
    mock_repo = _parchear_repo_gdd(monkeypatch)
    # Constante a proposito: tambien lo llama _resolver_id_bdd para las 2
    # filas de fuente_oficial primaria (servidor_fuente_oficial no vacio en
    # _fila_metadata) -- lo que distingue los 2 destinos de consumo aqui es
    # nombre_tabla/nombre_campo, no id_servidor.
    mock_repo.resolver_o_crear_servidor.return_value = 7
    engine = MagicMock()

    resultado = carga_gdd.ejecutar_merge_dominio(engine, staging_repo, "ADS")

    assert resultado.ok, resultado.error
    assert resultado.consumos_insertados == 2
    assert mock_repo.insertar_atributo_fuente_consumo.call_count == 2


def test_dominio_no_encontrado_aborta_solo_ese_dominio(monkeypatch, staging_repo_vacio):
    mock_repo = MagicMock()
    mock_repo.resolver_dominio_id.side_effect = CatalogoNoEncontradoError("no existe")
    monkeypatch.setattr(carga_gdd, "repo", mock_repo)
    engine = MagicMock()

    resultado = carga_gdd.ejecutar_merge_dominio(engine, staging_repo_vacio, "ADS")

    assert not resultado.ok
    assert "no existe" in resultado.error
    assert resultado.atributos_insertados == 0


# --- ejecutar_merge_investigacion_dominio: funcion aparte, transaccion aparte
# (no comparte fixtures con ejecutar_merge_dominio -- gdd.investigacion no
# tiene FK hacia atributo/fuente_oficial). ---


def _fila_investigacion(**overrides) -> dict:
    base = {
        "codigo_dominio": "ADS",
        "clasificacion": "Normativas e instructivos",
        "nombre": "Código de comercio",
        "descripcion": "Rige las obligaciones de los comerciantes.",
        "referencia_normativa": "Resolución de la Superintendencia de Compañías 6.",
    }
    base.update(overrides)
    return base


def _parchear_repo_investigacion(monkeypatch, *, id_dominio=99, existentes=None):
    mock_repo = MagicMock()
    mock_repo.resolver_dominio_id.return_value = id_dominio
    mock_repo.resolver_catalogo_controlado.return_value = 7
    mock_repo.investigacion_existentes.return_value = existentes or {}
    mock_repo.insertar_investigacion.return_value = 555
    mock_repo.campos_actuales.return_value = {}
    monkeypatch.setattr(carga_gdd, "repo", mock_repo)
    return mock_repo


def test_investigacion_nueva_se_inserta(monkeypatch):
    staging_repo = MagicMock()
    staging_repo.leer.return_value = [_fila_investigacion()]
    mock_repo = _parchear_repo_investigacion(monkeypatch)
    engine = MagicMock()

    resultado = carga_gdd.ejecutar_merge_investigacion_dominio(engine, staging_repo, "ADS")

    assert resultado.ok
    assert resultado.insertadas == 1
    assert resultado.actualizadas == 0
    assert resultado.eliminadas == 0
    assert mock_repo.insertar_investigacion.call_args.args[1] == {
        "id_dominio": 99,
        "codigo_dominio": "ADS",
        "id_clasificacion": 7,
        "nombre": "Código de comercio",
        "descripcion": "Rige las obligaciones de los comerciantes.",
        "referencia_normativa": "Resolución de la Superintendencia de Compañías 6.",
    }
    mock_repo.actualizar_investigacion.assert_not_called()
    mock_repo.eliminar_investigacion.assert_not_called()


def test_investigacion_existente_con_cambios_se_actualiza_in_place(monkeypatch):
    staging_repo = MagicMock()
    staging_repo.leer.return_value = [_fila_investigacion(descripcion="Descripcion nueva")]
    existentes = {"Código de comercio": ExistenteVersionado(id=42, fecha_aprobacion=None)}
    mock_repo = _parchear_repo_investigacion(monkeypatch, existentes=existentes)
    # lo que hay hoy en BD (distinto a lo que trae el Excel -> hay cambio real)
    mock_repo.campos_actuales.return_value = {
        "id_clasificacion": 7, "descripcion": "Descripcion vieja",
        "referencia_normativa": "Resolución de la Superintendencia de Compañías 6.",
    }
    engine = MagicMock()

    resultado = carga_gdd.ejecutar_merge_investigacion_dominio(engine, staging_repo, "ADS")

    assert resultado.ok
    assert resultado.actualizadas == 1
    assert resultado.sin_cambios == 0
    assert resultado.insertadas == 0
    mock_repo.actualizar_investigacion.assert_called_once()
    assert mock_repo.actualizar_investigacion.call_args.args[1] == 42
    # NO se inserta una fila nueva ni se da de baja la existente -- UPDATE in-place
    mock_repo.insertar_investigacion.assert_not_called()
    mock_repo.eliminar_investigacion.assert_not_called()


def test_investigacion_existente_sin_cambios_no_actualiza(monkeypatch):
    """El bug real que detecto el usuario probando la version anterior
    (reemplazo completo por dominio): correr el merge varias veces al dia
    sin cambios en el Excel no debe tocar filas que ya estan iguales en BD.
    """
    staging_repo = MagicMock()
    staging_repo.leer.return_value = [_fila_investigacion()]
    existentes = {"Código de comercio": ExistenteVersionado(id=42, fecha_aprobacion=None)}
    mock_repo = _parchear_repo_investigacion(monkeypatch, existentes=existentes)
    # lo que hay hoy en BD ya coincide con lo que trae el Excel
    mock_repo.campos_actuales.return_value = {
        "id_clasificacion": 7, "descripcion": "Rige las obligaciones de los comerciantes.",
        "referencia_normativa": "Resolución de la Superintendencia de Compañías 6.",
    }
    engine = MagicMock()

    resultado = carga_gdd.ejecutar_merge_investigacion_dominio(engine, staging_repo, "ADS")

    assert resultado.ok
    assert resultado.sin_cambios == 1
    assert resultado.actualizadas == 0
    mock_repo.actualizar_investigacion.assert_not_called()
    mock_repo.insertar_investigacion.assert_not_called()
    mock_repo.eliminar_investigacion.assert_not_called()


def test_investigacion_solo_referencia_normativa_distinta_se_actualiza(monkeypatch):
    # referencia_normativa es mutable igual que descripcion -- un cambio
    # solo ahi (mismo resto de campos) debe disparar ACTUALIZAR.
    staging_repo = MagicMock()
    staging_repo.leer.return_value = [_fila_investigacion(referencia_normativa="Referencia nueva.")]
    existentes = {"Código de comercio": ExistenteVersionado(id=42, fecha_aprobacion=None)}
    mock_repo = _parchear_repo_investigacion(monkeypatch, existentes=existentes)
    mock_repo.campos_actuales.return_value = {
        "id_clasificacion": 7, "descripcion": "Rige las obligaciones de los comerciantes.",
        "referencia_normativa": "Referencia vieja.",
    }
    engine = MagicMock()

    resultado = carga_gdd.ejecutar_merge_investigacion_dominio(engine, staging_repo, "ADS")

    assert resultado.ok
    assert resultado.actualizadas == 1
    assert resultado.sin_cambios == 0
    mock_repo.actualizar_investigacion.assert_called_once()


def test_investigacion_ausente_del_excel_se_da_de_baja(monkeypatch):
    staging_repo = MagicMock()
    staging_repo.leer.return_value = []  # el Excel ya no trae esta fila
    existentes = {"Código de comercio": ExistenteVersionado(id=42, fecha_aprobacion=None)}
    mock_repo = _parchear_repo_investigacion(monkeypatch, existentes=existentes)
    engine = MagicMock()

    resultado = carga_gdd.ejecutar_merge_investigacion_dominio(engine, staging_repo, "ADS")

    assert resultado.ok
    assert resultado.eliminadas == 1
    mock_repo.eliminar_investigacion.assert_called_once()
    assert mock_repo.eliminar_investigacion.call_args.args[1] == 42
    mock_repo.insertar_investigacion.assert_not_called()
    mock_repo.actualizar_investigacion.assert_not_called()


def test_investigacion_dominio_no_encontrado_aborta(monkeypatch):
    staging_repo = MagicMock()
    staging_repo.leer.return_value = [_fila_investigacion()]

    mock_repo = MagicMock()
    mock_repo.resolver_dominio_id.side_effect = CatalogoNoEncontradoError("no existe")
    monkeypatch.setattr(carga_gdd, "repo", mock_repo)
    engine = MagicMock()

    resultado = carga_gdd.ejecutar_merge_investigacion_dominio(engine, staging_repo, "ADS")

    assert not resultado.ok
    assert "no existe" in resultado.error
    mock_repo.insertar_investigacion.assert_not_called()


def test_investigacion_clasificacion_desconocida_aborta(monkeypatch):
    staging_repo = MagicMock()
    staging_repo.leer.return_value = [_fila_investigacion(clasificacion="Inventada")]

    mock_repo = _parchear_repo_investigacion(monkeypatch)
    mock_repo.resolver_catalogo_controlado.side_effect = CatalogoNoEncontradoError(
        "gdd.cat_clasificacion_investigacion: no existe ninguna fila con nombre_clasificacion = 'Inventada'."
    )
    engine = MagicMock()

    resultado = carga_gdd.ejecutar_merge_investigacion_dominio(engine, staging_repo, "ADS")

    assert not resultado.ok
    assert "Inventada" in resultado.error
    mock_repo.insertar_investigacion.assert_not_called()


def test_investigacion_fila_sin_nombre_aborta_antes_de_tocar_bd(monkeypatch):
    staging_repo = MagicMock()
    staging_repo.leer.return_value = [_fila_investigacion(nombre=None)]

    mock_repo = MagicMock()
    monkeypatch.setattr(carga_gdd, "repo", mock_repo)
    engine = MagicMock()

    resultado = carga_gdd.ejecutar_merge_investigacion_dominio(engine, staging_repo, "ADS")

    assert not resultado.ok
    mock_repo.resolver_dominio_id.assert_not_called()


# --- ejecutar_merge_estructura_dominio: funcion aparte, transaccion aparte
# (no comparte fixtures con ejecutar_merge_dominio/investigacion --
# gdd.dominio_responsable no tiene FK hacia esas tablas). ---


def _fila_estructura(**overrides) -> dict:
    base = {
        "codigo_dominio": "ADS",
        "rol": "Dueño de Dominio",
        "area": "Tribu Producto Banca Relacional",
        "codigo_plaza": "1000921",
        "nombre_plaza": "Dueño de Producto Sr",
        "nombre_responsable": "Xavier Santiago Cabrera Rivadeneira",
        "fecha_aprobada": datetime.date(2024, 5, 10),
    }
    base.update(overrides)
    return base


def _parchear_repo_estructura(monkeypatch, *, id_dominio=99, existentes=None):
    mock_repo = MagicMock()
    mock_repo.resolver_dominio_id.return_value = id_dominio
    mock_repo.resolver_catalogo_controlado.return_value = 7
    mock_repo.dominio_responsable_existentes.return_value = existentes or {}
    mock_repo.insertar_dominio_responsable.return_value = 555
    mock_repo.campos_actuales.return_value = {}
    monkeypatch.setattr(carga_gdd, "repo", mock_repo)
    return mock_repo


def test_estructura_nueva_se_inserta(monkeypatch):
    staging_repo = MagicMock()
    staging_repo.leer.return_value = [_fila_estructura()]
    mock_repo = _parchear_repo_estructura(monkeypatch)
    engine = MagicMock()

    resultado = carga_gdd.ejecutar_merge_estructura_dominio(engine, staging_repo, "ADS")

    assert resultado.ok
    assert resultado.insertadas == 1
    assert resultado.actualizadas == 0
    assert resultado.eliminadas == 0
    assert mock_repo.insertar_dominio_responsable.call_args.args[1] == {
        "id_dominio": 99,
        "id_rol": 7,
        "area": "Tribu Producto Banca Relacional",
        "codigo_plaza": "1000921",
        "nombre_plaza": "Dueño de Producto Sr",
        "nombre_responsable": "Xavier Santiago Cabrera Rivadeneira",
        "fecha_aprobada": datetime.date(2024, 5, 10),
    }
    mock_repo.actualizar_dominio_responsable.assert_not_called()
    mock_repo.eliminar_dominio_responsable.assert_not_called()


def test_estructura_existente_con_cambios_se_actualiza_in_place(monkeypatch):
    staging_repo = MagicMock()
    staging_repo.leer.return_value = [_fila_estructura(nombre_responsable="Otra Persona")]
    existentes = {"1000921": ExistenteVersionado(id=42, fecha_aprobacion=None)}
    mock_repo = _parchear_repo_estructura(monkeypatch, existentes=existentes)
    # lo que hay hoy en BD (distinto a lo que trae el Excel -> hay cambio real)
    mock_repo.campos_actuales.return_value = {
        "id_rol": 7,
        "area": "Tribu Producto Banca Relacional",
        "nombre_plaza": "Dueño de Producto Sr",
        "nombre_responsable": "Xavier Santiago Cabrera Rivadeneira",
        "fecha_aprobada": datetime.date(2024, 5, 10),
    }
    engine = MagicMock()

    resultado = carga_gdd.ejecutar_merge_estructura_dominio(engine, staging_repo, "ADS")

    assert resultado.ok
    assert resultado.actualizadas == 1
    assert resultado.sin_cambios == 0
    assert resultado.insertadas == 0
    mock_repo.actualizar_dominio_responsable.assert_called_once()
    assert mock_repo.actualizar_dominio_responsable.call_args.args[1] == 42
    # NO se inserta una fila nueva ni se da de baja la existente -- UPDATE in-place
    mock_repo.insertar_dominio_responsable.assert_not_called()
    mock_repo.eliminar_dominio_responsable.assert_not_called()


def test_estructura_existente_sin_cambios_no_actualiza(monkeypatch):
    staging_repo = MagicMock()
    staging_repo.leer.return_value = [_fila_estructura()]
    existentes = {"1000921": ExistenteVersionado(id=42, fecha_aprobacion=None)}
    mock_repo = _parchear_repo_estructura(monkeypatch, existentes=existentes)
    # lo que hay hoy en BD ya coincide con lo que trae el Excel
    mock_repo.campos_actuales.return_value = {
        "id_rol": 7,
        "area": "Tribu Producto Banca Relacional",
        "nombre_plaza": "Dueño de Producto Sr",
        "nombre_responsable": "Xavier Santiago Cabrera Rivadeneira",
        "fecha_aprobada": datetime.date(2024, 5, 10),
    }
    engine = MagicMock()

    resultado = carga_gdd.ejecutar_merge_estructura_dominio(engine, staging_repo, "ADS")

    assert resultado.ok
    assert resultado.sin_cambios == 1
    assert resultado.actualizadas == 0
    mock_repo.actualizar_dominio_responsable.assert_not_called()
    mock_repo.insertar_dominio_responsable.assert_not_called()
    mock_repo.eliminar_dominio_responsable.assert_not_called()


def test_estructura_ausente_del_excel_se_da_de_baja(monkeypatch):
    staging_repo = MagicMock()
    staging_repo.leer.return_value = []  # el Excel ya no trae esta plaza
    existentes = {"1000921": ExistenteVersionado(id=42, fecha_aprobacion=None)}
    mock_repo = _parchear_repo_estructura(monkeypatch, existentes=existentes)
    engine = MagicMock()

    resultado = carga_gdd.ejecutar_merge_estructura_dominio(engine, staging_repo, "ADS")

    assert resultado.ok
    assert resultado.eliminadas == 1
    mock_repo.eliminar_dominio_responsable.assert_called_once()
    assert mock_repo.eliminar_dominio_responsable.call_args.args[1] == 42
    mock_repo.insertar_dominio_responsable.assert_not_called()
    mock_repo.actualizar_dominio_responsable.assert_not_called()


def test_estructura_dominio_no_encontrado_aborta(monkeypatch):
    staging_repo = MagicMock()
    staging_repo.leer.return_value = [_fila_estructura()]

    mock_repo = MagicMock()
    mock_repo.resolver_dominio_id.side_effect = CatalogoNoEncontradoError("no existe")
    monkeypatch.setattr(carga_gdd, "repo", mock_repo)
    engine = MagicMock()

    resultado = carga_gdd.ejecutar_merge_estructura_dominio(engine, staging_repo, "ADS")

    assert not resultado.ok
    assert "no existe" in resultado.error
    mock_repo.insertar_dominio_responsable.assert_not_called()


def test_estructura_rol_desconocido_aborta(monkeypatch):
    staging_repo = MagicMock()
    staging_repo.leer.return_value = [_fila_estructura(rol="Rol Inventado")]

    mock_repo = _parchear_repo_estructura(monkeypatch)
    mock_repo.resolver_catalogo_controlado.side_effect = CatalogoNoEncontradoError(
        "gdd.cat_rol: no existe ninguna fila con nombre_rol = 'Rol Inventado'."
    )
    engine = MagicMock()

    resultado = carga_gdd.ejecutar_merge_estructura_dominio(engine, staging_repo, "ADS")

    assert not resultado.ok
    assert "Rol Inventado" in resultado.error
    mock_repo.insertar_dominio_responsable.assert_not_called()


def test_estructura_fila_sin_codigo_plaza_aborta_antes_de_tocar_bd(monkeypatch):
    staging_repo = MagicMock()
    staging_repo.leer.return_value = [_fila_estructura(codigo_plaza=None)]

    mock_repo = MagicMock()
    monkeypatch.setattr(carga_gdd, "repo", mock_repo)
    engine = MagicMock()

    resultado = carga_gdd.ejecutar_merge_estructura_dominio(engine, staging_repo, "ADS")

    assert not resultado.ok
    mock_repo.resolver_dominio_id.assert_not_called()


# --- ejecutar_merge_respaldos_dominio: funcion aparte, transaccion aparte
# (no comparte fixtures con ejecutar_merge_dominio/investigacion/estructura
# -- gdd.respaldo no tiene FK hacia esas tablas). A diferencia de esas, la
# clave natural incluye la parte resuelta contra catalogo (id_tipo_respaldo),
# ver _clave_respaldo. ---

_ENLACE = "https://ejemplo.atlassian.net/wiki/spaces/DEMO/pages/1000"


def _fila_respaldo(**overrides) -> dict:
    base = {
        "codigo_dominio": "ADS",
        "tipo_respaldo": "Correo",
        "enlace_respaldo": _ENLACE,
        "observaciones": "Ninguna",
        "fecha_aprobada": "2026/15/09",
    }
    base.update(overrides)
    return base


def _parchear_repo_respaldos(monkeypatch, *, id_dominio=99, id_tipo_respaldo=7, existentes=None):
    mock_repo = MagicMock()
    mock_repo.resolver_dominio_id.return_value = id_dominio
    mock_repo.resolver_catalogo_controlado.return_value = id_tipo_respaldo
    mock_repo.respaldo_existentes.return_value = existentes or {}
    mock_repo.insertar_respaldo.return_value = 555
    mock_repo.campos_actuales.return_value = {}
    monkeypatch.setattr(carga_gdd, "repo", mock_repo)
    return mock_repo


def test_respaldo_nuevo_se_inserta(monkeypatch):
    staging_repo = MagicMock()
    staging_repo.leer.return_value = [_fila_respaldo()]
    mock_repo = _parchear_repo_respaldos(monkeypatch)
    engine = MagicMock()

    resultado = carga_gdd.ejecutar_merge_respaldos_dominio(engine, staging_repo, "ADS")

    assert resultado.ok
    assert resultado.insertadas == 1
    assert resultado.actualizadas == 0
    assert resultado.eliminadas == 0
    assert mock_repo.insertar_respaldo.call_args.args[1] == {
        "id_dominio": 99,
        "codigo_dominio": "ADS",
        "id_tipo_respaldo": 7,
        "enlace_respaldo": _ENLACE,
        "observaciones": "Ninguna",
        "fecha_aprobada": "2026/15/09",
    }
    mock_repo.actualizar_respaldo.assert_not_called()
    mock_repo.eliminar_respaldo.assert_not_called()


def test_respaldo_mismo_enlace_distinto_tipo_no_colisiona(monkeypatch):
    # El caso real del Excel: 2 filas con el MISMO enlace_respaldo,
    # diferenciadas solo por tipo_respaldo -- deben generar 2 inserts, no 1.
    staging_repo = MagicMock()
    staging_repo.leer.return_value = [
        _fila_respaldo(tipo_respaldo="Correo"),
        _fila_respaldo(tipo_respaldo="Minuta"),
    ]
    mock_repo = _parchear_repo_respaldos(monkeypatch)
    # cada tipo_respaldo resuelve a un id distinto en gdd.cat_tipo_respaldo
    mock_repo.resolver_catalogo_controlado.side_effect = [7, 8]
    engine = MagicMock()

    resultado = carga_gdd.ejecutar_merge_respaldos_dominio(engine, staging_repo, "ADS")

    assert resultado.ok
    assert resultado.insertadas == 2
    assert mock_repo.insertar_respaldo.call_count == 2


def test_respaldo_existente_con_cambios_se_actualiza_in_place(monkeypatch):
    staging_repo = MagicMock()
    staging_repo.leer.return_value = [_fila_respaldo(observaciones="Observacion nueva")]
    existentes = {(7, _ENLACE): ExistenteVersionado(id=42, fecha_aprobacion=None)}
    mock_repo = _parchear_repo_respaldos(monkeypatch, existentes=existentes)
    mock_repo.campos_actuales.return_value = {
        "observaciones": "Ninguna", "fecha_aprobada": "2026/15/09",
    }
    engine = MagicMock()

    resultado = carga_gdd.ejecutar_merge_respaldos_dominio(engine, staging_repo, "ADS")

    assert resultado.ok
    assert resultado.actualizadas == 1
    assert resultado.sin_cambios == 0
    assert resultado.insertadas == 0
    mock_repo.actualizar_respaldo.assert_called_once()
    assert mock_repo.actualizar_respaldo.call_args.args[1] == 42
    mock_repo.insertar_respaldo.assert_not_called()
    mock_repo.eliminar_respaldo.assert_not_called()


def test_respaldo_existente_sin_cambios_no_actualiza(monkeypatch):
    staging_repo = MagicMock()
    staging_repo.leer.return_value = [_fila_respaldo()]
    existentes = {(7, _ENLACE): ExistenteVersionado(id=42, fecha_aprobacion=None)}
    mock_repo = _parchear_repo_respaldos(monkeypatch, existentes=existentes)
    mock_repo.campos_actuales.return_value = {
        "observaciones": "Ninguna", "fecha_aprobada": "2026/15/09",
    }
    engine = MagicMock()

    resultado = carga_gdd.ejecutar_merge_respaldos_dominio(engine, staging_repo, "ADS")

    assert resultado.ok
    assert resultado.sin_cambios == 1
    assert resultado.actualizadas == 0
    mock_repo.actualizar_respaldo.assert_not_called()
    mock_repo.insertar_respaldo.assert_not_called()
    mock_repo.eliminar_respaldo.assert_not_called()


def test_respaldo_ausente_del_excel_se_da_de_baja(monkeypatch):
    staging_repo = MagicMock()
    staging_repo.leer.return_value = []
    existentes = {(7, _ENLACE): ExistenteVersionado(id=42, fecha_aprobacion=None)}
    mock_repo = _parchear_repo_respaldos(monkeypatch, existentes=existentes)
    engine = MagicMock()

    resultado = carga_gdd.ejecutar_merge_respaldos_dominio(engine, staging_repo, "ADS")

    assert resultado.ok
    assert resultado.eliminadas == 1
    mock_repo.eliminar_respaldo.assert_called_once()
    assert mock_repo.eliminar_respaldo.call_args.args[1] == 42
    mock_repo.insertar_respaldo.assert_not_called()
    mock_repo.actualizar_respaldo.assert_not_called()


def test_respaldo_dominio_no_encontrado_aborta(monkeypatch):
    staging_repo = MagicMock()
    staging_repo.leer.return_value = [_fila_respaldo()]

    mock_repo = MagicMock()
    mock_repo.resolver_dominio_id.side_effect = CatalogoNoEncontradoError("no existe")
    monkeypatch.setattr(carga_gdd, "repo", mock_repo)
    engine = MagicMock()

    resultado = carga_gdd.ejecutar_merge_respaldos_dominio(engine, staging_repo, "ADS")

    assert not resultado.ok
    assert "no existe" in resultado.error
    mock_repo.insertar_respaldo.assert_not_called()


def test_respaldo_tipo_desconocido_aborta(monkeypatch):
    staging_repo = MagicMock()
    staging_repo.leer.return_value = [_fila_respaldo(tipo_respaldo="Tipo Inventado")]

    mock_repo = _parchear_repo_respaldos(monkeypatch)
    mock_repo.resolver_catalogo_controlado.side_effect = CatalogoNoEncontradoError(
        "gdd.cat_tipo_respaldo: no existe ninguna fila con nombre_tipo_respaldo = 'Tipo Inventado'."
    )
    engine = MagicMock()

    resultado = carga_gdd.ejecutar_merge_respaldos_dominio(engine, staging_repo, "ADS")

    assert not resultado.ok
    assert "Tipo Inventado" in resultado.error
    mock_repo.insertar_respaldo.assert_not_called()


def test_respaldo_fila_sin_enlace_aborta_antes_de_tocar_bd(monkeypatch):
    staging_repo = MagicMock()
    staging_repo.leer.return_value = [_fila_respaldo(enlace_respaldo=None)]

    mock_repo = MagicMock()
    monkeypatch.setattr(carga_gdd, "repo", mock_repo)
    engine = MagicMock()

    resultado = carga_gdd.ejecutar_merge_respaldos_dominio(engine, staging_repo, "ADS")

    assert not resultado.ok
    mock_repo.resolver_dominio_id.assert_not_called()


def _fila_plan_remediacion(**overrides) -> dict:
    base = {
        "codigo_dominio_atributo": "ADS-4",
        "dimension": "Implementación",
        "categoria": "Registro de modelos analíticos",
        "fuente_oficial_sistema": "Bancs",
        "tipo_plan": "Tecnológico",
        "categoria_plan": "Ajusta Aplicaciones",
        "subcategoria_plan": "Ajusta Aplicaciones",
        "priorizacion": "1",
        "id_problema": "PR1",
        "estado_actual": "Alertado",
        "fecha_identificacion": datetime.date(2025, 9, 15),
        "fecha_finalizacion_definitiva": datetime.date(2026, 1, 30),
        "descripcion_causa_raíz": "Una causa cualquiera.",
        "persona_responsable_ejecución_plan_remediación": "Sebastian Tamayo, Santiago Hidalgo",
        "dependencias": "Cambio del servicio de actualización en OnBoard y SIMA",
        "observacion": None,
        "acciones_resolucion_corto_plazo": "Accion de corto plazo.",
        "acciones_resolucion_definitivo": "Accion definitiva.",
        "avance": "70",
        "fecha_ultima_modificacion": datetime.date(2026, 3, 15),
        "siro": "4566546546",
    }
    base.update(overrides)
    return base


_CAMPOS_MUTABLES_PLAN_REMEDIACION = {
    "id_dimension": 1,
    "categoria": "Registro de modelos analíticos",
    "fuente_oficial_sistema": "Bancs",
    "id_tipo_plan": 1,
    "id_categoria_plan": 1,
    "id_sub_categoria_plan": 1,
    "priorizacion": "1",
    "id_estado_plan": 1,
    "fecha_identificacion": datetime.date(2025, 9, 15),
    "fecha_finalizacion_definitiva": datetime.date(2026, 1, 30),
    "descripcion_causa_raiz": "Una causa cualquiera.",
    "persona_responsable": "Sebastian Tamayo, Santiago Hidalgo",
    "dependencias": "Cambio del servicio de actualización en OnBoard y SIMA",
    "observacion": None,
    "acciones_corto_plazo": "Accion de corto plazo.",
    "acciones_definitivo": "Accion definitiva.",
    "avance": 70,
    "fecha_ultima_modificacion": datetime.date(2026, 3, 15),
    "siro": "4566546546",
}


def _parchear_repo_plan_remediacion(monkeypatch, *, id_atributo=321, existentes=None):
    mock_repo = MagicMock()
    mock_repo.resolver_atributo_por_codigo.return_value = id_atributo
    mock_repo.resolver_catalogo_controlado.return_value = 1
    mock_repo.plan_remediacion_existentes.return_value = existentes or {}
    mock_repo.insertar_plan_remediacion.return_value = 555
    mock_repo.campos_actuales.return_value = {}
    monkeypatch.setattr(carga_gdd, "repo", mock_repo)
    return mock_repo


def test_plan_remediacion_nuevo_se_inserta(monkeypatch):
    staging_repo = MagicMock()
    staging_repo.leer_por_prefijo.return_value = [_fila_plan_remediacion()]
    mock_repo = _parchear_repo_plan_remediacion(monkeypatch)
    engine = MagicMock()

    resultado = carga_gdd.ejecutar_merge_plan_remediacion_dominio(engine, staging_repo, "ADS")

    assert resultado.ok
    assert resultado.insertadas == 1
    assert resultado.actualizadas == 0
    assert resultado.eliminadas == 0
    staging_repo.leer_por_prefijo.assert_called_once_with(
        "staging.plan_remediacion", "codigo_dominio_atributo", "ADS"
    )
    campos = mock_repo.insertar_plan_remediacion.call_args.args[1]
    assert campos == {
        "id_atributo": 321,
        "codigo_dominio": "ADS",
        "codigo_dominio_atributo": "ADS-4",
        "id_problema": "PR1",
        **_CAMPOS_MUTABLES_PLAN_REMEDIACION,
    }
    mock_repo.actualizar_plan_remediacion.assert_not_called()
    mock_repo.eliminar_plan_remediacion.assert_not_called()


def test_plan_remediacion_existente_con_cambios_se_actualiza_in_place(monkeypatch):
    staging_repo = MagicMock()
    staging_repo.leer_por_prefijo.return_value = [_fila_plan_remediacion(avance="80")]
    existentes = {("ADS-4", "PR1"): ExistenteVersionado(id=42, fecha_aprobacion=None)}
    mock_repo = _parchear_repo_plan_remediacion(monkeypatch, existentes=existentes)
    mock_repo.campos_actuales.return_value = dict(_CAMPOS_MUTABLES_PLAN_REMEDIACION)
    engine = MagicMock()

    resultado = carga_gdd.ejecutar_merge_plan_remediacion_dominio(engine, staging_repo, "ADS")

    assert resultado.ok
    assert resultado.actualizadas == 1
    assert resultado.sin_cambios == 0
    assert resultado.insertadas == 0
    mock_repo.actualizar_plan_remediacion.assert_called_once()
    assert mock_repo.actualizar_plan_remediacion.call_args.args[1] == 42
    campos_actualizados = mock_repo.actualizar_plan_remediacion.call_args.args[2]
    assert campos_actualizados["avance"] == 80
    # id_atributo/codigo_dominio/codigo_dominio_atributo/id_problema son la
    # clave -- no deben ir en el UPDATE.
    assert "id_atributo" not in campos_actualizados
    assert "codigo_dominio_atributo" not in campos_actualizados
    mock_repo.insertar_plan_remediacion.assert_not_called()
    mock_repo.eliminar_plan_remediacion.assert_not_called()


def test_plan_remediacion_existente_sin_cambios_no_actualiza(monkeypatch):
    staging_repo = MagicMock()
    staging_repo.leer_por_prefijo.return_value = [_fila_plan_remediacion()]
    existentes = {("ADS-4", "PR1"): ExistenteVersionado(id=42, fecha_aprobacion=None)}
    mock_repo = _parchear_repo_plan_remediacion(monkeypatch, existentes=existentes)
    mock_repo.campos_actuales.return_value = dict(_CAMPOS_MUTABLES_PLAN_REMEDIACION)
    engine = MagicMock()

    resultado = carga_gdd.ejecutar_merge_plan_remediacion_dominio(engine, staging_repo, "ADS")

    assert resultado.ok
    assert resultado.sin_cambios == 1
    assert resultado.actualizadas == 0
    mock_repo.actualizar_plan_remediacion.assert_not_called()
    mock_repo.insertar_plan_remediacion.assert_not_called()
    mock_repo.eliminar_plan_remediacion.assert_not_called()


def test_plan_remediacion_ausente_del_excel_se_da_de_baja(monkeypatch):
    staging_repo = MagicMock()
    staging_repo.leer_por_prefijo.return_value = []
    existentes = {("ADS-4", "PR1"): ExistenteVersionado(id=42, fecha_aprobacion=None)}
    mock_repo = _parchear_repo_plan_remediacion(monkeypatch, existentes=existentes)
    engine = MagicMock()

    resultado = carga_gdd.ejecutar_merge_plan_remediacion_dominio(engine, staging_repo, "ADS")

    assert resultado.ok
    assert resultado.eliminadas == 1
    mock_repo.eliminar_plan_remediacion.assert_called_once()
    assert mock_repo.eliminar_plan_remediacion.call_args.args[1] == 42
    mock_repo.insertar_plan_remediacion.assert_not_called()
    mock_repo.actualizar_plan_remediacion.assert_not_called()


def test_plan_remediacion_atributo_no_encontrado_aborta(monkeypatch):
    staging_repo = MagicMock()
    staging_repo.leer_por_prefijo.return_value = [_fila_plan_remediacion()]

    mock_repo = MagicMock()
    mock_repo.plan_remediacion_existentes.return_value = {}
    mock_repo.resolver_atributo_por_codigo.side_effect = CatalogoNoEncontradoError(
        "gdd.atributo: no existe una fila activa con codigo_dominio='ADS', codigo_atributo=4."
    )
    monkeypatch.setattr(carga_gdd, "repo", mock_repo)
    engine = MagicMock()

    resultado = carga_gdd.ejecutar_merge_plan_remediacion_dominio(engine, staging_repo, "ADS")

    assert not resultado.ok
    assert "codigo_atributo=4" in resultado.error
    mock_repo.insertar_plan_remediacion.assert_not_called()


def test_plan_remediacion_catalogo_desconocido_aborta(monkeypatch):
    staging_repo = MagicMock()
    staging_repo.leer_por_prefijo.return_value = [_fila_plan_remediacion(estado_actual="Inventado")]

    mock_repo = _parchear_repo_plan_remediacion(monkeypatch)
    mock_repo.resolver_catalogo_controlado.side_effect = CatalogoNoEncontradoError(
        "gdd.cat_estado_plan: no existe ninguna fila con nombre_estado_plan = 'Inventado'."
    )
    engine = MagicMock()

    resultado = carga_gdd.ejecutar_merge_plan_remediacion_dominio(engine, staging_repo, "ADS")

    assert not resultado.ok
    assert "Inventado" in resultado.error
    mock_repo.insertar_plan_remediacion.assert_not_called()


def test_plan_remediacion_fila_sin_id_problema_aborta_antes_de_tocar_bd(monkeypatch):
    staging_repo = MagicMock()
    staging_repo.leer_por_prefijo.return_value = [_fila_plan_remediacion(id_problema=None)]

    mock_repo = MagicMock()
    monkeypatch.setattr(carga_gdd, "repo", mock_repo)
    engine = MagicMock()

    resultado = carga_gdd.ejecutar_merge_plan_remediacion_dominio(engine, staging_repo, "ADS")

    assert not resultado.ok
    mock_repo.plan_remediacion_existentes.assert_not_called()
    mock_repo.resolver_atributo_por_codigo.assert_not_called()

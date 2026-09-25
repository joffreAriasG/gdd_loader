import datetime

import pytest

from gdd_loader.domain.gdd_mapping import (
    extraer_codigo_categoria_nivel_uno,
    extraer_codigo_sensibilidad,
    extraer_codigo_tipo_dato,
    mapear_atributo_fuente_consumo,
    mapear_detalle_atributos,
    mapear_estructura,
    mapear_investigacion,
    mapear_metadata_tecnica,
    mapear_plan_remediacion,
    mapear_respaldos,
)


def _fila_detalle(**overrides) -> dict:
    base = {
        "codigo_dominio": "ADS",
        "codigo_atributo": "1",
        "atributo": "Código del producto del seguro",
        "descripcion_atributo": "Una descripcion cualquiera.",
        "nivel_criticidad": "Crítico",
        "reporte": "X",
        "proyecto": "-",
        "proceso": "X",
        "estructura": "-",
        "es_dato_personal": "No",
        "categoria_nivel_1": "-",
        "tipo_dato": "-",
        "sensibilidad": "-",
        "tipo_atributo": "Maestro",
        "compartido_similar": "-",
        "atributo_primario": "-",
        "dominio_primario": "-",
        "fecha_aprobada": datetime.date(2025, 1, 31),
    }
    base.update(overrides)
    return base


def test_mapea_bits_x_guion_correctamente():
    [resultado] = mapear_detalle_atributos([_fila_detalle(reporte="X", proyecto="-")])
    assert resultado.reporte is True
    assert resultado.proyecto is False


def test_mapea_guion_como_none_en_columnas_de_texto():
    [resultado] = mapear_detalle_atributos([_fila_detalle(compartido_similar="-")])
    assert resultado.compartido_similar_texto is None


def test_ignora_categoria_tipo_dato_sensibilidad_si_no_es_dato_personal():
    fila = _fila_detalle(
        es_dato_personal="No",
        categoria_nivel_1="6. Crediticios",  # dato "sucio": no deberia venir, pero si viniera
    )
    [resultado] = mapear_detalle_atributos([fila])
    assert resultado.es_dato_personal is False
    assert resultado.categoria_nivel_1_texto is None


def test_captura_categoria_tipo_dato_sensibilidad_si_es_dato_personal():
    fila = _fila_detalle(
        es_dato_personal="Sí",
        categoria_nivel_1="6. Crediticios",
        tipo_dato="6.1. Datos Identificativos para Solicitudes y Formularios de Relación Comercial",
        sensibilidad="2. Medio",
    )
    [resultado] = mapear_detalle_atributos([fila])
    assert resultado.es_dato_personal is True
    assert resultado.categoria_nivel_1_texto == "6. Crediticios"
    assert resultado.sensibilidad_texto == "2. Medio"


def test_codigo_dominio_atributo_y_clave_natural():
    [resultado] = mapear_detalle_atributos([_fila_detalle(codigo_dominio="ADS", codigo_atributo="7")])
    assert resultado.codigo_dominio_atributo == "ADS-7"
    assert resultado.clave_natural == ("ADS", 7)


def test_falla_con_valor_inesperado_en_columna_x_guion():
    with pytest.raises(ValueError, match="reporte|inesperado"):
        mapear_detalle_atributos([_fila_detalle(reporte="SI")])


def test_falla_con_valor_inesperado_en_columna_si_no():
    with pytest.raises(ValueError):
        mapear_detalle_atributos([_fila_detalle(es_dato_personal="X")])


def test_extrae_codigo_categoria_nivel_uno():
    assert extraer_codigo_categoria_nivel_uno("6. Crediticios") == "6"


def test_extrae_codigo_tipo_dato():
    assert (
        extraer_codigo_tipo_dato(
            "6.1. Datos Identificativos para Solicitudes y Formularios de Relación Comercial"
        )
        == "6.1"
    )


def test_extrae_codigo_sensibilidad():
    assert extraer_codigo_sensibilidad("2. Medio") == "2"
    assert extraer_codigo_sensibilidad("3. Alto") == "3"


def test_extraer_codigo_retorna_none_si_el_texto_es_none():
    assert extraer_codigo_categoria_nivel_uno(None) is None
    assert extraer_codigo_tipo_dato(None) is None
    assert extraer_codigo_sensibilidad(None) is None


def test_extraer_codigo_falla_con_texto_sin_formato_esperado():
    with pytest.raises(ValueError, match="categoria_nivel_1"):
        extraer_codigo_categoria_nivel_uno("Crediticios")
    with pytest.raises(ValueError, match="tipo_dato"):
        extraer_codigo_tipo_dato("Datos Identificativos")
    with pytest.raises(ValueError, match="sensibilidad"):
        extraer_codigo_sensibilidad("Medio")


def _fila_metadata(**overrides) -> dict:
    base = {
        "codigo_dominio_atributo": "ADS-1",
        "clase": "Ventas",
        "formula_calculo": "-",
        "servidor_fuente_oficial": "Servidor de archivos",
        "base_datos_fuente_oficial": "https://sftpi.example/Inputs/",
        "tabla_fuente_oficial": "ReporteVentasDiario_dd.mm.yyyy.txt",
        "nombre_campo_fuente_oficial": "IDMSVPRODUCTO",
        "longitud_campo_fuente_oficial": "50.0",
        "lista_valores_validos": "-",
        "acepta_valores_nulos": "No",
        "tipo_campo": "VARCHAR",
        "coleccion_foc": "-",
        "servidor_foc": "-",
        "tabla_bv_foc": "-",
        "nombre_campo_foc": "-",
        "fecha_aprobada": "31-01-2025",
    }
    base.update(overrides)
    return base


def test_metadata_sin_datos_foc_genera_una_sola_fuente_primaria():
    resultado = mapear_metadata_tecnica([_fila_metadata()])
    assert len(resultado) == 1
    assert resultado[0].es_fuente_primaria is True
    assert resultado[0].fecha_aprobacion == datetime.date(2025, 1, 31)


def test_metadata_con_datos_foc_genera_fuente_primaria_y_secundaria():
    fila = _fila_metadata(servidor_foc="Servidor B", nombre_campo_foc="CAMPOB")
    resultado = mapear_metadata_tecnica([fila])
    assert len(resultado) == 2
    assert resultado[0].es_fuente_primaria is True
    assert resultado[1].es_fuente_primaria is False
    assert resultado[1].servidor_texto == "Servidor B"
    assert resultado[1].nombre_campo == "CAMPOB"


def test_metadata_sin_fecha_aprobada_queda_en_none():
    resultado = mapear_metadata_tecnica([_fila_metadata(fecha_aprobada="-")])
    assert resultado[0].fecha_aprobacion is None


def test_metadata_falla_con_fecha_no_parseable():
    with pytest.raises(ValueError, match="fecha_aprobada"):
        mapear_metadata_tecnica([_fila_metadata(fecha_aprobada="2025-01-31")])


def test_metadata_fecha_aprobada_acepta_formato_nuevo_de_excel():
    """Desde 2026-09-16 la plantilla trae fecha_aprobada como celda de fecha
    real de Excel; con dtype=str eso llega como texto ISO con hora
    ("YYYY-MM-DD HH:MM:SS"). El parser debe aceptar este formato ademas del
    legacy "DD-MM-YYYY" (filas ya cargadas en staging antes del cambio)."""
    resultado = mapear_metadata_tecnica(
        [_fila_metadata(fecha_aprobada="2026-01-31 00:00:00")]
    )
    assert resultado[0].fecha_aprobacion == datetime.date(2026, 1, 31)


def test_metadata_fecha_aprobada_sigue_aceptando_formato_legacy():
    """Filas viejas de staging.metadata_tecnica (cargadas antes del cambio
    de plantilla) siguen en formato DD-MM-YYYY -- deben seguir parseando."""
    resultado = mapear_metadata_tecnica(
        [_fila_metadata(fecha_aprobada="31-01-2025")]
    )
    assert resultado[0].fecha_aprobacion == datetime.date(2025, 1, 31)


# --- mapear_atributo_fuente_consumo ---
#
# Alcance ampliado 2026-09-17, REFACTORIZADO 2026-09-18: columnas _foc de
# metadata_tecnica, ahora resueltas contra catalogos controlados (ver
# pipeline/carga_gdd.py y load/gdd_repository.py) en vez de guardarse como
# texto libre. Confirmado con el usuario: la cardinalidad es directa con el
# ATRIBUTO (se agrupa/deduplica), no una fila por cada fila de staging como
# fuente_oficial. coleccion_foc paso de OPCIONAL a OBLIGATORIA -- resuelve la
# coleccion/base de datos destino, ya no es solo descriptiva.


def test_consumo_sin_datos_foc_no_genera_filas():
    resultado = mapear_atributo_fuente_consumo([_fila_metadata()])
    assert resultado == []


def test_consumo_requiere_las_4_columnas_foc_juntas():
    """Sin servidor_foc, coleccion_foc, tabla_bv_foc Y nombre_campo_foc juntos no
    hay destino de consumo identificable -- se omite en vez de insertar una
    fila a medias. coleccion_foc (REFACTOR 2026-09-18) ahora es tan obligatoria
    como las otras 3, porque resuelve la coleccion/base de datos destino."""
    assert mapear_atributo_fuente_consumo([_fila_metadata(coleccion_foc="Vista")]) == []
    fila_sin_clase = _fila_metadata(
        servidor_foc="BI01", tabla_bv_foc="VW_VENTAS", nombre_campo_foc="ID_PRODUCTO"
    )
    assert mapear_atributo_fuente_consumo([fila_sin_clase]) == []


def test_consumo_con_las_4_columnas_foc_genera_una_fila():
    fila = _fila_metadata(
        coleccion_foc="Vista", servidor_foc="BI01", tabla_bv_foc="VW_VENTAS", nombre_campo_foc="ID_PRODUCTO"
    )
    [resultado] = mapear_atributo_fuente_consumo([fila])
    assert resultado.codigo_dominio_atributo == "ADS-1"
    assert resultado.servidor_texto == "BI01"
    assert resultado.coleccion_texto == "Vista"
    assert resultado.nombre_tabla == "VW_VENTAS"
    assert resultado.nombre_campo == "ID_PRODUCTO"


def test_consumo_agrupa_varias_filas_del_mismo_atributo():
    """La plantilla puede repetir codigo_dominio_atributo en varias filas
    cuando un atributo tiene mas de un destino de consumo -- confirmado con
    el usuario. Cada destino distinto produce su propia fila."""
    filas = [
        _fila_metadata(
            codigo_dominio_atributo="ADS-1", coleccion_foc="Vista",
            servidor_foc="BI01", tabla_bv_foc="VW_VENTAS", nombre_campo_foc="ID_PRODUCTO",
        ),
        _fila_metadata(
            codigo_dominio_atributo="ADS-1", coleccion_foc="Vista",
            servidor_foc="BI02", tabla_bv_foc="VW_REPORTES", nombre_campo_foc="COD_PRODUCTO",
        ),
    ]
    resultado = mapear_atributo_fuente_consumo(filas)
    assert len(resultado) == 2
    assert {r.servidor_texto for r in resultado} == {"BI01", "BI02"}


def test_consumo_deduplica_mismo_destino_repetido():
    """Mismo (atributo, servidor, clase, tabla, campo) repetido -- se
    colapsa en una sola fila, no se duplica."""
    filas = [
        _fila_metadata(coleccion_foc="Vista", servidor_foc="BI01", tabla_bv_foc="VW_VENTAS", nombre_campo_foc="ID_PRODUCTO"),
        _fila_metadata(coleccion_foc="Vista", servidor_foc="BI01", tabla_bv_foc="VW_VENTAS", nombre_campo_foc="ID_PRODUCTO"),
    ]
    resultado = mapear_atributo_fuente_consumo(filas)
    assert len(resultado) == 1


def test_consumo_distinta_clase_no_colapsa():
    """REFACTOR 2026-09-18: coleccion_foc ahora es parte de la clave de
    agrupacion (resuelve la coleccion/bdd destino) -- el mismo servidor/
    tabla/campo con coleccion_foc distinta son destinos DISTINTOS, a diferencia
    del diseno anterior donde clase era solo descriptiva y no colapsaba."""
    filas = [
        _fila_metadata(coleccion_foc="Vista", servidor_foc="BI01", tabla_bv_foc="VW_VENTAS", nombre_campo_foc="ID_PRODUCTO"),
        _fila_metadata(coleccion_foc="Tabla", servidor_foc="BI01", tabla_bv_foc="VW_VENTAS", nombre_campo_foc="ID_PRODUCTO"),
    ]
    resultado = mapear_atributo_fuente_consumo(filas)
    assert len(resultado) == 2
    assert {r.coleccion_texto for r in resultado} == {"Vista", "Tabla"}


def test_consumo_no_colapsa_atributos_distintos_con_mismo_destino():
    """El mismo servidor/clase/tabla/campo para atributos DISTINTOS son
    destinos de consumo distintos (la clave incluye el atributo)."""
    filas = [
        _fila_metadata(
            codigo_dominio_atributo="ADS-1", coleccion_foc="Vista",
            servidor_foc="BI01", tabla_bv_foc="VW_VENTAS", nombre_campo_foc="ID_PRODUCTO",
        ),
        _fila_metadata(
            codigo_dominio_atributo="ADS-2", coleccion_foc="Vista",
            servidor_foc="BI01", tabla_bv_foc="VW_VENTAS", nombre_campo_foc="ID_PRODUCTO",
        ),
    ]
    resultado = mapear_atributo_fuente_consumo(filas)
    assert len(resultado) == 2
    assert {r.codigo_dominio_atributo for r in resultado} == {"ADS-1", "ADS-2"}


def _fila_investigacion(**overrides) -> dict:
    base = {
        "codigo_dominio": "ADS",
        "clasificacion": "Normativas e instructivos",
        "nombre": "Código de comercio",
        "descripcion": "Rige las obligaciones de los comerciantes.",
        "referencia_normativa": "Resolución de la Superintendencia de Compañías 6, Registro Oficial 483.",
    }
    base.update(overrides)
    return base


def test_investigacion_mapea_todas_las_columnas():
    resultado = mapear_investigacion([_fila_investigacion()])
    assert len(resultado) == 1
    fila = resultado[0]
    assert fila.codigo_dominio == "ADS"
    assert fila.clasificacion_texto == "Normativas e instructivos"
    assert fila.nombre == "Código de comercio"
    assert fila.descripcion == "Rige las obligaciones de los comerciantes."
    assert fila.referencia_normativa == "Resolución de la Superintendencia de Compañías 6, Registro Oficial 483."


def test_investigacion_descripcion_vacia_queda_en_none():
    resultado = mapear_investigacion([_fila_investigacion(descripcion=None)])
    assert resultado[0].descripcion is None


def test_investigacion_referencia_normativa_vacia_queda_en_none():
    resultado = mapear_investigacion([_fila_investigacion(referencia_normativa=None)])
    assert resultado[0].referencia_normativa is None


def test_investigacion_falla_sin_clasificacion():
    with pytest.raises(ValueError, match="clasificacion"):
        mapear_investigacion([_fila_investigacion(clasificacion=None)])


def test_investigacion_falla_sin_nombre():
    with pytest.raises(ValueError, match="nombre"):
        mapear_investigacion([_fila_investigacion(nombre=None)])


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


def test_estructura_mapea_todas_las_columnas():
    resultado = mapear_estructura([_fila_estructura()])
    assert len(resultado) == 1
    fila = resultado[0]
    assert fila.codigo_dominio == "ADS"
    assert fila.rol_texto == "Dueño de Dominio"
    assert fila.area == "Tribu Producto Banca Relacional"
    assert fila.codigo_plaza == "1000921"
    assert fila.nombre_plaza == "Dueño de Producto Sr"
    assert fila.nombre_responsable == "Xavier Santiago Cabrera Rivadeneira"
    assert fila.fecha_aprobada == datetime.date(2024, 5, 10)


def test_estructura_nombre_plaza_vacio_queda_en_none():
    resultado = mapear_estructura([_fila_estructura(nombre_plaza=None)])
    assert resultado[0].nombre_plaza is None


def test_estructura_falla_sin_rol():
    with pytest.raises(ValueError, match="rol"):
        mapear_estructura([_fila_estructura(rol=None)])


def test_estructura_falla_sin_codigo_plaza():
    with pytest.raises(ValueError, match="codigo_plaza"):
        mapear_estructura([_fila_estructura(codigo_plaza=None)])


def _fila_respaldo(**overrides) -> dict:
    base = {
        "codigo_dominio": "ADS",
        "tipo_respaldo": "Correo",
        "enlace_respaldo": "https://ejemplo.atlassian.net/wiki/spaces/DEMO/pages/1000",
        "observaciones": "Ninguna",
        "fecha_aprobada": "2026/15/09",
    }
    base.update(overrides)
    return base


def test_respaldos_mapea_todas_las_columnas():
    resultado = mapear_respaldos([_fila_respaldo()])
    assert len(resultado) == 1
    fila = resultado[0]
    assert fila.codigo_dominio == "ADS"
    assert fila.tipo_respaldo_texto == "Correo"
    assert fila.enlace_respaldo == "https://ejemplo.atlassian.net/wiki/spaces/DEMO/pages/1000"
    assert fila.observaciones == "Ninguna"
    assert fila.fecha_aprobada == "2026/15/09"


def test_respaldos_fecha_aprobada_no_se_parsea_como_fecha():
    # A diferencia de Estructura/DetalleAtributos -- queda como texto tal cual.
    resultado = mapear_respaldos([_fila_respaldo(fecha_aprobada="2026/15/09")])
    assert resultado[0].fecha_aprobada == "2026/15/09"


def test_respaldos_observaciones_vacia_queda_en_none():
    resultado = mapear_respaldos([_fila_respaldo(observaciones="-")])
    assert resultado[0].observaciones is None


def test_respaldos_falla_sin_tipo_respaldo():
    with pytest.raises(ValueError, match="tipo_respaldo"):
        mapear_respaldos([_fila_respaldo(tipo_respaldo=None)])


def test_respaldos_falla_sin_enlace_respaldo():
    with pytest.raises(ValueError, match="enlace_respaldo"):
        mapear_respaldos([_fila_respaldo(enlace_respaldo=None)])


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


def test_plan_remediacion_mapea_todas_las_columnas():
    resultado = mapear_plan_remediacion([_fila_plan_remediacion()])
    assert len(resultado) == 1
    fila = resultado[0]
    assert fila.codigo_dominio == "ADS"
    assert fila.codigo_atributo == 4
    assert fila.codigo_dominio_atributo == "ADS-4"
    assert fila.id_problema == "PR1"
    assert fila.dimension_texto == "Implementación"
    assert fila.categoria == "Registro de modelos analíticos"
    assert fila.fuente_oficial_sistema == "Bancs"
    assert fila.tipo_plan_texto == "Tecnológico"
    assert fila.categoria_plan_texto == "Ajusta Aplicaciones"
    assert fila.subcategoria_plan_texto == "Ajusta Aplicaciones"
    assert fila.priorizacion == 1
    assert fila.estado_actual_texto == "Alertado"
    assert fila.fecha_identificacion == datetime.date(2025, 9, 15)
    assert fila.fecha_finalizacion_definitiva == datetime.date(2026, 1, 30)
    assert fila.descripcion_causa_raiz == "Una causa cualquiera."
    assert fila.persona_responsable == "Sebastian Tamayo, Santiago Hidalgo"
    assert fila.dependencias == "Cambio del servicio de actualización en OnBoard y SIMA"
    assert fila.observacion is None
    assert fila.acciones_corto_plazo == "Accion de corto plazo."
    assert fila.acciones_definitivo == "Accion definitiva."
    assert fila.avance == 70
    assert fila.fecha_ultima_modificacion == datetime.date(2026, 3, 15)
    assert fila.siro == "4566546546"


def test_plan_remediacion_divide_codigo_dominio_atributo_correctamente():
    resultado = mapear_plan_remediacion([_fila_plan_remediacion(codigo_dominio_atributo="ADS-12")])
    assert resultado[0].codigo_dominio == "ADS"
    assert resultado[0].codigo_atributo == 12


def test_plan_remediacion_categoria_y_fuente_oficial_sistema_son_texto_libre():
    # No pasan por ningun catalogo -- se guardan tal cual (a diferencia de
    # dimension/tipo_plan/categoria_plan/subcategoria_plan/estado_actual).
    resultado = mapear_plan_remediacion(
        [_fila_plan_remediacion(categoria="Cualquier cosa", fuente_oficial_sistema="Otro sistema")]
    )
    assert resultado[0].categoria == "Cualquier cosa"
    assert resultado[0].fuente_oficial_sistema == "Otro sistema"


def test_plan_remediacion_observacion_vacia_queda_en_none():
    resultado = mapear_plan_remediacion([_fila_plan_remediacion(observacion="-")])
    assert resultado[0].observacion is None


def test_plan_remediacion_priorizacion_y_avance_vacios_quedan_en_none():
    resultado = mapear_plan_remediacion([_fila_plan_remediacion(priorizacion=None, avance="-")])
    assert resultado[0].priorizacion is None
    assert resultado[0].avance is None


def test_plan_remediacion_falla_sin_codigo_dominio_atributo():
    with pytest.raises(ValueError, match="codigo_dominio_atributo"):
        mapear_plan_remediacion([_fila_plan_remediacion(codigo_dominio_atributo=None)])


def test_plan_remediacion_falla_sin_id_problema():
    with pytest.raises(ValueError, match="id_problema"):
        mapear_plan_remediacion([_fila_plan_remediacion(id_problema=None)])


def test_plan_remediacion_falla_con_codigo_dominio_atributo_sin_guion():
    with pytest.raises(ValueError, match="codigo_dominio_atributo"):
        mapear_plan_remediacion([_fila_plan_remediacion(codigo_dominio_atributo="ADS4")])


def test_plan_remediacion_falla_con_codigo_atributo_no_numerico():
    with pytest.raises(ValueError, match="codigo_dominio_atributo"):
        mapear_plan_remediacion([_fila_plan_remediacion(codigo_dominio_atributo="ADS-X")])


def test_plan_remediacion_celda_nan_real_de_excel_queda_en_none():
    # Bug real detectado probando contra el Excel real: una celda
    # GENUINAMENTE vacia (a diferencia de "-") llega como float('nan'), no
    # como None -- sin el fix en _texto_o_none quedaba guardado como el
    # texto literal 'nan'.
    resultado = mapear_plan_remediacion([_fila_plan_remediacion(observacion=float("nan"))])
    assert resultado[0].observacion is None

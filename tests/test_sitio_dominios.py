"""Tests del sitio de consulta por dominio (datos sinteticos, sin BD)."""

from __future__ import annotations

import datetime
import json
import re
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from gdd_loader.exportar.sitio_dominios import (PAGINAS, comprimir_datos, construir_modelo, descomprimir_datos,
                                                generar_html, generar_sitio, modelo_para_seccion)
from gdd_loader.load import sitio_repository
from gdd_loader.sitio_cli import escribir_sitio, paginas_excedidas, resolver_ruta_salida


def _dom(cod, nombre=None):
    return {"codigo_dominio": cod, "nombre_dominio": nombre or f"Dominio {cod}", "tipo_dominio": "Maestro",
            "frontera": None}


def _attr(cod_dom, cod_attr, nombre=None, personal=False):
    return {"codigo_dominio": cod_dom, "codigo_atributo": cod_attr, "nombre_atributo": nombre or f"Atributo {cod_attr}",
            "descripcion_atributo": "Desc", "criticidad": "Alta", "tipo_atributo": "Simple",
            "compartido_similar": None, "es_dato_personal": personal, "clasificacion_dato_personal": None,
            "reporte": True, "proyecto": False, "proceso": False, "estructura": False,
            "atributo_primario": None, "dominio_primario": None,
            "fecha_aprobacion": datetime.date(2026, 9, 1), "version": "1"}


def _fo(cod_dom, cod_attr, campo, primaria=True):
    return {"codigo_dominio": cod_dom, "codigo_atributo": cod_attr, "servidor": "SRV", "base_datos": "BD",
            "tabla": "T1", "nombre_campo": campo, "es_fuente_primaria": primaria, "longitud_campo": Decimal("10.00"),
            "tipo_campo": "varchar", "acepta_valores_nulos": False, "formula_calculo": None,
            "lista_valores_validos": None, "fecha_aprobacion": datetime.date(2026, 9, 1), "version": "1"}


def _fc(cod_dom, cod_attr, campo, clase="Bronce"):
    return {"codigo_dominio": cod_dom, "codigo_atributo": cod_attr, "servidor": "S3", "base_datos": "coleccion",
            "tabla": "tabla_bv", "nombre_campo": campo, "clase": clase, "fecha_aprobacion": datetime.date(2026, 10, 5)}


def _modelo_del_html(html: str) -> dict:
    """Datos embebidos (gzip + base64) de una pagina generada."""
    bloque = re.search(r'<script id="datos" type="text/plain" data-formato="gzip-base64">(.*?)</script>', html, re.S)
    return descomprimir_datos(bloque.group(1))


def _config_del_html(html: str):
    return json.loads(re.search(r'<script id="config" type="application/json">(.*?)</script>', html, re.S).group(1))


def _datos(**extra):
    base = {
        "dominios": [_dom("CLI"), _dom("ADS")],
        "responsables": [{"codigo_dominio": "ADS", "rol": "Data Owner", "area": "Seguros", "codigo_plaza": "P1",
                          "nombre_plaza": "Jefe", "nombre_responsable": "Persona A", "fecha_aprobada": None}],
        "respaldos": [{"codigo_dominio": "ADS", "tipo_respaldo": "Acta", "enlace_respaldo": "https://x/acta",
                       "observaciones": None, "fecha_aprobada": "01/09/2026"}],
        "atributos": [_attr("ADS", 10), _attr("ADS", 2, personal=True), _attr("CLI", 1)],
        "fuentes_oficiales": [_fo("ADS", 2, "CAMPO_B", primaria=False), _fo("ADS", 2, "CAMPO_A")],
        "fuentes_consumo": [_fc("ADS", 2, "campo_x")],
        "investigacion": [{"codigo_dominio": "ADS", "clasificacion": "Termino", "nombre": "Poliza",
                           "descripcion": "Contrato", "referencia_normativa": None}],
    }
    base.update(extra)
    return base


def test_agrupa_por_dominio_y_ordena():
    m = construir_modelo(_datos())
    assert [d["codigo"] for d in m["dominios"]] == ["ADS", "CLI"]
    ads = m["dominios"][0]
    # orden natural de codigo_atributo: 2 antes de 10
    assert [a["codigo_atributo"] for a in ads["atributos"]] == ["2", "10"]
    # el unico registro de investigacion de ADS es un "Termino": cuenta como termino, no como investigacion
    assert ads["conteos"] == {"estructura": 1, "respaldos": 1, "atributos": 2, "fuentes_oficiales": 2,
                              "fuentes_consumo": 1, "investigacion": 0, "terminos": 1}
    assert m["resumen"]["n_dominios_con_atributos"] == 2
    assert m["resumen"]["totales"]["atributos"] == 3
    assert m["resumen"]["huerfanos"] == {}


def test_fuentes_cuelgan_de_su_atributo_y_primaria_primero():
    a2 = construir_modelo(_datos())["dominios"][0]["atributos"][0]
    assert [f["nombre_campo"] for f in a2["fuentes_oficiales"]] == ["CAMPO_A", "CAMPO_B"]
    assert a2["fuentes_oficiales"][0]["es_fuente_primaria"] == "Sí"
    assert a2["fuentes_oficiales"][0]["longitud_campo"] == "10"
    assert [f["nombre_campo"] for f in a2["fuentes_consumo"]] == ["campo_x"]
    assert "codigo_dominio" not in a2["fuentes_consumo"][0]


def test_valores_a_texto():
    ads = construir_modelo(_datos())["dominios"][0]
    a2 = ads["atributos"][0]
    assert a2["es_dato_personal"] == "Sí"
    assert a2["fecha_aprobacion"] == "2026-09-01"
    assert ads["info"] == {"tipo_dominio": "Maestro", "frontera": ""}


def test_registros_sin_dominio_o_atributo_se_cuentan_como_huerfanos():
    m = construir_modelo(_datos(
        investigacion=[{"codigo_dominio": "ZZZ", "clasificacion": "Termino", "nombre": "x",
                        "descripcion": None, "referencia_normativa": None}],
        fuentes_consumo=[_fc("ADS", 99, "campo_y")],
    ))
    assert m["resumen"]["huerfanos"] == {"investigacion": 1, "fuentes_consumo": 1}
    assert m["dominios"][0]["conteos"]["investigacion"] == 0


def test_dominio_sin_datos_aparece_vacio():
    m = construir_modelo({"dominios": [_dom("RAE")]})
    rae = m["dominios"][0]
    assert rae["atributos"] == [] and rae["estructura"] == [] and rae["investigacion"] == []
    assert m["resumen"]["n_dominios_con_atributos"] == 0


def test_html_autocontenido_y_escapa_script():
    datos = _datos()
    datos["investigacion"][0]["descripcion"] = "</script><script>alert(1)</script>"
    html = generar_html(construir_modelo(datos), "2026-11-09 21:00", "Glosario <PRUEBAS>")
    assert "</script><script>alert(1)" not in html
    assert "Glosario &lt;PRUEBAS&gt;" in html
    assert not re.search(r'(src|href)="https?://', html)
    # los datos embebidos (comprimidos) se leen de vuelta completos
    modelo = _modelo_del_html(html)
    assert modelo["dominios"][0]["codigo"] == "ADS"
    assert modelo["dominios"][0]["investigacion"][0]["descripcion"] == "</script><script>alert(1)</script>"
    assert _config_del_html(html) is None      # un solo archivo


def test_html_tiene_las_tres_secciones():
    html = generar_html(construir_modelo(_datos()), "x")
    for titulo in ("Dominio y estructura", "Atributos y fuentes", "Investigación"):
        assert titulo in html


def test_ficha_del_dominio_solo_tipo_y_frontera_y_responsables_con_icono():
    html = generar_html(construir_modelo(_datos()), "x")
    assert '["tipo_dominio","Tipo de dominio"], ["frontera","Frontera"]' in html
    for quitado in ("Estado de gobierno", "Versión de calidad", "Release de gobierno"):
        assert quitado not in html
    assert "function tarjetaPersona" in html and "ico-avatar" in html


def test_ficha_del_atributo_en_bloques_y_sin_release():
    html = generar_html(construir_modelo(_datos()), "x")
    assert '"release_atributo"' not in html and '"Release"' not in html
    for bloque in ('texto:"Descripción"', 'texto:"Compartido / similar"', 'texto:"Clasificación y aprobación"'):
        assert bloque in html


def test_formula_larga_va_completa_en_los_datos_y_con_fila_desplegable():
    datos = _datos()
    formula = "Para obtener el campo se debe: " + "combinar tablas; " * 500  # ~8.500 caracteres
    datos["fuentes_oficiales"][1]["formula_calculo"] = formula
    modelo = construir_modelo(datos)
    fuente = modelo["dominios"][0]["atributos"][0]["fuentes_oficiales"][0]
    assert fuente["formula_calculo"] == formula.strip()  # sin recortes
    html = generar_html(modelo, "x")
    assert "function tablaFuentesOficiales" in html and "Ver fórmula" in html
    assert "Abrir todas las fórmulas" in html


def _grafo_sintetico():
    from gdd_loader.exportar.grafo_relaciones import construir_modelo as construir_grafo
    dominios = [{"id_dominio": 1, "codigo_dominio": "ADS", "nombre_dominio": "Dominio ADS", "tipo_dominio": None,
                 "n_atributos_activos": 2},
                {"id_dominio": 2, "codigo_dominio": "CLI", "nombre_dominio": "Dominio CLI", "tipo_dominio": None,
                 "n_atributos_activos": 1}]
    relaciones = [{"id_atributo_origen": 7, "codigo_atributo_origen": 2, "nombre_atributo_origen": "Atributo 2",
                   "id_dominio_origen": 1, "codigo_dominio_origen": "ADS", "nombre_dominio_origen": "Dominio ADS",
                   "tipo_relacion": "Compartido", "id_atributo_destino": 9, "nombre_atributo_destino": "Cliente",
                   "atributo_destino_activo": True, "id_dominio_destino": 2, "codigo_dominio_destino": "CLI",
                   "nombre_dominio_destino": "Dominio CLI", "estado_vinculo": "RESUELTO"}]
    return construir_grafo(dominios, relaciones)


def _inicio_vis() -> str:
    from gdd_loader.exportar.grafo_relaciones import ARCHIVO_VIS, RECURSOS
    return (RECURSOS / ARCHIVO_VIS).read_text(encoding="utf-8")[:200]


def test_relaciones_cuarta_seccion_e_incrusta_vis_solo_si_hay_grafo():
    con = generar_html(construir_modelo(_datos(), _grafo_sintetico()), "x")
    assert "Relaciones entre dominios" in con and "function vistaRelaciones" in con
    assert _inicio_vis() in con
    rel = _modelo_del_html(con)["relaciones"]
    assert rel["resumen"]["n_aristas"] == 1 and rel["aristas"][0]["atributos"][0]["codigo_origen"] == "ADS-2"

    sin = generar_html(construir_modelo(_datos()), "x")
    assert _inicio_vis() not in sin            # sin vista: no se incrustan ~690 KB de vis-network
    assert _modelo_del_html(sin)["relaciones"] is None
    assert "falta la vista" in sin             # la seccion 4 lo informa


def test_leer_relaciones_o_none(monkeypatch):
    from gdd_loader import sitio_cli
    from gdd_loader.load.grafo_repository import VistaNoExisteError

    logger = MagicMock()
    monkeypatch.setattr(sitio_cli, "leer_dominios", lambda conn: [])

    def sin_vista(conn):
        raise VistaNoExisteError("No existe gdd.v_relacion_atributo.")
    monkeypatch.setattr(sitio_cli, "leer_relaciones", sin_vista)
    assert sitio_cli.leer_relaciones_o_none(MagicMock(), logger) is None
    assert logger.warning.called

    monkeypatch.setattr(sitio_cli, "leer_relaciones", lambda conn: [])
    modelo = sitio_cli.leer_relaciones_o_none(MagicMock(), logger)
    assert modelo["resumen"]["n_aristas"] == 0


def test_es_termino_sin_importar_tildes_ni_mayusculas():
    from gdd_loader.exportar.sitio_dominios import es_termino
    for valor in ("Término", "Termino", " TÉRMINO ", "término"):
        assert es_termino(valor)
    for valor in (None, "", "Términos relacionados", "Normativas e instructivos"):
        assert not es_termino(valor)


def test_terminos_separados_de_investigacion():
    datos = _datos(investigacion=[
        {"codigo_dominio": "ADS", "clasificacion": "Término", "nombre": "Póliza", "descripcion": "Contrato",
         "referencia_normativa": "Definición Banco"},
        {"codigo_dominio": "ADS", "clasificacion": "Normativas e instructivos", "nombre": "Norma X",
         "descripcion": None, "referencia_normativa": None},
        {"codigo_dominio": "CLI", "clasificacion": "Termino", "nombre": "Cliente", "descripcion": "Persona",
         "referencia_normativa": None},
    ])
    m = construir_modelo(datos)
    ads, cli = m["dominios"]
    assert (ads["conteos"]["investigacion"], ads["conteos"]["terminos"]) == (1, 1)
    assert (cli["conteos"]["investigacion"], cli["conteos"]["terminos"]) == (0, 1)
    assert m["resumen"]["totales"]["terminos"] == 2
    html = generar_html(m, "x")
    assert "Glosario de términos" in html and "function vistaTerminos" in html


def test_descarga_excel_de_atributos_incluida():
    # El .xlsx se arma en el navegador (JS); aqui se verifica que la pagina trae el generador y las
    # hojas esperadas. La validacion del archivo real se hizo abriendolo con openpyxl (ver docs).
    html = generar_html(construir_modelo(_datos(), _grafo_sintetico()), "x")
    assert "function descargarAtributos" in html and "function zipStore" in html and "Descargar Excel" in html
    for hoja in ('nombre:"Atributos"', 'nombre:"Fuentes oficiales"', 'nombre:"Fuentes de consumo"',
                 'nombre:"Relaciones"', 'nombre:"Filtro"'):
        assert hoja in html
    assert not re.search(r'(src|href)="https?://', html)  # sigue sin recursos externos


# --- indicadores de fuente oficial (v0.11.0) -------------------------------------------------------


def _attr_ind(cod, criticidad, estructura, con_fuente):
    a = _attr("ADS", cod)
    a.update(criticidad=criticidad, estructura=estructura)
    return a, ([_fo("ADS", cod, f"C{cod}")] if con_fuente else [])


def _modelo_ind(casos, roles=(1, 2, 3, 4), relaciones=None):
    attrs, fuentes = [], []
    for c in casos:
        a, f = _attr_ind(*c)
        attrs.append(a)
        fuentes += f
    datos = _datos(atributos=attrs, fuentes_oficiales=fuentes, fuentes_consumo=[],
                   responsables=[{"codigo_dominio": "ADS", "id_rol": r, "rol": f"Rol {r}", "area": None,
                                  "codigo_plaza": str(r), "nombre_plaza": None, "nombre_responsable": None,
                                  "fecha_aprobada": None} for r in roles],
                   roles=[{"id": i, "nombre_rol": f"Rol {i}"} for i in range(1, 6)])
    return construir_modelo(datos, relaciones)


def test_puntaje_penaliza_5_3_1_solo_a_los_atributos_sin_fuente():
    m = _modelo_ind([
        (1, "Crítico", True, False),      # critico + estructura, sin fuente -> 5
        (2, "Crítico", False, False),     # solo critico, sin fuente        -> 3
        (3, "No crítico", False, False),  # otro, sin fuente                -> 1
        (4, "No crítico", True, False),   # no critico con estructura       -> 1 (supuesto: "otro caso")
        (5, "Crítico", True, True),       # con fuente oficial              -> 0
    ])
    i = m["dominios"][0]["indicadores"]
    assert i["penalizacion"] == 10 and i["puntaje"] == 90
    assert i["detalle_penalizacion"] == {"critico_estructura": {"n": 1, "puntos": 5},
                                         "critico": {"n": 1, "puntos": 3}, "otro": {"n": 2, "puntos": 2}}
    assert (i["criticos"], i["criticos_con_fo"], i["no_criticos"], i["no_criticos_con_fo"]) == (3, 1, 2, 0)
    assert [x["codigo_atributo"] for x in i["sin_fuente"]] == ["1", "2", "3", "4"]  # mayor resta primero


def test_puntaje_tiene_piso_cero_y_dominio_sin_atributos_no_tiene_puntaje():
    m = _modelo_ind([(n, "Crítico", True, False) for n in range(1, 31)])  # 30 x 5 = 150
    assert m["dominios"][0]["indicadores"]["puntaje"] == 0
    vacio = construir_modelo({"dominios": [_dom("RAE")]})["dominios"][0]["indicadores"]
    assert vacio["puntaje"] is None and vacio["pct_con_fo"] is None
    assert vacio["roles_faltantes"] == [1, 2, 3, 4] and vacio["estructura_completa"] is False


def test_estructura_completa_con_roles_1_a_4():
    completo = _modelo_ind([(1, "Crítico", False, True)], roles=(1, 2, 3, 4, 5))["dominios"][0]["indicadores"]
    assert completo["estructura_completa"] and completo["roles_faltantes"] == []
    falta = _modelo_ind([(1, "Crítico", False, True)], roles=(1, 3, 4))["dominios"][0]["indicadores"]
    assert not falta["estructura_completa"] and falta["roles_faltantes"] == [2]
    m = _modelo_ind([(1, "Crítico", False, True)])
    assert [r["nombre"] for r in m["resumen"]["indicadores"]["roles_requeridos"]] == ["Rol 1", "Rol 2", "Rol 3", "Rol 4"]


def test_vinculos_resueltos_y_pendientes_por_dominio():
    i = _modelo_ind([(1, "Crítico", False, True)], relaciones=_grafo_sintetico())["dominios"][0]["indicadores"]
    assert (i["vinculos_resueltos"], i["vinculos_pendientes"]) == (1, 0)
    sin_rel = _modelo_ind([(1, "Crítico", False, True)])["dominios"][0]["indicadores"]
    assert sin_rel["vinculos_resueltos"] is None


def test_indicadores_en_el_menu():
    html = generar_html(_modelo_ind([(1, "Crítico", False, True)]), "x")
    assert '{id:"indicadores", titulo:"Indicadores"}' in html and "function vistaIndicadores" in html


def test_explorador_de_atributos_en_el_menu():
    html = generar_html(construir_modelo(_datos(), _grafo_sintetico()), "x")
    assert '{id:"explorador", titulo:"Explorador de atributos"}' in html
    for fn in ("function vistaExplorador", "function filasFiltradasExp", "function descargarExplorador", "function desplegableMulti"):
        assert fn in html
    # filtros pedidos: dominio, criticidad, compartido/similar (y columnas seleccionables)
    for filtro in ('["dominio","Dominio"]', '["criticidad","Criticidad"]', '["compartido_similar","Compartido / similar"]'):
        assert filtro in html
    assert not re.search(r'(src|href)="https?://', html)


def test_resolver_ruta_salida():
    ahora = datetime.datetime(2026, 11, 9, 21, 5)
    assert resolver_ruta_salida("C:/x", ahora) == Path("C:/x") / "sitio_dominios_20261109_2105"   # carpeta del sitio
    assert resolver_ruta_salida("a/sitio.html", ahora) == Path("a/sitio.html")


def test_leer_todo_ejecuta_una_consulta_por_entidad():
    conn = MagicMock()
    fila = MagicMock()
    fila._mapping = {"codigo_dominio": "ADS"}
    columna = MagicMock()
    columna.scalar.return_value = 8                     # COL_LENGTH de id_estado_vigencia: existe
    conn.execute.side_effect = lambda sql: columna if sql is sitio_repository.SQL_TIENE_ESTADO_VIGENCIA else [fila]
    datos = sitio_repository.leer_todo(conn)
    assert set(datos) == {"dominios", "roles", "clasificaciones", "responsables", "respaldos", "atributos",
                          "fuentes_oficiales", "fuentes_consumo", "investigacion"}
    assert conn.execute.call_count == 10                # comprobacion de la columna + 9 entidades
    assert datos["dominios"] == [{"codigo_dominio": "ADS"}]
    assert "estado_vigencia" in str(sitio_repository.SQL_DOMINIOS)


def test_leer_todo_sin_la_migracion_de_estado_se_detiene():
    conn = MagicMock()
    conn.execute.return_value.scalar.return_value = None   # la columna no existe
    with pytest.raises(sitio_repository.FaltaEstadoVigenciaError, match="20261111_dominio_estado_vigencia"):
        sitio_repository.leer_todo(conn)
    assert conn.execute.call_count == 1                     # no lee nada mas


def test_consultas_no_leen_lo_que_el_sitio_no_muestra():
    assert "RELEASE" not in str(sitio_repository.SQL_ATRIBUTOS).upper()
    dominios = str(sitio_repository.SQL_DOMINIOS).lower()
    assert "estado_dominio" not in dominios and "version_" not in dominios


def test_consultas_solo_lectura():
    for sql in sitio_repository.CONSULTAS.values():
        texto = str(sql).upper()
        assert texto.strip().startswith("SELECT")
        assert not re.search(r"\b(INSERT|UPDATE|DELETE|MERGE|DROP|ALTER|TRUNCATE|EXEC)\b", texto)


# --- responsables agrupados por persona (v0.13.0) ---

def _resp(cod, nombre, rol="Dueño de Dominio", area="Seguros"):
    return {"codigo_dominio": cod, "id_rol": 3, "rol": rol, "area": area, "codigo_plaza": "P", "nombre_plaza": "Plaza",
            "nombre_responsable": nombre, "fecha_aprobada": None}


def test_responsables_agrupados_por_persona():
    from gdd_loader.exportar.sitio_dominios import agrupar_responsables, clave_persona
    assert clave_persona("  José   PÉREZ ") == clave_persona("jose perez") == "jose perez"
    datos = _datos(responsables=[
        _resp("ADS", "José Pérez", rol="Dueño de Dominio"),
        _resp("ADS", "jose  perez", rol="Data Steward Calidad"),     # misma persona, otro rol, mismo dominio
        _resp("CLI", "JOSÉ PÉREZ", rol="Coordinador de Dominio", area="Clientes"),
        _resp("CLI", "Ana Ruiz"),
        _resp("CLI", None),                                            # sin nombre: no se agrupa
        _resp("CLI", "  "),
    ])
    m = construir_modelo(datos)
    R = m["resumen"]["responsables"]
    assert m["resumen"]["totales"]["estructura"] == R["asignaciones"] == 6
    assert R["personas"] == 2 and R["asignaciones_sin_nombre"] == 2
    assert R["personas_varios_dominios"] == 1 and R["personas_varias_asignaciones"] == 1
    assert R["por_n_dominios"] == [[1, 1], [2, 1]]
    jose, ana = R["por_persona"]                                       # orden: mas asignaciones primero
    assert jose == {"nombre": "José Pérez", "asignaciones": 3, "n_dominios": 2, "dominios": ["ADS", "CLI"],
                    "roles": ["Coordinador de Dominio", "Data Steward Calidad", "Dueño de Dominio"],
                    "areas": ["Clientes", "Seguros"]}
    assert ana["asignaciones"] == 1 and ana["dominios"] == ["CLI"]
    assert agrupar_responsables([])["por_n_dominios"] == [] and agrupar_responsables([])["personas"] == 0


def test_responsables_por_persona_en_resumen_e_indicadores():
    html = generar_html(construir_modelo(_datos()), "x")
    assert "function tablaPersonas" in html and "function notaResponsables" in html
    assert html.count('tarjeta("Responsables por persona"') == 2      # resumen de dominios e indicadores
    for etiqueta in ("Dominios activos", "Total de atributos", "Registros de fuente oficial", "Registros de fuente de consumo",
                     "Registros de responsables", "Registros de investigación"):
        assert html.count('["' + etiqueta + '"') == (1 if etiqueta == "Dominios activos" else 2)   # resumen y ficha
    paginas = generar_sitio(construir_modelo(_datos()), "x")
    assert all(_modelo_del_html(h)["resumen"]["responsables"]["personas"] == 1 for h in paginas.values())


# --- estado de vigencia del dominio (v0.13.0) ---

def _datos_vigencia():
    """ADS Confidencial (con fuentes reales), CLI Activo, OLD Inactivo con todo tipo de registros."""
    dom = lambda cod, estado: {**_dom(cod), "estado_vigencia": estado}
    return _datos(
        dominios=[dom("ADS", "Confidencial"), dom("CLI", "Activo"), dom("OLD", "Inactivo")],
        responsables=_datos()["responsables"] + [_resp("OLD", "Persona Vieja")],
        atributos=_datos()["atributos"] + [_attr("OLD", 1)],
        fuentes_oficiales=_datos()["fuentes_oficiales"] + [_fo("OLD", 1, "CAMPO_VIEJO"), _fo("CLI", 1, "CAMPO_CLI")],
        fuentes_consumo=_datos()["fuentes_consumo"] + [_fc("OLD", 1, "campo_viejo")],
        investigacion=_datos()["investigacion"] + [{"codigo_dominio": "OLD", "clasificacion": "Norma", "nombre": "N",
                                                    "descripcion": "D", "referencia_normativa": None}],
    )


def test_clasificar_vigencia():
    from gdd_loader.exportar.sitio_dominios import clasificar_vigencia
    assert clasificar_vigencia("Activo") == clasificar_vigencia(None) == clasificar_vigencia("") == "Activo"
    assert clasificar_vigencia(" INACTIVO ") == "Inactivo"
    assert clasificar_vigencia("Confidencial") == "Confidencial"
    assert clasificar_vigencia("Otro estado") == "Confidencial"     # desconocido: no publica fuentes


def test_dominio_inactivo_no_se_publica_y_no_cuenta_como_huerfano():
    m = construir_modelo(_datos_vigencia())
    assert [d["codigo"] for d in m["dominios"]] == ["ADS", "CLI"]
    r = m["resumen"]
    assert r["n_dominios"] == 2 and r["n_inactivos"] == 1 and r["n_confidenciales"] == 1
    assert r["huerfanos"] == {}
    assert r["excluidos_inactivos"] == {"responsables": 1, "atributos": 1, "investigacion": 1,
                                        "fuentes_oficiales": 1, "fuentes_consumo": 1}
    assert r["totales"]["atributos"] == 3 and r["responsables"]["personas"] == 1   # sin "Persona Vieja"
    html = generar_html(m, "x")
    assert "Persona Vieja" not in json.dumps(_modelo_del_html(html), ensure_ascii=False)


def test_dominio_confidencial_sin_detalle_de_fuentes_pero_con_conteos_e_indicadores():
    m = construir_modelo(_datos_vigencia())
    ads, cli = m["dominios"]
    assert ads["estado_vigencia"] == "Confidencial" and cli["estado_vigencia"] == "Activo"
    a2 = next(a for a in ads["atributos"] if a["codigo_atributo"] == "2")
    assert a2["fuentes_oficiales"] == [] and a2["fuentes_consumo"] == []          # el detalle no viaja
    assert a2["n_fuentes_oficiales"] == 2 and a2["n_fuentes_consumo"] == 1          # la cantidad si
    assert ads["conteos"]["fuentes_oficiales"] == 2 and ads["conteos"]["fuentes_consumo"] == 1
    assert ads["indicadores"]["con_fo"] == 1                                        # indicadores con datos reales
    assert cli["atributos"][0]["fuentes_oficiales"][0]["nombre_campo"] == "CAMPO_CLI"  # Activo: completo
    for html in [generar_html(m, "x")] + list(generar_sitio(m, "x").values()):
        datos = json.dumps(_modelo_del_html(html), ensure_ascii=False)
        assert "CAMPO_A" not in datos and "CAMPO_B" not in datos and "campo_x" not in datos
        assert "CAMPO_VIEJO" not in datos


def test_relaciones_sin_dominios_inactivos():
    from gdd_loader.exportar.grafo_relaciones import construir_modelo as construir_grafo
    from gdd_loader.exportar.sitio_dominios import (codigos_inactivos, describir_estado_inactivo,
                                                    filtrar_relaciones_vigencia)
    assert codigos_inactivos(_datos_vigencia()["dominios"]) == {"OLD"}
    dominios = [{"id_dominio": i, "codigo_dominio": c, "nombre_dominio": c, "tipo_dominio": None, "n_atributos_activos": 1}
                for i, c in ((1, "ADS"), (2, "CLI"), (3, "OLD"))]
    rel = lambda io, co, idd, cd: {"id_atributo_origen": io * 10, "codigo_atributo_origen": 1, "nombre_atributo_origen": "A",
                                   "id_dominio_origen": io, "codigo_dominio_origen": co, "nombre_dominio_origen": co,
                                   "tipo_relacion": "Compartido", "id_atributo_destino": idd * 10, "nombre_atributo_destino": "B",
                                   "atributo_destino_activo": True, "id_dominio_destino": idd, "codigo_dominio_destino": cd,
                                   "nombre_dominio_destino": cd, "estado_vinculo": "RESUELTO"}
    doms, rels = filtrar_relaciones_vigencia(dominios, [rel(1, "ADS", 2, "CLI"), rel(1, "ADS", 3, "OLD"), rel(3, "OLD", 2, "CLI")],
                                             {"OLD"})
    modelo = describir_estado_inactivo(construir_grafo(doms, rels))
    assert [n["codigo"] for n in modelo["nodos"]] == ["ADS", "CLI"]                 # sin nodo OLD
    assert len(modelo["aristas"]) == 1 and [p["estado"] for p in modelo["problemas"]] == ["DOMINIO_INACTIVO"]
    assert "Inactivo" in modelo["descripcion_estado"]["DOMINIO_INACTIVO"]


def test_explorador_fuentes_en_columnas_separadas_y_estado_del_dominio():
    html = generar_html(construir_modelo(_datos_vigencia()), "x")
    for col in ('["fo_servidor","Servidor (f. oficial)"', '["fo_base_datos"', '["fo_tabla"', '["fo_campo"',
                '["fc_clase"', '["fc_servidor"', '["fc_base_datos"', '["fc_tabla"', '["fc_campo"',
                '["estado_vigencia","Estado del dominio"'):
        assert col in html
    assert '["fuentes_oficiales","Fuentes oficiales",60' not in html and '["fuentes_consumo","Fuentes de consumo",60' not in html
    assert 'const OCULTO_CONF = "(confidencial)"' in html and "function etqConf" not in html and "const etqConf" in html


# --- v0.13.1: responsables por rol en el Explorador e Investigacion por secciones del catalogo ---

_CLASIFICACIONES = [{"id": 6, "nombre_clasificacion": "Actualización de datos"}, {"id": 1, "nombre_clasificacion": "Normativas e instructivos"},
                    {"id": 5, "nombre_clasificacion": "Término"}, {"id": 2, "nombre_clasificacion": "Procesos relacionados"}]
_ROLES = [{"id": 3, "nombre_rol": "Dueño de Dominio"}, {"id": 1, "nombre_rol": "Data Steward Implementación"}]


def test_resumen_trae_secciones_de_investigacion_y_roles_del_catalogo():
    m = construir_modelo(_datos(clasificaciones=_CLASIFICACIONES, roles=_ROLES))
    r = m["resumen"]
    # orden del catalogo (id), sin Termino; "Actualización de datos" aunque no tenga registros
    assert r["secciones_investigacion"] == ["Normativas e instructivos", "Procesos relacionados", "Actualización de datos"]
    assert r["roles"] == [{"id": 1, "nombre": "Data Steward Implementación"}, {"id": 3, "nombre": "Dueño de Dominio"}]
    assert construir_modelo(_datos())["resumen"]["secciones_investigacion"] == []      # sin catalogo: no falla


def test_explorador_lleva_la_estructura_para_las_columnas_de_responsables():
    modelo = construir_modelo(_datos(roles=_ROLES))
    exp = _modelo_del_html(generar_sitio(modelo, "x")["explorador.html"])
    ads = next(d for d in exp["dominios"] if d["codigo"] == "ADS")
    assert [r["nombre_responsable"] for r in ads["estructura"]] == ["Persona A"]
    html = generar_html(modelo, "x")
    assert "function responsablesPorRol" in html and 'clave:"resp_" + r.id' in html
    assert "...Object.values(resp)" in html                      # la busqueda de texto incluye a los responsables


def test_investigacion_en_tarjetas_con_todas_las_secciones():
    html = generar_html(construir_modelo(_datos(clasificaciones=_CLASIFICACIONES)), "x")
    assert "DATOS.resumen.secciones_investigacion" in html and 'texto: g.total ? "Ningún registro' in html
    assert 'el("article", {clase:"termino"}' in html and "indice-secciones" in html
    assert "filtroInvestigacion" in html


# --- sitio de varias paginas (v0.13.0) ---

def test_datos_comprimidos_ida_y_vuelta_y_deterministas():
    modelo = construir_modelo(_datos(), _grafo_sintetico())
    texto = comprimir_datos(modelo)
    assert re.fullmatch(r"[A-Za-z0-9+/=]+", texto)          # seguro dentro de <script>
    assert descomprimir_datos(texto) == json.loads(json.dumps(modelo))
    assert comprimir_datos(modelo) == texto                 # mismo modelo, mismo archivo


def test_generar_sitio_una_pagina_por_seccion_con_index():
    paginas = generar_sitio(construir_modelo(_datos(), _grafo_sintetico()), "2026-11-11 10:00", "Glosario")
    assert list(paginas) == ["index.html", "atributos.html", "explorador.html", "investigacion.html",
                             "terminos.html", "relaciones.html", "indicadores.html"]
    for seccion, archivo in PAGINAS.items():
        html = paginas[archivo]
        assert _config_del_html(html) == {"seccion": seccion, "paginas": PAGINAS}
        assert "<h1>Glosario</h1>" in html                   # mismo diseno y menu en todas
        assert not re.search(r'(src|href)="https?://', html)  # sin recursos externos
        assert not re.search(r"<script[^>]+src=", html)       # ni archivos aparte: cada pagina se basta sola
    assert "function irA" in paginas["index.html"] and 'el("a", {clase:' in paginas["index.html"]


def test_vis_network_solo_en_la_pagina_de_relaciones():
    paginas = generar_sitio(construir_modelo(_datos(), _grafo_sintetico()), "x")
    assert [a for a, h in paginas.items() if _inicio_vis() in h] == ["relaciones.html"]
    sin_grafo = generar_sitio(construir_modelo(_datos()), "x")
    assert not any(_inicio_vis() in h for h in sin_grafo.values())


def test_cada_pagina_lleva_solo_los_datos_que_usa():
    modelo = construir_modelo(_datos(), _grafo_sintetico())
    paginas = {s: _modelo_del_html(h) for s, h in zip(PAGINAS, generar_sitio(modelo, "x").values())}
    ads = {s: next(d for d in m["dominios"] if d["codigo"] == "ADS") for s, m in paginas.items()}
    resumen = json.loads(json.dumps(modelo["resumen"]))
    for s in PAGINAS:   # menu lateral y tabla resumen en todas
        assert ads[s]["nombre"] and ads[s]["info"] and ads[s]["conteos"]["atributos"] == 2
        assert paginas[s]["resumen"] == resumen
    assert len(ads["atributos"]["atributos"]) == len(ads["explorador"]["atributos"]) == 2
    assert ads["estructura"]["atributos"] == [] and len(ads["estructura"]["estructura"]) == 1
    assert ads["relaciones"]["atributos"] == [] and ads["relaciones"]["investigacion"] == []
    assert len(ads["terminos"]["investigacion"]) == 1 and ads["investigacion"]["investigacion"]
    assert ads["indicadores"]["indicadores"]["atributos"] == 2 and ads["estructura"]["indicadores"] is None
    assert {s for s, m in paginas.items() if m["relaciones"]} == {"atributos", "explorador", "relaciones", "indicadores"}
    assert modelo["dominios"][0]["atributos"] and modelo["relaciones"]   # el modelo original no cambia


def test_seccion_desconocida():
    with pytest.raises(KeyError):
        modelo_para_seccion(construir_modelo(_datos()), "otra")
    with pytest.raises(ValueError):
        generar_html(construir_modelo(_datos()), "x", seccion="otra")


def test_paginas_excedidas_y_escribir_sitio_sin_sobrescribir(tmp_path):
    assert paginas_excedidas({"a.html": "x" * 1_500_000, "b.html": "y"}, 1.0) == [("a.html", 1.5)]
    destino = tmp_path / "sitio_dominios_20261111_1000"
    escribir_sitio(destino, {"index.html": "<p>1</p>", "atributos.html": "<p>2</p>"})
    assert sorted(p.name for p in destino.iterdir()) == ["atributos.html", "index.html"]
    with pytest.raises(FileExistsError):
        escribir_sitio(destino, {"index.html": "<p>otro</p>"})
    assert (destino / "index.html").read_text(encoding="utf-8") == "<p>1</p>"

"""Tests del sitio de consulta por dominio (datos sinteticos, sin BD)."""

from __future__ import annotations

import datetime
import json
import re
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

from gdd_loader.exportar.sitio_dominios import construir_modelo, generar_html
from gdd_loader.load import sitio_repository
from gdd_loader.sitio_cli import resolver_ruta_salida


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
    assert "<\\/script>" in html
    assert "Glosario &lt;PRUEBAS&gt;" in html
    assert not re.search(r'(src|href)="https?://', html)
    # el JSON embebido se puede leer de vuelta
    bloque = re.search(r'<script id="datos" type="application/json">(.*?)</script>', html, re.S).group(1)
    modelo = json.loads(bloque.replace("<\\/", "</"))
    assert modelo["dominios"][0]["codigo"] == "ADS"


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
    bloque = re.search(r'<script id="datos" type="application/json">(.*?)</script>', con, re.S).group(1)
    rel = json.loads(bloque.replace("<\\/", "</"))["relaciones"]
    assert rel["resumen"]["n_aristas"] == 1 and rel["aristas"][0]["atributos"][0]["codigo_origen"] == "ADS-2"

    sin = generar_html(construir_modelo(_datos()), "x")
    assert _inicio_vis() not in sin            # sin vista: no se incrustan ~690 KB de vis-network
    assert '"relaciones": null' in sin
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


def test_resolver_ruta_salida():
    ahora = datetime.datetime(2026, 11, 9, 21, 5)
    assert resolver_ruta_salida("C:/x", ahora) == Path("C:/x") / "sitio_dominios_20261109_2105.html"
    assert resolver_ruta_salida("a/sitio.html", ahora) == Path("a/sitio.html")


def test_leer_todo_ejecuta_una_consulta_por_entidad():
    conn = MagicMock()
    fila = MagicMock()
    fila._mapping = {"codigo_dominio": "ADS"}
    conn.execute.return_value = [fila]
    datos = sitio_repository.leer_todo(conn)
    assert set(datos) == {"dominios", "responsables", "respaldos", "atributos",
                          "fuentes_oficiales", "fuentes_consumo", "investigacion"}
    assert conn.execute.call_count == 7
    assert datos["dominios"] == [{"codigo_dominio": "ADS"}]


def test_consultas_no_leen_lo_que_el_sitio_no_muestra():
    assert "RELEASE" not in str(sitio_repository.SQL_ATRIBUTOS).upper()
    dominios = str(sitio_repository.SQL_DOMINIOS).lower()
    assert "estado_dominio" not in dominios and "version_" not in dominios


def test_consultas_solo_lectura():
    for sql in sitio_repository.CONSULTAS.values():
        texto = str(sql).upper()
        assert texto.strip().startswith("SELECT")
        assert not re.search(r"\b(INSERT|UPDATE|DELETE|MERGE|DROP|ALTER|TRUNCATE|EXEC)\b", texto)

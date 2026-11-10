"""Tests del grafo de relaciones entre dominios (datos sinteticos, sin BD)."""

from __future__ import annotations

import datetime
import json
import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from gdd_loader.exportar.grafo_relaciones import construir_modelo, generar_html
from gdd_loader.grafo_cli import resolver_ruta_salida
from gdd_loader.load import grafo_repository


def _dom(id_, cod, nombre=None, n=10):
    return {"id_dominio": id_, "codigo_dominio": cod, "nombre_dominio": nombre or f"Dominio {cod}",
            "tipo_dominio": "Maestro", "n_atributos_activos": n}


def _rel(id_attr, o, cod_o, d, cod_d, tipo="Compartido", estado="RESUELTO", cod_attr=None):
    return {"id_atributo_origen": id_attr, "codigo_atributo_origen": cod_attr or id_attr,
            "nombre_atributo_origen": f"Atributo {id_attr}", "id_dominio_origen": o,
            "codigo_dominio_origen": cod_o, "nombre_dominio_origen": f"Dominio {cod_o}",
            "tipo_relacion": tipo, "id_atributo_destino": None if d is None else 900 + id_attr,
            "nombre_atributo_destino": None if d is None else f"Primario {id_attr}",
            "atributo_destino_activo": estado != "DESTINO_INACTIVO",
            "id_dominio_destino": d, "codigo_dominio_destino": cod_d,
            "nombre_dominio_destino": None if cod_d is None else f"Dominio {cod_d}",
            "estado_vinculo": estado}


DOMINIOS = [_dom(1, "ADS"), _dom(2, "CLI"), _dom(3, "PRD"), _dom(4, "AIS")]


def test_agrega_por_par_y_tipo():
    m = construir_modelo(DOMINIOS, [
        _rel(1, 1, "ADS", 2, "CLI"), _rel(2, 1, "ADS", 2, "CLI"),
        _rel(3, 1, "ADS", 2, "CLI", tipo="Similar"), _rel(4, 1, "ADS", 3, "PRD"),
    ])
    aristas = {(a["origen"], a["destino"], a["tipo"]): a["n"] for a in m["aristas"]}
    assert aristas == {(1, 2, "Compartido"): 2, (1, 2, "Similar"): 1, (1, 3, "Compartido"): 1}
    assert m["resumen"]["n_pares_dominio"] == 2
    assert m["resumen"]["n_atributos_en_grafo"] == 4
    assert [a["id"] for a in m["aristas"]] == list(range(len(m["aristas"])))


def test_grado_cuenta_dominios_distintos_no_atributos():
    m = construir_modelo(DOMINIOS, [
        _rel(1, 1, "ADS", 2, "CLI"), _rel(2, 1, "ADS", 2, "CLI", tipo="Similar"),
        _rel(3, 3, "PRD", 2, "CLI"),
    ])
    n = {x["codigo"]: x for x in m["nodos"]}
    assert n["CLI"]["dependientes"] == 2      # ADS y PRD
    assert n["ADS"]["dependencias"] == 1      # solo CLI, aunque con 2 tipos
    assert n["AIS"]["dependientes"] == n["AIS"]["dependencias"] == 0
    assert m["resumen"]["n_dominios_con_relacion"] == 3


def test_destino_inactivo_dibuja_y_se_cuenta_aparte():
    m = construir_modelo(DOMINIOS, [_rel(1, 1, "ADS", 2, "CLI", estado="DESTINO_INACTIVO"),
                                    _rel(2, 1, "ADS", 2, "CLI")])
    (a,) = m["aristas"]
    assert (a["n"], a["n_inactivo"]) == (2, 1)
    assert m["problemas"] == []


@pytest.mark.parametrize("estado", ["SIN_VINCULO", "VINCULO_ROTO", "INCONSISTENTE", "MISMO_DOMINIO", "SIN_TIPO"])
def test_estados_sin_destino_confiable_no_generan_arista(estado):
    d, cod = (None, None) if estado == "SIN_VINCULO" else (2, "CLI")
    m = construir_modelo(DOMINIOS, [_rel(1, 1, "ADS", d, cod, estado=estado)])
    assert m["aristas"] == []
    assert [p["estado"] for p in m["problemas"]] == [estado]
    assert m["resumen"]["n_atributos_relacionados"] == 1
    assert m["resumen"]["n_atributos_en_grafo"] == 0


def test_sin_vinculo_se_marca_en_el_nodo_origen():
    m = construir_modelo(DOMINIOS, [_rel(1, 1, "ADS", None, None, estado="SIN_VINCULO"),
                                    _rel(2, 1, "ADS", None, None, estado="SIN_VINCULO")])
    assert {x["codigo"]: x["sin_vinculo"] for x in m["nodos"]}["ADS"] == 2


def test_tipo_nulo_se_agrupa_como_sin_tipo():
    m = construir_modelo(DOMINIOS, [_rel(1, 1, "ADS", 2, "CLI", tipo=None)])
    assert m["aristas"][0]["tipo"] == "Sin tipo"
    assert m["tipos"] == ["Sin tipo"]


def test_dominio_no_listado_se_agrega_como_desconocido():
    m = construir_modelo([_dom(1, "ADS")], [_rel(1, 1, "ADS", 7, "XYZ")])
    n = {x["codigo"]: x for x in m["nodos"]}
    assert n["XYZ"]["desconocido"] is True


def test_html_autocontenido_y_datos_escapados():
    m = construir_modelo(DOMINIOS, [_rel(1, 1, "ADS", 2, "CLI")])
    m["nodos"][0]["nombre"] = "Malicioso </script><script>alert(1)</script>"
    html = generar_html(m, "2026-11-05 17:00", titulo="Prueba <b>")
    assert "__DATOS__" not in html and "__VIS_JS__" not in html
    assert "vis-network" in html and "<title>Prueba &lt;b&gt;</title>" in html
    assert "src=\"http" not in html and "href=\"http" not in html   # sin dependencias externas
    assert "alert(1)</script>" not in html                            # no rompe el <script>
    datos = re.search(r"const M = (\{.*?\});\n", html, re.S).group(1)
    assert json.loads(datos.replace("<\\/", "</"))["nodos"][0]["nombre"].startswith("Malicioso")


def test_ruta_salida_carpeta_o_archivo(tmp_path):
    ahora = datetime.datetime(2026, 11, 5, 17, 30)
    assert resolver_ruta_salida(str(tmp_path), ahora) == tmp_path / "grafo_relaciones_20261105_1730.html"
    assert resolver_ruta_salida(str(tmp_path / "x.HTML"), ahora) == tmp_path / "x.HTML"


def test_repositorio_traduce_vista_faltante():
    conn = MagicMock()
    conn.execute.side_effect = Exception("Invalid object name 'gdd.v_relacion_atributo'.")
    with pytest.raises(grafo_repository.VistaNoExisteError, match="20261105_vistas_relacion_dominio"):
        grafo_repository.leer_relaciones(conn)


def test_repositorio_no_oculta_otros_errores():
    conn = MagicMock()
    conn.execute.side_effect = Exception("Login failed")
    with pytest.raises(Exception, match="Login failed"):
        grafo_repository.leer_relaciones(conn)


def test_repositorio_solo_hace_select():
    for sql in (grafo_repository.SQL_DOMINIOS, grafo_repository.SQL_RELACIONES):
        assert str(sql).lstrip().upper().startswith("SELECT")
        assert not re.search(r"\b(INSERT|UPDATE|DELETE|MERGE|DROP|ALTER)\b", str(sql).upper())


def test_recurso_vis_network_empaquetado():
    from gdd_loader.exportar import grafo_relaciones as g
    assert (g.RECURSOS / g.ARCHIVO_VIS).stat().st_size > 100_000
    assert (Path(g.RECURSOS) / "vis-network-LICENSE-MIT").exists()

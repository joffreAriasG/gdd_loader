"""GDD-04: respaldo logico a zip de CSV (sin base de datos)."""

import csv
import datetime as dt
import io
import json
import zipfile
from decimal import Decimal

import pytest

from gdd_loader.pipeline.respaldo import (
    FORMATO,
    NOMBRE_MANIFIESTO,
    Columna,
    Manifiesto,
    escribir_respaldo,
    formatear_valor,
    verificar_respaldo,
)


def _manifiesto():
    return Manifiesto(FORMATO, "0.15.0", "srv", "DGODELIVERY", "2026-11-19T10:00:00", ["gdd"])


def _csv(texto_bytes):
    return list(csv.reader(io.StringIO(texto_bytes.decode("utf-8"))))


@pytest.fixture
def respaldo(tmp_path):
    cols = [Columna("id", "int", False), Columna("nombre", "nvarchar(50)", True),
            Columna("monto", "decimal(18,4)", True), Columna("fecha", "datetime", True),
            Columna("activo", "bit", False)]
    filas = [
        (1, "Linea\ncon salto, coma y \"comillas\"", Decimal("12345678901234.5600"), dt.datetime(2026, 11, 19, 8, 30), True),
        (2, "", None, None, False),
        (3, None, Decimal("0.0001"), dt.date(2026, 1, 2), True),
    ]
    ruta = tmp_path / "r.zip"
    escribir_respaldo(ruta, _manifiesto(), [
        ("gdd", "atributo", cols, iter(filas), lambda: 3),
        ("gdd", "vacia", [Columna("id", "int", False)], iter([]), lambda: 0),
    ])
    return ruta


def test_zip_con_un_csv_por_tabla_y_manifiesto(respaldo):
    with zipfile.ZipFile(respaldo) as zf:
        assert sorted(zf.namelist()) == ["gdd/atributo.csv", "gdd/vacia.csv", NOMBRE_MANIFIESTO]
        man = json.loads(zf.read(NOMBRE_MANIFIESTO))
    assert man["formato"] == FORMATO and man["base_datos"] == "DGODELIVERY"
    t = {x["tabla"]: x for x in man["tablas"]}
    assert t["atributo"]["filas"] == 3 and t["atributo"]["filas_bd"] == 3
    assert t["atributo"]["columnas"][2] == {"nombre": "monto", "tipo": "decimal(18,4)", "nulable": True}
    assert t["vacia"]["filas"] == 0
    assert not respaldo.with_name("r.zip.parcial").exists()


def test_null_distinto_de_texto_vacio_y_valores_exactos(respaldo):
    with zipfile.ZipFile(respaldo) as zf:
        crudo = zf.read("gdd/atributo.csv").decode("utf-8")
    lineas = crudo.split("\n")
    assert lineas[0] == '"id","nombre","monto","fecha","activo"'
    # fila 2: texto vacio entre comillas, NULL sin nada
    assert '2,"",,,0' in crudo
    # fila 3: NULL en nombre, Decimal exacto sin notacion cientifica
    assert "3,,0.0001,\"2026-01-02\",1" in crudo
    filas = _csv(crudo.encode("utf-8"))
    assert filas[1][1] == 'Linea\ncon salto, coma y "comillas"'
    assert filas[1][2] == "12345678901234.5600"
    assert filas[1][3] == "2026-11-19T08:30:00"


def test_verificar_respaldo_integro(respaldo):
    assert verificar_respaldo(respaldo) == []


def test_verificar_detecta_csv_alterado(respaldo, tmp_path):
    alterado = tmp_path / "alterado.zip"
    with zipfile.ZipFile(respaldo) as src, zipfile.ZipFile(alterado, "w") as dst:
        for nombre in src.namelist():
            datos = src.read(nombre)
            if nombre == "gdd/atributo.csv":
                datos = datos.replace(b"0.0001", b"0.0002")
            dst.writestr(nombre, datos)
    assert verificar_respaldo(alterado) == ["gdd/atributo.csv: SHA-256 no coincide"]


def test_verificar_avisa_si_la_tabla_cambio_mientras_se_leia(tmp_path):
    ruta = tmp_path / "r.zip"
    escribir_respaldo(ruta, _manifiesto(), [("gdd", "t", [Columna("id", "int", False)], iter([(1,), (2,)]), lambda: 5)])
    problemas = verificar_respaldo(ruta)
    assert len(problemas) == 1 and "tenia 5 al terminar" in problemas[0]


@pytest.mark.parametrize("valor, esperado", [
    (None, None),
    (True, 1),
    (7, 7),
    (dt.time(8, 5), "08:05:00"),
    (b"\x01\xab", "0x01AB"),
    ("texto", "texto"),
])
def test_formatear_valor(valor, esperado):
    assert formatear_valor(valor) == esperado


def test_formatear_decimal_conserva_el_texto_exacto(tmp_path):
    ruta = tmp_path / "d.zip"
    escribir_respaldo(ruta, _manifiesto(), [("gdd", "d", [Columna("a", "decimal", True), Columna("b", "decimal", True)],
                                             iter([(Decimal("1E+3"), Decimal("-0.10"))]), None)])
    with zipfile.ZipFile(ruta) as zf:
        assert zf.read("gdd/d.csv").decode("utf-8").split("\n")[1] == "1000,-0.10"

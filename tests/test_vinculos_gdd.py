"""
Pruebas de la funcion adicional de vinculos pendientes
(pipeline/vinculos_gdd.py). Sin base real: se reemplazan gdd_repository y
vinculos_repository con mocks.
"""

from __future__ import annotations

import datetime
from unittest.mock import MagicMock

import pytest

from gdd_loader.load import vinculos_repository as vrepo_real
from gdd_loader.pipeline import vinculos_gdd

FECHA = datetime.date(2026, 1, 31)


def _fila(codigo_atributo="1", atributo="Cupo Aprobado", dominio="Cupos", fecha=FECHA):
    return {
        "codigo_dominio": "ADS",
        "codigo_atributo": codigo_atributo,
        "atributo_primario": atributo,
        "dominio_primario": dominio,
        "fecha_aprobada": fecha,
    }


def _actual(id_=10, fecha=FECHA, id_attr=None, id_dom=None, version="2"):
    return {"id": id_, "version": version, "fecha_aprobacion": fecha,
            "id_atributo_primario": id_attr, "id_dominio_primario": id_dom}


@pytest.fixture
def entorno(monkeypatch):
    staging = MagicMock()
    staging.leer.return_value = [_fila()]
    repo = MagicMock()
    repo.buscar_atributo_primario.return_value = (777, 88)
    vrepo = MagicMock()
    vrepo.atributo_activo.return_value = _actual()
    monkeypatch.setattr(vinculos_gdd, "repo", repo)
    monkeypatch.setattr(vinculos_gdd, "vrepo", vrepo)
    engine = MagicMock()
    return engine, staging, repo, vrepo


def test_simulacion_no_escribe_y_usa_connect(entorno):
    engine, staging, repo, vrepo = entorno
    r = vinculos_gdd.resolver_vinculos_dominio(engine, staging, "ADS", aplicar=False)
    assert r.ok and not r.aplicado
    assert len(r.resueltos) == 1
    assert r.resueltos[0].codigo_dominio_atributo == "ADS-1"
    assert r.resueltos[0].columnas == {"id_atributo_primario": 777, "id_dominio_primario": 88}
    vrepo.completar_vinculos.assert_not_called()
    repo.registrar_bitacora.assert_not_called()
    engine.connect.assert_called_once()
    engine.begin.assert_not_called()


def test_aplicar_completa_ambos_y_registra_bitacora(entorno):
    engine, staging, repo, vrepo = entorno
    r = vinculos_gdd.resolver_vinculos_dominio(engine, staging, "ADS", aplicar=True)
    assert r.ok and r.aplicado and len(r.resueltos) == 1
    conn = engine.begin.return_value.__enter__.return_value
    vrepo.completar_vinculos.assert_called_once_with(
        conn, 10, {"id_atributo_primario": 777, "id_dominio_primario": 88})
    repo.registrar_bitacora.assert_called_once_with(
        conn, "atributo", 10, "ACTUALIZAR", "ADS", "ADS-1", "2")
    repo.buscar_atributo_primario.assert_called_once_with(conn, "Cupo Aprobado", "Cupos")


def test_completa_solo_la_columna_en_null(entorno):
    engine, staging, repo, vrepo = entorno
    vrepo.atributo_activo.return_value = _actual(id_attr=777, id_dom=None)
    r = vinculos_gdd.resolver_vinculos_dominio(engine, staging, "ADS", aplicar=True)
    assert r.ok and not r.conflictos
    assert r.resueltos[0].columnas == {"id_dominio_primario": 88}
    assert vrepo.completar_vinculos.call_args.args[2] == {"id_dominio_primario": 88}


def test_conflicto_si_el_valor_existente_difiere_no_escribe(entorno):
    engine, staging, repo, vrepo = entorno
    vrepo.atributo_activo.return_value = _actual(id_attr=999, id_dom=None)
    r = vinculos_gdd.resolver_vinculos_dominio(engine, staging, "ADS", aplicar=True)
    assert r.ok and len(r.conflictos) == 1 and not r.resueltos
    assert "ADS-1" in r.conflictos[0]
    vrepo.completar_vinculos.assert_not_called()
    repo.registrar_bitacora.assert_not_called()


def test_ya_vinculado_no_resuelve_ni_escribe(entorno):
    engine, staging, repo, vrepo = entorno
    vrepo.atributo_activo.return_value = _actual(id_attr=777, id_dom=88)
    r = vinculos_gdd.resolver_vinculos_dominio(engine, staging, "ADS", aplicar=True)
    assert r.ya_vinculados == 1 and not r.resueltos
    repo.buscar_atributo_primario.assert_not_called()
    vrepo.completar_vinculos.assert_not_called()


def test_fecha_distinta_se_omite(entorno):
    engine, staging, repo, vrepo = entorno
    vrepo.atributo_activo.return_value = _actual(fecha=datetime.date(2025, 12, 31))
    r = vinculos_gdd.resolver_vinculos_dominio(engine, staging, "ADS", aplicar=True)
    assert r.omitidos_fecha == 1 and not r.resueltos
    repo.buscar_atributo_primario.assert_not_called()
    vrepo.completar_vinculos.assert_not_called()


def test_fecha_datetime_de_staging_se_compara_como_fecha(entorno):
    engine, staging, repo, vrepo = entorno
    staging.leer.return_value = [_fila(fecha=datetime.datetime(2026, 1, 31, 0, 0))]
    r = vinculos_gdd.resolver_vinculos_dominio(engine, staging, "ADS", aplicar=False)
    assert r.omitidos_fecha == 0 and len(r.resueltos) == 1


def test_sin_coincidencia_se_informa_y_no_escribe(entorno):
    engine, staging, repo, vrepo = entorno
    repo.buscar_atributo_primario.return_value = (None, None)
    r = vinculos_gdd.resolver_vinculos_dominio(engine, staging, "ADS", aplicar=True)
    assert r.ok and not r.resueltos
    assert r.sin_coincidencia == [("ADS-1", "Cupo Aprobado", "Cupos")]
    vrepo.completar_vinculos.assert_not_called()


@pytest.mark.parametrize("atributo,dominio", [("-", "Cupos"), ("Cupo", "-"), (None, None), ("", "")])
def test_sin_ambos_textos_no_se_procesa(entorno, atributo, dominio):
    engine, staging, repo, vrepo = entorno
    staging.leer.return_value = [_fila(atributo=atributo, dominio=dominio)]
    r = vinculos_gdd.resolver_vinculos_dominio(engine, staging, "ADS", aplicar=True)
    assert r.sin_texto == 1 and not r.resueltos
    vrepo.atributo_activo.assert_not_called()


def test_atributo_que_no_esta_en_gdd(entorno):
    engine, staging, repo, vrepo = entorno
    vrepo.atributo_activo.return_value = None
    r = vinculos_gdd.resolver_vinculos_dominio(engine, staging, "ADS", aplicar=True)
    assert r.no_en_gdd == 1 and not r.resueltos


def test_error_en_un_dominio_lo_reporta_y_limpia_resueltos(entorno):
    engine, staging, repo, vrepo = entorno
    staging.leer.return_value = [_fila("1"), _fila("2")]
    vrepo.completar_vinculos.side_effect = [None, RuntimeError("boom")]
    r = vinculos_gdd.resolver_vinculos_dominio(engine, staging, "ADS", aplicar=True)
    assert not r.ok and "boom" in r.error
    assert r.resueltos == []


def test_repositorio_solo_escribe_columnas_de_vinculo_y_solo_si_siguen_null():
    conn = MagicMock()
    vrepo_real.completar_vinculos(conn, 10, {"id_dominio_primario": 88})
    sql = str(conn.execute.call_args.args[0])
    assert "SET [id_dominio_primario] = :id_dominio_primario" in sql
    assert "[id_dominio_primario] IS NULL" in sql
    assert conn.execute.call_args.args[1] == {"id_dominio_primario": 88, "id": 10}


@pytest.mark.parametrize("valores", [{}, {"version": "9"}, {"id_dominio_primario": 1, "fecha_aprobacion": 2}])
def test_repositorio_rechaza_columnas_no_permitidas(valores):
    with pytest.raises(ValueError):
        vrepo_real.completar_vinculos(MagicMock(), 10, valores)


@pytest.mark.parametrize("entrada,esperado,descartados", [
    (["ADS", "CAC"], ["ADS", "CAC"], 0),
    (["ADS", None], ["ADS"], 1),
    ([None], [], 1),
    (["ADS", "", "  ", None, " CAC "], ["ADS", "CAC"], 3),
    ([], [], 0),
])
def test_cli_descarta_dominios_null_o_vacios(entrada, esperado, descartados):
    from gdd_loader.vinculos_cli import _dominios_validos
    assert _dominios_validos(entrada) == (esperado, descartados)


def test_ignorar_fecha_aplica_aunque_staging_sea_mas_viejo_y_lo_marca(entorno):
    engine, staging, repo, vrepo = entorno
    vrepo.atributo_activo.return_value = _actual(fecha=datetime.date(2026, 6, 26))
    staging.leer.return_value = [_fila(fecha=datetime.date(2025, 10, 13))]
    r = vinculos_gdd.resolver_vinculos_dominio(engine, staging, "ADS", aplicar=True, ignorar_fecha=True)
    assert r.ok and r.ignorar_fecha and r.omitidos_fecha == 0
    assert len(r.resueltos) == 1
    assert r.resueltos[0].fechas_distintas == (datetime.date(2025, 10, 13), datetime.date(2026, 6, 26))
    conn = engine.begin.return_value.__enter__.return_value
    vrepo.completar_vinculos.assert_called_once_with(
        conn, 10, {"id_atributo_primario": 777, "id_dominio_primario": 88})
    repo.registrar_bitacora.assert_called_once()


def test_ignorar_fecha_con_fechas_iguales_no_marca_nada(entorno):
    engine, staging, repo, vrepo = entorno
    r = vinculos_gdd.resolver_vinculos_dominio(engine, staging, "ADS", aplicar=False, ignorar_fecha=True)
    assert len(r.resueltos) == 1 and r.resueltos[0].fechas_distintas is None


def test_ignorar_fecha_no_relaja_las_demas_reglas(entorno):
    engine, staging, repo, vrepo = entorno
    # conflicto: sigue sin tocarse aunque se ignore la fecha
    vrepo.atributo_activo.return_value = _actual(fecha=datetime.date(2020, 1, 1), id_attr=999)
    r = vinculos_gdd.resolver_vinculos_dominio(engine, staging, "ADS", aplicar=True, ignorar_fecha=True)
    assert len(r.conflictos) == 1 and not r.resueltos
    vrepo.completar_vinculos.assert_not_called()
    # sin coincidencia: tampoco escribe
    vrepo.atributo_activo.return_value = _actual(fecha=datetime.date(2020, 1, 1))
    repo.buscar_atributo_primario.return_value = (None, None)
    r = vinculos_gdd.resolver_vinculos_dominio(engine, staging, "ADS", aplicar=True, ignorar_fecha=True)
    assert len(r.sin_coincidencia) == 1 and not r.resueltos


def test_fecha_distinta_sin_la_bandera_sigue_omitiendo(entorno):
    engine, staging, repo, vrepo = entorno
    vrepo.atributo_activo.return_value = _actual(fecha=datetime.date(2026, 6, 26))
    staging.leer.return_value = [_fila(fecha=datetime.date(2025, 10, 13))]
    r = vinculos_gdd.resolver_vinculos_dominio(engine, staging, "ADS", aplicar=True)
    assert r.omitidos_fecha == 1 and not r.resueltos and not r.ignorar_fecha


def test_cli_acepta_ignorar_fecha_y_su_abreviatura():
    from gdd_loader.vinculos_cli import _construir_parser
    assert _construir_parser().parse_args([]).ignorar_fecha is False
    assert _construir_parser().parse_args(["--ignorar-fecha"]).ignorar_fecha is True
    assert _construir_parser().parse_args(["--ignorar"]).ignorar_fecha is True
    a = _construir_parser().parse_args(["--aplicar", "--ignorar", "--dominio", "CND"])
    assert a.aplicar and a.ignorar_fecha and a.dominio == "CND"

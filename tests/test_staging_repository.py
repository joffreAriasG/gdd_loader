from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from gdd_loader.load.staging_repository import StagingRepository


def test_cargar_trunca_toda_la_tabla_con_estrategia_truncate():
    engine = MagicMock()
    conn = engine.begin.return_value.__enter__.return_value
    df = pd.DataFrame({"codigo_dominio": ["ADS", "ADS", "OTRO"]})

    with patch.object(pd.DataFrame, "to_sql") as mock_to_sql:
        repo = StagingRepository(engine)
        filas = repo.cargar(df, "staging.detalle_atributos", "codigo_dominio", "TRUNCATE")

    conn.exec_driver_sql.assert_called_once_with(
        "TRUNCATE TABLE staging.detalle_atributos"
    )
    conn.execute.assert_not_called()
    mock_to_sql.assert_called_once()
    assert mock_to_sql.call_args.kwargs["schema"] == "staging"
    assert filas == 3


# --- estrategia DOMINIO ---
#
# Bug real corregido 2026-09-17 (dos vueltas, mismo sintoma): borrar por
# igualdad exacta o por prefijo DERIVADOS de los valores presentes en el
# propio df (la hoja que se esta cargando) falla en cuanto una fila
# desaparece del archivo -- si el valor puntual ya no aparece en ninguna
# fila, o si la hoja completa queda vacia, no hay nada de donde derivar el
# dominio a borrar, y el DELETE nunca se ejecuta (fila huerfana en staging
# para siempre, el merge nunca la marca como eliminada). El fix real:
# `codigo_dominio_archivo` se pasa SIEMPRE explicito (lo determina
# pipeline/carga_staging.ejecutar, anclado en DetalleAtributos), nunca se
# deriva de `df` -- asi el borrado se ejecuta pase lo que pase con el
# contenido de la hoja puntual.


def test_cargar_con_estrategia_dominio_borra_por_igualdad_exacta():
    engine = MagicMock()
    conn = engine.begin.return_value.__enter__.return_value
    df = pd.DataFrame({"codigo_dominio": ["ADS", "ADS"]})

    with patch.object(pd.DataFrame, "to_sql") as mock_to_sql:
        repo = StagingRepository(engine)
        filas = repo.cargar(
            df, "staging.detalle_atributos", "codigo_dominio", "DOMINIO",
            codigo_dominio_archivo="ADS",
        )

    conn.exec_driver_sql.assert_not_called()
    conn.execute.assert_called_once()
    stmt, params = conn.execute.call_args.args
    assert "LIKE" not in str(stmt)
    assert params["dominio"] == "ADS"
    mock_to_sql.assert_called_once()
    assert filas == 2


def test_cargar_con_estrategia_dominio_borra_aunque_el_dataframe_este_vacio():
    """Caso limite real reportado por el usuario: vacio toda la hoja
    PlanDeRemediacion (0 filas) para probar la baja, y con el borrado
    anterior (derivado de los valores del propio df) el DELETE nunca se
    ejecutaba -- la fila vieja se quedaba activa en gdd para siempre. Con
    codigo_dominio_archivo explicito, el borrado se ejecuta igual.
    """
    engine = MagicMock()
    conn = engine.begin.return_value.__enter__.return_value
    df = pd.DataFrame({"codigo_dominio": []})

    with patch.object(pd.DataFrame, "to_sql") as mock_to_sql:
        repo = StagingRepository(engine)
        filas = repo.cargar(
            df, "staging.detalle_atributos", "codigo_dominio", "DOMINIO",
            codigo_dominio_archivo="ADS",
        )

    conn.execute.assert_called_once()
    _, params = conn.execute.call_args.args
    assert params["dominio"] == "ADS"
    mock_to_sql.assert_called_once()
    assert filas == 0


def test_cargar_falla_con_estrategia_desconocida():
    engine = MagicMock()
    df = pd.DataFrame({"codigo_dominio": ["ADS"]})
    repo = StagingRepository(engine)

    with pytest.raises(ValueError):
        repo.cargar(df, "staging.detalle_atributos", "codigo_dominio", "OTRA")


def test_cargar_con_estrategia_dominio_falla_sin_columna_clave():
    engine = MagicMock()
    df = pd.DataFrame({"codigo_dominio": ["ADS"]})
    repo = StagingRepository(engine)

    with pytest.raises(ValueError):
        repo.cargar(df, "staging.detalle_atributos", "", "DOMINIO", codigo_dominio_archivo="ADS")


def test_cargar_con_estrategia_dominio_falla_sin_codigo_dominio_archivo():
    """No se debe adivinar ni omitir el borrado en silencio -- si no se
    puede determinar el dominio del archivo, falla explicito.
    """
    engine = MagicMock()
    df = pd.DataFrame({"codigo_dominio": ["ADS"]})
    repo = StagingRepository(engine)

    with pytest.raises(ValueError, match="codigo_dominio_archivo"):
        repo.cargar(df, "staging.detalle_atributos", "codigo_dominio", "DOMINIO")


# --- columna_clave_prefijo_dominio ---
#
# MetadataTecnica/PlanDeRemediacion usan columna_clave="codigo_dominio_atributo"
# (ej. "ADS-4"), una clave mas fina que codigo_dominio -- el borrado debe
# ser por PREFIJO de dominio (LIKE 'ADS-%'), no por igualdad exacta.


def test_cargar_con_prefijo_dominio_borra_por_like_no_por_igualdad_exacta():
    engine = MagicMock()
    conn = engine.begin.return_value.__enter__.return_value
    df = pd.DataFrame({"codigo_dominio_atributo": ["ADS-1", "ADS-3"]})

    with patch.object(pd.DataFrame, "to_sql") as mock_to_sql:
        repo = StagingRepository(engine)
        filas = repo.cargar(
            df, "staging.plan_remediacion", "codigo_dominio_atributo", "DOMINIO",
            columna_clave_prefijo_dominio=True, codigo_dominio_archivo="ADS",
        )

    conn.execute.assert_called_once()
    stmt, params = conn.execute.call_args.args
    assert "LIKE" in str(stmt)
    assert params["patron"] == "ADS-%"
    mock_to_sql.assert_called_once()
    assert filas == 2


def test_cargar_con_prefijo_dominio_borra_aunque_el_dataframe_este_vacio():
    """El escenario exacto que el usuario reporto: vacia toda la hoja
    PlanDeRemediacion (0 filas) -- el DELETE por prefijo debe ejecutarse
    igual, ya que codigo_dominio_archivo no depende del contenido de df.
    """
    engine = MagicMock()
    conn = engine.begin.return_value.__enter__.return_value
    df = pd.DataFrame({"codigo_dominio_atributo": []})

    with patch.object(pd.DataFrame, "to_sql") as mock_to_sql:
        repo = StagingRepository(engine)
        filas = repo.cargar(
            df, "staging.plan_remediacion", "codigo_dominio_atributo", "DOMINIO",
            columna_clave_prefijo_dominio=True, codigo_dominio_archivo="ADS",
        )

    conn.execute.assert_called_once()
    _, params = conn.execute.call_args.args
    assert params["patron"] == "ADS-%"
    mock_to_sql.assert_called_once()
    assert filas == 0

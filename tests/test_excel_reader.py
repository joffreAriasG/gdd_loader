import datetime

import pandas as pd
import pytest

from gdd_loader.domain.sheet_config import SheetConfig
from gdd_loader.extract.excel_reader import ColumnasFaltantesError, leer_hoja


def test_lee_columnas_esperadas_y_castea_tipos(excel_de_prueba, cfg_detalle_atributos):
    df = leer_hoja(excel_de_prueba, cfg_detalle_atributos)

    assert list(df.columns) == cfg_detalle_atributos.columnas
    assert len(df) == 2
    assert df["codigo_atributo"].tolist() == [1, 2]  # casteado a Int64


def test_falla_si_falta_una_columna_esperada(excel_de_prueba, cfg_detalle_atributos):
    cfg_con_columna_extra = SheetConfig(
        nombre_hoja=cfg_detalle_atributos.nombre_hoja,
        tabla_staging=cfg_detalle_atributos.tabla_staging,
        columnas=cfg_detalle_atributos.columnas + ["columna_que_no_existe"],
    )

    with pytest.raises(ColumnasFaltantesError):
        leer_hoja(excel_de_prueba, cfg_con_columna_extra)


def test_falla_si_el_archivo_no_existe(tmp_path, cfg_detalle_atributos):
    with pytest.raises(FileNotFoundError):
        leer_hoja(tmp_path / "no_existe.xlsx", cfg_detalle_atributos)


def _cfg_con_fecha(nombre_hoja: str) -> SheetConfig:
    return SheetConfig(
        nombre_hoja=nombre_hoja,
        tabla_staging="staging.detalle_atributos",
        columnas=["codigo_dominio", "fecha_aprobada"],
        columnas_fecha={"fecha_aprobada": "%d-%m-%Y"},
    )


def test_parsea_fecha_con_formato_dd_mm_yyyy_explicito(tmp_path):
    cfg = _cfg_con_fecha("DetalleAtributos")
    archivo = tmp_path / "con_fecha.xlsx"
    df = pd.DataFrame(
        {
            # 05-06-2026 es ambiguo (dd-mm vs mm-dd) si no se usa un formato
            # explicito — confirma que se interpreta como 5 de junio, no
            # como 6 de mayo.
            "codigo_dominio": ["ADS", "ADS"],
            "fecha_aprobada": ["31-01-2025", "05-06-2026"],
        }
    )
    with pd.ExcelWriter(archivo, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name=cfg.nombre_hoja, index=False)

    resultado = leer_hoja(archivo, cfg)

    assert resultado["fecha_aprobada"].tolist() == [
        datetime.date(2025, 1, 31),
        datetime.date(2026, 6, 5),
    ]


def test_falla_con_mensaje_claro_si_la_fecha_no_calza_con_el_formato(tmp_path):
    cfg = _cfg_con_fecha("DetalleAtributos")
    archivo = tmp_path / "fecha_invalida.xlsx"
    df = pd.DataFrame({"codigo_dominio": ["ADS"], "fecha_aprobada": ["2026-01-31"]})
    with pd.ExcelWriter(archivo, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name=cfg.nombre_hoja, index=False)

    with pytest.raises(ValueError, match="fecha_aprobada"):
        leer_hoja(archivo, cfg)


def test_fecha_acepta_celda_de_fecha_real_de_excel_mezclada_con_texto(tmp_path):
    """Regresion del bug real reportado 2026-09-21 en PlanDeRemediacion.
    fecha_finalizacion_definitiva: la columna esta configurada como TEXTO
    ("%m/%d/%Y"), pero al menos una fila trae la celda como fecha real de
    Excel -- con dtype=str eso llega como texto ISO con hora
    ("2028-09-22 00:00:00"), que no calza con el formato configurado. Debe
    aceptarse via el formato de respaldo, igual que ya se resolvio antes
    para metadata_tecnica.fecha_aprobada.
    """
    cfg = SheetConfig(
        nombre_hoja="PlanDeRemediacion",
        tabla_staging="staging.plan_remediacion",
        columnas=["codigo_dominio_atributo", "fecha_finalizacion_definitiva"],
        columnas_fecha={"fecha_finalizacion_definitiva": "%m/%d/%Y"},
    )
    archivo = tmp_path / "fecha_mixta.xlsx"
    with pd.ExcelWriter(archivo, engine="openpyxl") as writer:
        pd.DataFrame(
            {
                "codigo_dominio_atributo": ["ADS-1"],
                "fecha_finalizacion_definitiva": ["09/22/2028"],
            }
        ).to_excel(writer, sheet_name=cfg.nombre_hoja, index=False)
        hoja = writer.sheets[cfg.nombre_hoja]
        # Fila 2 (1-indexed + header): celda de fecha REAL de Excel, no texto.
        hoja["B3"] = datetime.datetime(2028, 9, 22)
        hoja["A3"] = "ADS-2"

    resultado = leer_hoja(archivo, cfg)

    assert resultado["fecha_finalizacion_definitiva"].tolist() == [
        datetime.date(2028, 9, 22),
        datetime.date(2028, 9, 22),
    ]


def test_fecha_sigue_fallando_con_mensaje_claro_si_ningun_formato_calza(tmp_path):
    """El formato de respaldo (fecha real de Excel) no debe volver
    permisivo el parseo -- un valor genuinamente invalido sigue abortando
    con error claro."""
    cfg = SheetConfig(
        nombre_hoja="PlanDeRemediacion",
        tabla_staging="staging.plan_remediacion",
        columnas=["codigo_dominio_atributo", "fecha_finalizacion_definitiva"],
        columnas_fecha={"fecha_finalizacion_definitiva": "%m/%d/%Y"},
    )
    archivo = tmp_path / "fecha_mala.xlsx"
    df = pd.DataFrame(
        {
            "codigo_dominio_atributo": ["ADS-1"],
            "fecha_finalizacion_definitiva": ["no es una fecha"],
        }
    )
    with pd.ExcelWriter(archivo, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name=cfg.nombre_hoja, index=False)

    with pytest.raises(ValueError, match="fecha_finalizacion_definitiva"):
        leer_hoja(archivo, cfg)


def _excel_fechas(tmp_path, nombre_hoja, valores):
    archivo = tmp_path / "fechas.xlsx"
    pd.DataFrame({"codigo_dominio": ["CLI"] * len(valores), "fecha_aprobada": valores}).to_excel(
        archivo, sheet_name=nombre_hoja, index=False)
    return archivo


def test_fecha_real_excel_acepta_texto_dd_mm_aaaa(tmp_path):
    """2026-10-01 (CLI.xlsx, Estructura): mezcla de fecha real de Excel y
    texto "03/08/2026" -> 3 de agosto (dd/mm/aaaa)."""
    import datetime
    cfg = SheetConfig(nombre_hoja="Estructura", tabla_staging="staging.estructura",
                      columnas=["codigo_dominio", "fecha_aprobada"],
                      columnas_fecha={"fecha_aprobada": "%Y-%m-%d %H:%M:%S"})
    archivo = _excel_fechas(tmp_path, "Estructura",
                            [datetime.datetime(2023, 5, 30), "03/08/2026", " 14/07/2026 "])
    resultado = leer_hoja(archivo, cfg)
    assert resultado["fecha_aprobada"].tolist() == [
        datetime.date(2023, 5, 30), datetime.date(2026, 8, 3), datetime.date(2026, 7, 14)]


def test_formato_mes_primero_no_usa_respaldo_dd_mm(tmp_path):
    """Columnas con formato declarado distinto (ej. "%m/%d/%Y") no se
    interpretan con dia primero: "14/07/2026" debe fallar, no convertirse."""
    cfg = SheetConfig(nombre_hoja="PlanDeRemediacion", tabla_staging="staging.plan_remediacion",
                      columnas=["codigo_dominio", "fecha_aprobada"],
                      columnas_fecha={"fecha_aprobada": "%m/%d/%Y"})
    archivo = _excel_fechas(tmp_path, "PlanDeRemediacion", ["07/14/2026", "14/07/2026"])
    with pytest.raises(ValueError, match="14/07/2026"):
        leer_hoja(archivo, cfg)


def test_plan_remediacion_real_acepta_dd_mm_aaaa_en_todas_sus_fechas(tmp_path):
    """2026-10-05: con la config REAL (HOJAS_ADS), PlanDeRemediacion acepta
    texto DD/MM/AAAA (normalizado por el Office Script 3) y fecha real de
    Excel en sus 3 columnas de fecha. "30/01/2026" -> 30 de enero."""
    from gdd_loader.domain.sheet_config import HOJAS_ADS
    cfg = next(c for c in HOJAS_ADS if c.nombre_hoja == "PlanDeRemediacion")
    fila = {c: "-" for c in cfg.columnas}
    fila.update(codigo_dominio_atributo="ADS-1", avance="10",
                fecha_identificacion="15/09/2025",
                fecha_finalizacion_definitiva="30/01/2026",
                fecha_ultima_modificacion=datetime.datetime(2026, 3, 15))
    archivo = tmp_path / "plan.xlsx"
    pd.DataFrame([fila]).to_excel(archivo, sheet_name=cfg.nombre_hoja, index=False)
    [r] = leer_hoja(archivo, cfg).to_dict("records")
    assert r["fecha_identificacion"] == datetime.date(2025, 9, 15)
    assert r["fecha_finalizacion_definitiva"] == datetime.date(2026, 1, 30)
    assert r["fecha_ultima_modificacion"] == datetime.date(2026, 3, 15)


# --- MetadataRepositorio (plantilla 2.0.0, 2026-10-05) ---------------------

def _cfg_repositorio() -> SheetConfig:
    from gdd_loader.domain.sheet_config import HOJAS_ADS
    return next(c for c in HOJAS_ADS if c.nombre_hoja == "MetadataRepositorio")


def _fila_repo(**overrides) -> dict:
    base = {
        "codigo_dominio_atributo": "AES-1",
        "clase": "Registro_alianzas_establecimientos",
        "coleccion_foc": "gob_reg_alianzas_establecimientos",
        "servidor_foc": "Stratio",
        "tabla_bv_foc": "ZP_BP_Mdp_Tc_TN_EstablecimientosConsumos",
        "nombre_campo_foc": "codigoUnicoEstablecimiento",
        "fecha_aprobada": "21/08/2026",
    }
    base.update(overrides)
    return base


def _excel_repo(tmp_path, filas, columnas=None) -> "Path":
    archivo = tmp_path / "repo.xlsx"
    df = pd.DataFrame(filas, columns=columnas or list(_fila_repo().keys()))
    with pd.ExcelWriter(archivo, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="MetadataRepositorio", index=False)
    return archivo


def test_config_real_metadata_tecnica_ya_no_trae_clase_ni_foc():
    from gdd_loader.domain.sheet_config import HOJAS_ADS
    cfg = next(c for c in HOJAS_ADS if c.nombre_hoja == "MetadataTecnica")
    assert "clase" not in cfg.columnas
    assert not [c for c in cfg.columnas if c.endswith("_foc")]
    assert len(cfg.columnas) == 11


def test_metadata_repositorio_lee_todas_las_columnas_y_parsea_fecha(tmp_path):
    archivo = _excel_repo(tmp_path, [_fila_repo(), _fila_repo(fecha_aprobada="18/06/2025",
                                                              nombre_campo_foc="marca")])
    df = leer_hoja(archivo, _cfg_repositorio())
    assert list(df.columns) == _cfg_repositorio().columnas
    assert df["fecha_aprobada"].tolist() == [datetime.date(2026, 8, 21), datetime.date(2025, 6, 18)]


@pytest.mark.parametrize("valor", [None, "-", "   "])
@pytest.mark.parametrize("columna", list(_fila_repo().keys()))
def test_metadata_repositorio_rechaza_vacio_o_guion_en_cualquier_columna(tmp_path, columna, valor):
    from gdd_loader.extract.excel_reader import ValoresObligatoriosError
    archivo = _excel_repo(tmp_path, [_fila_repo(), _fila_repo(**{columna: valor})])
    with pytest.raises(ValoresObligatoriosError, match=rf"fila 3: {columna}"):
        leer_hoja(archivo, _cfg_repositorio())


def test_metadata_repositorio_fila_toda_guion_es_error(tmp_path):
    from gdd_loader.extract.excel_reader import ValoresObligatoriosError
    archivo = _excel_repo(tmp_path, [{k: "-" for k in _fila_repo()}])
    with pytest.raises(ValoresObligatoriosError, match="fila 2"):
        leer_hoja(archivo, _cfg_repositorio())


def test_metadata_repositorio_sin_filas_es_valida(tmp_path):
    archivo = _excel_repo(tmp_path, [])
    df = leer_hoja(archivo, _cfg_repositorio())
    assert df.empty


def test_hojas_sin_columnas_obligatorias_no_cambian(tmp_path):
    """Las demas hojas siguen aceptando "-" y vacios (sin columnas_obligatorias)."""
    from gdd_loader.domain.sheet_config import HOJAS_ADS
    assert [c.nombre_hoja for c in HOJAS_ADS if c.columnas_obligatorias] == ["MetadataRepositorio"]

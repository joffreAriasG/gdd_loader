import pandas as pd
import pytest

from gdd_loader.domain.sheet_config import SheetConfig


@pytest.fixture
def cfg_detalle_atributos() -> SheetConfig:
    return SheetConfig(
        nombre_hoja="Detalle Atributos",
        tabla_staging="staging.detalle_atributos",
        columnas=["codigo_dominio", "codigo_atributo", "atributo"],
        tipos={"codigo_atributo": "Int64"},
        columna_clave="codigo_dominio",
    )


@pytest.fixture
def excel_de_prueba(tmp_path, cfg_detalle_atributos):
    """Crea un .xlsx sintetico (sin datos reales) con la hoja esperada."""
    archivo = tmp_path / "plantilla_prueba.xlsx"
    df = pd.DataFrame(
        {
            "codigo_dominio": ["ADS", "ADS"],
            "codigo_atributo": ["1", "2"],
            "atributo": ["Atributo de prueba 1", "Atributo de prueba 2"],
        }
    )
    with pd.ExcelWriter(archivo, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name=cfg_detalle_atributos.nombre_hoja, index=False)
    return archivo

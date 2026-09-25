"""
Capa de extraccion: lee una hoja de un Excel y la deja como DataFrame
ya validado contra las columnas esperadas. No sabe nada de SQL Server.

Si en el futuro se reemplaza "carpeta sincronizada" por "Microsoft
Graph API", este es el unico modulo que cambia — el resto del
pipeline sigue recibiendo un DataFrame igual que ahora.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from gdd_loader.domain.sheet_config import SheetConfig


class ColumnasFaltantesError(Exception):
    """La hoja no tiene las columnas que el dominio espera."""


def leer_hoja(archivo: Path, cfg: SheetConfig) -> pd.DataFrame:
    if not archivo.exists():
        raise FileNotFoundError(f"No se encontro el archivo: {archivo}")

    df = pd.read_excel(archivo, sheet_name=cfg.nombre_hoja, dtype=str)
    df.columns = [str(c).strip() for c in df.columns]

    faltantes = [c for c in cfg.columnas if c not in df.columns]
    if faltantes:
        raise ColumnasFaltantesError(
            f"Hoja '{cfg.nombre_hoja}': faltan columnas esperadas {faltantes}. "
            "No se procesa hasta confirmar el cambio de estructura de la plantilla."
        )

    df = df[cfg.columnas].copy()
    df = df.dropna(how="all")  # filas completamente vacias al final de la hoja

    for columna, tipo in cfg.tipos.items():
        try:
            df[columna] = df[columna].astype(tipo)
        except (ValueError, TypeError) as exc:
            raise ValueError(
                f"Hoja '{cfg.nombre_hoja}', columna '{columna}': no se pudo convertir a {tipo}. "
                f"Detalle: {exc}"
            ) from exc

    # Formato de respaldo para celdas de FECHA REAL de Excel dentro de una
    # columna configurada como TEXTO (ej. PlanDeRemediacion.fecha_finaliza
    # cion_definitiva, documentada como texto "%m/%d/%Y" -- pero detectado
    # 2026-09-21: al menos una fila trae la celda como fecha real de Excel,
    # que con dtype=str llega como "2028-09-22 00:00:00", no como
    # "09/22/2028"). Mismo patron ya visto y resuelto en
    # metadata_tecnica.fecha_aprobada (gdd_mapping._parsear_fecha_aprobada_
    # metadata) -- aqui se generaliza para cualquier columna de
    # columnas_fecha, sin relajar la seguridad de usar format= explicito
    # (no se prueba con el parser automatico de pandas, que puede
    # interpretar mal fechas ambiguas dd-mm-yyyy vs mm-dd-yyyy).
    _FORMATO_FECHA_REAL_EXCEL = "%Y-%m-%d %H:%M:%S"

    for columna, formato in cfg.columnas_fecha.items():
        parsed = pd.to_datetime(df[columna], format=formato, errors="coerce")
        sin_parsear = parsed.isna() & df[columna].notna()

        if sin_parsear.any() and formato != _FORMATO_FECHA_REAL_EXCEL:
            reintento = pd.to_datetime(
                df.loc[sin_parsear, columna], format=_FORMATO_FECHA_REAL_EXCEL, errors="coerce"
            )
            parsed.loc[sin_parsear] = reintento
            sin_parsear = parsed.isna() & df[columna].notna()

        if sin_parsear.any():
            valores_malos = sorted(df.loc[sin_parsear, columna].unique().tolist())
            raise ValueError(
                f"Hoja '{cfg.nombre_hoja}', columna '{columna}': no se pudo parsear como fecha "
                f"con formato '{formato}' ni con el formato de fecha real de Excel "
                f"'{_FORMATO_FECHA_REAL_EXCEL}'. Valores sin parsear: {valores_malos}"
            )

        df[columna] = parsed.dt.date

    return df

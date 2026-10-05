"""
Diagnostico de carga a staging (SOLO LECTURA: no escribe en la base ni mueve
archivos).

Lee un Excel con las mismas reglas que gdd_loader (leer_hoja) y, por cada hoja,
compara el largo maximo de cada columna contra el ancho real de la columna en
la tabla staging (INFORMATION_SCHEMA). Reporta:
  - columnas cuyo valor mas largo no cabe en staging (causa del error
    "String or binary data would be truncated" / 22001), con la fila de Excel
    y el inicio del valor;
  - columnas que el loader envia pero no existen en la tabla staging;
  - hojas que no se pueden leer.

Uso (desde la carpeta del proyecto, con el entorno activado):
    python scripts/diagnostico_staging.py "C:\\ruta\\archivo.xlsx"
"""

from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import text

from gdd_loader.config import cargar_settings
from gdd_loader.domain.sheet_config import HOJAS_ADS
from gdd_loader.extract.excel_reader import leer_hoja
from gdd_loader.load.db import construir_engine


def anchos_staging(conn, tabla_staging: str) -> dict[str, int]:
    """columna -> largo maximo (-1 = varchar(max)); solo columnas de texto."""
    esquema, nombre = tabla_staging.split(".", 1)
    filas = conn.execute(text(
        "SELECT COLUMN_NAME, CHARACTER_MAXIMUM_LENGTH FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA = :esq AND TABLE_NAME = :tab"), {"esq": esquema, "tab": nombre}).all()
    return {f[0].lower(): (f[1] if f[1] is not None else 0) for f in filas}


def revisar(df, anchos: dict[str, int]) -> list[str]:
    """Funcion pura: hallazgos de una hoja ya leida contra los anchos de staging."""
    hallazgos = []
    for col in df.columns:
        ancho = anchos.get(str(col).lower())
        if ancho is None:
            hallazgos.append(f"  - columna '{col}': NO EXISTE en la tabla staging")
            continue
        if ancho <= 0:          # -1 = (max) o columna no textual
            continue
        largos = df[col].dropna().astype(str).str.len()
        if largos.empty or largos.max() <= ancho:
            continue
        exceden = largos[largos > ancho]
        primera = exceden.index[0]
        valor = str(df.at[primera, col])
        hallazgos.append(
            f"  - columna '{col}': staging admite {ancho}, el Excel trae hasta {int(largos.max())} "
            f"({len(exceden)} fila(s)). Primera: fila Excel {primera + 2}, valor '{valor[:60]}...'")
    return hallazgos


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    archivo = Path(sys.argv[1])
    if not archivo.exists():
        print(f"No existe: {archivo}")
        return 2
    settings = cargar_settings()
    engine = construir_engine(settings)
    total = 0
    with engine.connect() as conn:
        for cfg in HOJAS_ADS:
            try:
                df = leer_hoja(archivo, cfg)
            except Exception as exc:  # noqa: BLE001
                print(f"[{cfg.nombre_hoja}] NO SE PUDO LEER: {exc}")
                total += 1
                continue
            anchos = anchos_staging(conn, cfg.tabla_staging)
            if not anchos:
                print(f"[{cfg.nombre_hoja}] la tabla {cfg.tabla_staging} no existe")
                total += 1
                continue
            hallazgos = revisar(df, anchos)
            estado = "OK" if not hallazgos else f"{len(hallazgos)} problema(s)"
            print(f"[{cfg.nombre_hoja}] -> {cfg.tabla_staging}: {len(df)} filas, {estado}")
            for h in hallazgos:
                print(h)
            total += len(hallazgos)
    print("\nSin problemas de ancho detectados." if total == 0 else f"\nTotal: {total} hallazgo(s).")
    return 0 if total == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

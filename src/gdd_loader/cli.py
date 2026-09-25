"""
Punto de entrada. Uso:

    python -m gdd_loader.cli --archivo AdministracionDeSeguros.xlsx
    python -m gdd_loader.cli
        (sin --archivo: procesa TODOS los .xlsx de GDD_CARPETA_ORIGEN, uno
        por dominio — es el modo normal de operacion con multiples dominios)

Junta config + logging + las demas capas y define el codigo de salida
(0 si todo cargo bien, 1 si alguna hoja o archivo fallo) — para que Task
Scheduler pueda detectar un fallo real.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from gdd_loader.config import Settings, cargar_settings
from gdd_loader.domain.sheet_config import HOJAS_ADS
from gdd_loader.load.db import construir_engine
from gdd_loader.load.staging_repository import StagingRepository
from gdd_loader.logging_setup import configurar_logging
from gdd_loader.pipeline.carga_staging import ResultadoHoja, ejecutar


def _listar_archivos(carpeta: Path) -> list[Path]:
    """Todos los .xlsx de la carpeta origen, en orden, excluyendo los
    archivos de bloqueo que Excel crea mientras un archivo esta abierto
    (~$archivo.xlsx) — si no se filtran, el CLI intenta leerlos y falla.
    """
    return sorted(
        p for p in carpeta.glob("*.xlsx") if not p.name.startswith("~$")
    )


def _resolver_archivos(args: argparse.Namespace, settings: Settings) -> list[Path]:
    if args.archivo:
        return [settings.carpeta_origen / args.archivo]
    return _listar_archivos(settings.carpeta_origen)


def main() -> int:
    parser = argparse.ArgumentParser(description="Carga plantillas de gobierno de datos a staging")
    parser.add_argument(
        "--archivo",
        default=None,
        help=(
            "Nombre de un archivo puntual dentro de la carpeta origen "
            "(para pruebas manuales). Si se omite, se procesan todos los "
            ".xlsx de GDD_CARPETA_ORIGEN."
        ),
    )
    parser.add_argument("--env-file", default=None, help="Ruta a un .env especifico (opcional)")
    args = parser.parse_args()

    settings = cargar_settings(args.env_file)
    logger = configurar_logging(settings.log_dir, settings.log_level)

    try:
        settings.validar()
    except ValueError as exc:
        logger.error(str(exc))
        return 1

    archivos = _resolver_archivos(args, settings)
    if not archivos:
        logger.error("No se encontraron archivos .xlsx en %s", settings.carpeta_origen)
        return 1

    logger.info("Se procesaran %d archivo(s) desde %s", len(archivos), settings.carpeta_origen)

    engine = construir_engine(settings)
    repo = StagingRepository(engine)

    resultados: list[ResultadoHoja] = []
    for archivo in archivos:
        logger.info("Iniciando carga: %s", archivo)
        resultados.extend(ejecutar(archivo, HOJAS_ADS, repo, settings.estrategia_staging))

    hubo_error = any(r.error for r in resultados)
    for r in resultados:
        estado = f"ERROR: {r.error}" if r.error else f"{r.filas_cargadas} filas"
        logger.info("Resumen -> %s | %s (%s): %s", r.archivo, r.hoja, r.tabla, estado)

    archivos_con_error = {r.archivo for r in resultados if r.error}
    logger.info(
        "Carga finalizada: %d archivo(s) procesados, %d con al menos un error",
        len(archivos), len(archivos_con_error),
    )

    return 1 if hubo_error else 0


if __name__ == "__main__":
    sys.exit(main())

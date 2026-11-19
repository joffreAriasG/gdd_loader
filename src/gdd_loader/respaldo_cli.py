"""
Respaldo logico de DGODELIVERY (esquemas gdd y staging) a un zip de CSV. GDD-04.

    python -m gdd_loader.respaldo_cli crear
    python -m gdd_loader.respaldo_cli crear --destino "C:\\ruta\\respaldos" --esquemas gdd staging
    python -m gdd_loader.respaldo_cli verificar "C:\\ruta\\respaldos\\respaldo_DGODELIVERY_20261119_103000.zip"

SOLO LECTURA: no necesita permiso de BACKUP, solo SELECT. No reemplaza el
backup del DBA (no guarda indices, permisos ni otros esquemas); sirve para
recuperar datos de gdd/staging despues de un error de carga o de migracion.

Destino: --destino, o GDD_CARPETA_RESPALDOS del .env. Usar una carpeta FUERA
del servidor de base de datos. Correrlo cuando no haya una carga en curso
(si una tabla cambia mientras se lee, `verificar` lo avisa). Nunca borra
respaldos anteriores.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from gdd_loader import __version__
from gdd_loader.config import cargar_settings
from gdd_loader.load.db import construir_engine
from gdd_loader.logging_setup import configurar_logging
from gdd_loader.pipeline.respaldo import ESQUEMAS_POR_DEFECTO, respaldar, verificar_respaldo


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Respaldo de gdd/staging a un zip de CSV (solo lectura)")
    parser.add_argument("--env-file", default=None)
    sub = parser.add_subparsers(dest="accion", required=True)
    crear = sub.add_parser("crear", help="Crea un respaldo nuevo")
    crear.add_argument("--destino", type=Path, default=None,
                       help="Carpeta de respaldos (por defecto GDD_CARPETA_RESPALDOS)")
    crear.add_argument("--esquemas", nargs="+", default=list(ESQUEMAS_POR_DEFECTO))
    verificar = sub.add_parser("verificar", help="Comprueba un respaldo existente")
    verificar.add_argument("zip", type=Path)
    args = parser.parse_args(argv)

    settings = cargar_settings(args.env_file)
    logger = configurar_logging(settings.log_dir, settings.log_level)

    if args.accion == "verificar":
        if not args.zip.is_file():
            logger.error(f"No existe {args.zip}")
            return 1
        problemas = verificar_respaldo(args.zip)
        for p in problemas:
            logger.error(p)
        if problemas:
            logger.error(f"Respaldo con {len(problemas)} problema(s): {args.zip}")
            return 1
        logger.info(f"Respaldo integro: {args.zip}")
        return 0

    destino = args.destino or (Path(v) if (v := os.environ.get("GDD_CARPETA_RESPALDOS", "").strip()) else None)
    if destino is None:
        logger.error("Indique --destino o configure GDD_CARPETA_RESPALDOS en el .env")
        return 1
    try:
        settings.validar()
    except ValueError as exc:
        logger.error(str(exc))
        return 1

    engine = construir_engine(settings)
    try:
        ruta, manifiesto = respaldar(engine, destino, version_loader=__version__,
                                     servidor=settings.sql_servidor, base_datos=settings.sql_base_datos,
                                     esquemas=args.esquemas)
    except (ValueError, FileExistsError, OSError) as exc:
        logger.error(f"No se pudo crear el respaldo: {exc}")
        return 1

    for t in manifiesto.tablas:
        logger.debug(f"  {t.esquema}.{t.tabla}: {t.filas} filas")
    problemas = verificar_respaldo(ruta)
    for p in problemas:
        logger.warning(p)
    logger.info(f"Respaldo: {ruta} | {len(manifiesto.tablas)} tablas, {manifiesto.total_filas} filas, "
                 f"{ruta.stat().st_size / 1048576:.1f} MB | "
                 + ("verificado" if not problemas else f"{len(problemas)} problema(s), revisar"))
    return 0 if not problemas else 2


if __name__ == "__main__":
    sys.exit(main())

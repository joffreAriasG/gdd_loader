"""
Punto de entrada de la operacion normal (Fase 1): procesa los archivos de la
carpeta de Entrada de punta a punta, un id_carga por archivo.

    python -m gdd_loader.procesar_cli
    python -m gdd_loader.procesar_cli --archivo "nombre cualquiera.xlsx"
    python -m gdd_loader.procesar_cli --sin-purga

Por cada archivo estable (sin cambios en los ultimos GDD_SEGUNDOS_ESTABILIDAD):
valida plantilla (hoja _Control + huella de estructura contra
gdd.plantilla_version) -> carga staging -> merge gdd -> mueve el archivo a
Archivo o Rechazados. Al final purga los archivos con mas de
GDD_DIAS_RETENCION dias.

Reemplaza la secuencia manual `cli` + `merge_cli`, que siguen disponibles
para diagnostico o reprocesos puntuales.

Codigo de salida: 0 si todos los archivos terminaron en MERGE_OK u
OMITIDA_SIN_CAMBIOS; 1 si alguno fue rechazado o fallo (para Task Scheduler).
"""

from __future__ import annotations

import argparse
import getpass
import socket
import sys

from gdd_loader import __version__
from gdd_loader.config import cargar_settings
from gdd_loader.domain.sheet_config import HOJAS_ADS
from gdd_loader.load.control_repository import ControlRepository
from gdd_loader.load.db import construir_engine
from gdd_loader.load.staging_repository import StagingRepository
from gdd_loader.logging_setup import configurar_logging
from gdd_loader.pipeline.archivos import archivo_estable, listar_pendientes
from gdd_loader.notificacion.evento import Notificador
from gdd_loader.pipeline.merge_completo import mergear_dominio_completo
from gdd_loader.pipeline.proceso_carga import ContextoCarga, procesar_archivo
from gdd_loader.pipeline.purga import purgar


def main() -> int:
    parser = argparse.ArgumentParser(description="Procesa las plantillas de la carpeta de entrada (staging + merge)")
    parser.add_argument("--archivo", default=None,
                        help="Procesar solo este archivo de la carpeta de entrada")
    parser.add_argument("--sin-purga", action="store_true",
                        help="No ejecutar la purga de archivos vencidos al final")
    parser.add_argument("--env-file", default=None, help="Ruta a un .env especifico (opcional)")
    args = parser.parse_args()

    settings = cargar_settings(args.env_file)
    logger = configurar_logging(settings.log_dir, settings.log_level)
    try:
        settings.validar()
        settings.validar_procesamiento()
    except ValueError as exc:
        logger.error(str(exc))
        return 1

    engine = construir_engine(settings)
    control_repo = ControlRepository(engine)
    ctx = ContextoCarga(
        control_repo=control_repo,
        staging_repo=StagingRepository(engine),
        engine=engine,
        hojas=HOJAS_ADS,
        carpeta_archivo=settings.carpeta_archivo,
        carpeta_rechazados=settings.carpeta_rechazados,
        id_plantilla=settings.id_plantilla,
        exigir_control=settings.exigir_control,
        version_loader=__version__,
        equipo=socket.gethostname(),
        usuario_ejecucion=getpass.getuser(),
        funcion_merge=mergear_dominio_completo,
        estrategia_staging=settings.estrategia_staging,
        notificador=(
            Notificador(engine, settings.carpeta_notificaciones, settings.notif_id_rol,
                        settings.notif_ambiente, __version__, settings.notif_max_filas)
            if settings.carpeta_notificaciones is not None else None
        ),
    )
    if settings.carpeta_notificaciones is None:
        logger.info("Notificaciones desactivadas (GDD_CARPETA_NOTIFICACIONES vacia)")
    else:
        logger.info("Notificaciones -> %s | ambiente %s | responsables con id_rol=%s",
                    settings.carpeta_notificaciones, settings.notif_ambiente, settings.notif_id_rol)
    logger.info("gdd_loader %s | exigir _Control=%s | entrada=%s",
                __version__, "SI" if settings.exigir_control else "NO (transicion)",
                settings.carpeta_origen)

    if args.archivo:
        candidatos = [settings.carpeta_origen / args.archivo]
    else:
        candidatos = listar_pendientes(settings.carpeta_origen)

    resultados = []
    for archivo in candidatos:
        if not archivo.exists():
            logger.error("No existe: %s", archivo)
            resultados.append(None)
            continue
        if not archivo_estable(archivo, settings.segundos_estabilidad):
            logger.info("Se omite por ahora (modificado hace menos de %ds o vacio): %s",
                        settings.segundos_estabilidad, archivo.name)
            continue
        resultados.append(procesar_archivo(archivo, ctx))

    if not candidatos:
        logger.info("No hay archivos pendientes en %s", settings.carpeta_origen)

    for r in resultados:
        if r is not None:
            logger.info("Resumen -> carga %s | %s | %s | %s", r.id_carga, r.archivo_original,
                        r.codigo_dominio or "-", r.estado)

    if not args.sin_purga:
        purgar(control_repo, settings.dias_retencion,
               carpetas_permitidas=(settings.carpeta_archivo, settings.carpeta_rechazados))

    return 0 if all(r is not None and r.exitosa for r in resultados) else 1


if __name__ == "__main__":
    sys.exit(main())

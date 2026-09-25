"""
Genera la plantilla precargada de uno o varios dominios (Fase 2).

    python -m gdd_loader.generar_cli --dominio CAC
    python -m gdd_loader.generar_cli --dominio CAC ADS

Toma el molde VIGENTE de GDD_CARPETA_MOLDES, lo llena con la ultima carga
exitosa del dominio y deja el resultado en GDD_CARPETA_PLANTILLAS como
`{dominio}_v{version}_c{id_carga_base}.xlsx`. La persona responsable del
dominio descarga ese archivo, hace sus cambios y lo sube a la carpeta de
entrada. Si antes de subirlo alguien carga el dominio de nuevo, la carga se
rechaza (RECHAZADA_DESACTUALIZADA) y hay que volver a generar.

Codigo de salida: 0 si se generaron todas, 1 si alguna fallo.
"""

from __future__ import annotations

import argparse
import sys

from gdd_loader.config import cargar_settings
from gdd_loader.domain.sheet_config import HOJAS_ADS
from gdd_loader.exportar.listas_referencia import ListasRepository
from gdd_loader.exportar.generador import ContextoGenerador, GeneracionError, generar_plantilla
from gdd_loader.load.control_repository import ControlRepository
from gdd_loader.load.db import construir_engine
from gdd_loader.load.staging_repository import StagingRepository
from gdd_loader.logging_setup import configurar_logging


def main() -> int:
    parser = argparse.ArgumentParser(description="Genera plantillas precargadas por dominio")
    parser.add_argument("--dominio", nargs="+", required=True, help="Codigo(s) de dominio, ej. CAC ADS")
    parser.add_argument("--env-file", default=None)
    args = parser.parse_args()

    settings = cargar_settings(args.env_file)
    logger = configurar_logging(settings.log_dir, settings.log_level)
    try:
        settings.validar()
        settings.validar_generacion()
    except ValueError as exc:
        logger.error(str(exc))
        return 1

    engine = construir_engine(settings)
    ctx = ContextoGenerador(
        control_repo=ControlRepository(engine),
        staging_repo=StagingRepository(engine),
        hojas=HOJAS_ADS,
        id_plantilla=settings.id_plantilla,
        carpeta_moldes=settings.carpeta_moldes,
        carpeta_salida=settings.carpeta_plantillas,
        modo_listas=settings.listas_referencia,
        listas_repo=ListasRepository(engine),
    )
    logger.info("Listas de referencia: %s", "desde la BD" if settings.listas_referencia == "BD" else "del molde")

    errores = 0
    for dominio in args.dominio:
        dominio = dominio.strip().upper()
        try:
            r = generar_plantilla(dominio, ctx)
        except GeneracionError as exc:
            errores += 1
            logger.error("%s -> no generada: %s", dominio, exc)
            continue
        logger.info("%s -> %s (version %s, base carga %d, fuente %s) filas: %s",
                    dominio, r.ruta, r.version_plantilla, r.id_carga_base, r.fuente,
                    ", ".join(f"{h} {n}" for h, n in r.filas_por_hoja.items() if h != "Reporte_Errores"))
        for a in r.advertencias[:20]:
            logger.warning("%s -> %s", dominio, a)
        if len(r.advertencias) > 20:
            logger.warning("%s -> ... y %d advertencias mas", dominio, len(r.advertencias) - 20)
    return 1 if errores else 0


if __name__ == "__main__":
    sys.exit(main())

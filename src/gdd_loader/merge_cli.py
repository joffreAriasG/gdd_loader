"""
Punto de entrada del merge staging -> gdd. Es un paso SEPARADO de la carga
Excel -> staging (gdd_loader.cli): primero se cargan uno o varios archivos
a staging, se revisan los resultados, y luego -- cuando se confirma que
staging quedo bien -- se corre este merge para pasar los datos al esquema
`gdd`. No se encadenan automaticamente: mergear hacia gdd toca tablas con
relaciones (FKs, catalogos) que conviene revisar antes de escribir.

Uso:

    python -m gdd_loader.merge_cli --dominio ADS
    python -m gdd_loader.merge_cli
        (sin --dominio: mergea TODOS los codigo_dominio presentes hoy en
        staging.detalle_atributos)

Codigo de salida: 0 si todos los dominios mergearon sin error, 1 si alguno
fallo (se revierte solo ese dominio, no afecta a los demas).
"""

from __future__ import annotations

import argparse
import sys

from gdd_loader.config import cargar_settings
from gdd_loader.load.db import construir_engine
from gdd_loader.load.staging_repository import StagingRepository
from gdd_loader.logging_setup import configurar_logging
from gdd_loader.pipeline.carga_gdd import (
    ejecutar_merge_dominio,
    ejecutar_merge_estructura_dominio,
    ejecutar_merge_investigacion_dominio,
    ejecutar_merge_plan_remediacion_dominio,
    ejecutar_merge_respaldos_dominio,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Mergea staging.detalle_atributos / staging.metadata_tecnica / "
            "staging.investigacion / staging.estructura / staging.respaldos / "
            "staging.plan_remediacion hacia gdd.*"
        )
    )
    parser.add_argument(
        "--dominio",
        default=None,
        help=(
            "codigo_dominio puntual a mergear (ej. ADS). Si se omite, se "
            "mergean todos los codigo_dominio presentes en staging."
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

    engine = construir_engine(settings)
    staging_repo = StagingRepository(engine)

    if args.dominio:
        dominios = [args.dominio]
    else:
        dominios = staging_repo.dominios_distintos("staging.detalle_atributos", "codigo_dominio")

    if not dominios:
        logger.error("No hay dominios en staging.detalle_atributos para mergear")
        return 1

    logger.info("Se mergearan %d dominio(s): %s", len(dominios), ", ".join(dominios))

    hubo_error = False
    for codigo_dominio in dominios:
        resultado = ejecutar_merge_dominio(engine, staging_repo, codigo_dominio)
        if resultado.ok:
            logger.info(
                "OK %s -> atributo: %d insertados, %d actualizados, %d reemplazados, "
                "%d eliminados, %d omitidos (fecha retrocede), %d sin cambios | "
                "fuente_oficial: %d insertadas, %d actualizadas, %d reemplazadas, "
                "%d eliminadas, %d omitidas, %d sin cambios | "
                "fuente_consumo: %d insertadas, %d actualizadas, %d eliminadas, %d sin cambios",
                codigo_dominio,
                resultado.atributos_insertados,
                resultado.atributos_actualizados,
                resultado.atributos_reemplazados,
                resultado.atributos_eliminados,
                resultado.atributos_omitidos_fecha_retrocede,
                resultado.atributos_sin_cambios,
                resultado.fuentes_insertadas,
                resultado.fuentes_actualizadas,
                resultado.fuentes_reemplazadas,
                resultado.fuentes_eliminadas,
                resultado.fuentes_omitidas_fecha_retrocede,
                resultado.fuentes_sin_cambios,
                resultado.consumos_insertados,
                resultado.consumos_actualizados,
                resultado.consumos_eliminados,
                resultado.consumos_sin_cambios,
            )
        else:
            hubo_error = True
            logger.error("ERROR %s -> %s (dominio revertido por completo)", codigo_dominio, resultado.error)

        # Investigacion se mergea con su propia transaccion (ver
        # ejecutar_merge_investigacion_dominio): un error aqui no afecta lo
        # que ya se aplico arriba para atributo/fuente_oficial, y viceversa.
        resultado_inv = ejecutar_merge_investigacion_dominio(engine, staging_repo, codigo_dominio)
        if resultado_inv.ok:
            logger.info(
                "OK %s -> investigacion: %d insertadas, %d actualizadas, %d eliminadas, %d sin cambios",
                codigo_dominio,
                resultado_inv.insertadas,
                resultado_inv.actualizadas,
                resultado_inv.eliminadas,
                resultado_inv.sin_cambios,
            )
        else:
            hubo_error = True
            logger.error(
                "ERROR %s -> investigacion: %s (merge de investigacion revertido)",
                codigo_dominio, resultado_inv.error,
            )

        # Estructura (-> gdd.dominio_responsable) tambien se mergea con su
        # propia transaccion, igual que investigacion.
        resultado_estr = ejecutar_merge_estructura_dominio(engine, staging_repo, codigo_dominio)
        if resultado_estr.ok:
            logger.info(
                "OK %s -> dominio_responsable: %d insertadas, %d actualizadas, %d eliminadas, %d sin cambios",
                codigo_dominio,
                resultado_estr.insertadas,
                resultado_estr.actualizadas,
                resultado_estr.eliminadas,
                resultado_estr.sin_cambios,
            )
        else:
            hubo_error = True
            logger.error(
                "ERROR %s -> dominio_responsable: %s (merge de estructura revertido)",
                codigo_dominio, resultado_estr.error,
            )

        # Respaldos (-> gdd.respaldo) tambien se mergea con su propia
        # transaccion, igual que investigacion/estructura.
        resultado_resp = ejecutar_merge_respaldos_dominio(engine, staging_repo, codigo_dominio)
        if resultado_resp.ok:
            logger.info(
                "OK %s -> respaldo: %d insertadas, %d actualizadas, %d eliminadas, %d sin cambios",
                codigo_dominio,
                resultado_resp.insertadas,
                resultado_resp.actualizadas,
                resultado_resp.eliminadas,
                resultado_resp.sin_cambios,
            )
        else:
            hubo_error = True
            logger.error(
                "ERROR %s -> respaldo: %s (merge de respaldos revertido)",
                codigo_dominio, resultado_resp.error,
            )

        # Plan de remediacion (-> gdd.plan_remediacion) tambien se mergea
        # con su propia transaccion, igual que investigacion/estructura/
        # respaldos.
        resultado_plan = ejecutar_merge_plan_remediacion_dominio(engine, staging_repo, codigo_dominio)
        if resultado_plan.ok:
            logger.info(
                "OK %s -> plan_remediacion: %d insertadas, %d actualizadas, %d eliminadas, %d sin cambios",
                codigo_dominio,
                resultado_plan.insertadas,
                resultado_plan.actualizadas,
                resultado_plan.eliminadas,
                resultado_plan.sin_cambios,
            )
        else:
            hubo_error = True
            logger.error(
                "ERROR %s -> plan_remediacion: %s (merge de plan_remediacion revertido)",
                codigo_dominio, resultado_plan.error,
            )

    return 1 if hubo_error else 0


if __name__ == "__main__":
    sys.exit(main())

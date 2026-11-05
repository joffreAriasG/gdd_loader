"""
Completa vinculos pendientes de gdd.atributo (id_atributo_primario /
id_dominio_primario) cuando ya existe la coincidencia. Paso ADICIONAL y
separado del merge: no lo modifica ni lo reemplaza.

Uso:

    python -m gdd_loader.vinculos_cli                    # todos los dominios, SIMULACION
    python -m gdd_loader.vinculos_cli --dominio ADS      # un dominio, SIMULACION
    python -m gdd_loader.vinculos_cli --aplicar          # escribe en gdd

Sin --aplicar solo informa lo que haria. Pensado para correr despues de
merge_cli (por ejemplo, como segundo paso de la tarea de fin de dia).

Codigo de salida: 0 si ningun dominio fallo, 1 si alguno fallo (se revierte
solo ese dominio). Que queden vinculos sin coincidencia NO es un error.
"""

from __future__ import annotations

import argparse
import sys

from gdd_loader.config import cargar_settings
from gdd_loader.load.db import construir_engine
from gdd_loader.load.staging_repository import StagingRepository
from gdd_loader.logging_setup import configurar_logging
from gdd_loader.pipeline.vinculos_gdd import resolver_vinculos_dominio


def _dominios_validos(dominios: list) -> tuple[list[str], int]:
    """Descarta valores NULL o vacios de codigo_dominio (filas de staging sin
    dominio): no se pueden procesar y rompian el armado del mensaje de log.
    Retorna (dominios validos, cantidad descartada)."""
    validos = [str(d).strip() for d in dominios if d is not None and str(d).strip()]
    return validos, len(dominios) - len(validos)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Completa id_atributo_primario / id_dominio_primario pendientes en gdd.atributo"
    )
    parser.add_argument("--dominio", default=None,
                        help="codigo_dominio puntual. Si se omite, todos los de staging.detalle_atributos.")
    parser.add_argument("--aplicar", action="store_true",
                        help="Escribir en gdd (sin esto, solo simulacion).")
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
        dominios, descartados = _dominios_validos(
            staging_repo.dominios_distintos("staging.detalle_atributos", "codigo_dominio"))
        if descartados:
            logger.warning(
                "staging.detalle_atributos tiene filas con codigo_dominio NULL o vacio: se ignoran "
                "(revisar la carga que las genero)."
            )
    if not dominios:
        logger.error("No hay dominios en staging.detalle_atributos")
        return 1

    modo = "APLICAR" if args.aplicar else "SIMULACION (sin escribir; agregar --aplicar)"
    logger.info("Vinculos pendientes - modo %s - %d dominio(s): %s", modo, len(dominios), ", ".join(dominios))

    hubo_error = False
    for codigo_dominio in dominios:
        r = resolver_vinculos_dominio(engine, staging_repo, codigo_dominio, aplicar=args.aplicar)
        if not r.ok:
            hubo_error = True
            logger.error("ERROR %s -> %s (dominio revertido)", codigo_dominio, r.error)
            continue
        logger.info(
            "%s -> %s: %d | sin coincidencia: %d | conflictos: %d | omitidos por fecha: %d | "
            "ya vinculados: %d | sin texto de vinculo: %d | no estan en gdd: %d",
            codigo_dominio, "completados" if r.aplicado else "se completarian", len(r.resueltos),
            len(r.sin_coincidencia), len(r.conflictos), r.omitidos_fecha, r.ya_vinculados,
            r.sin_texto, r.no_en_gdd,
        )
        for v in r.resueltos:
            logger.info("  %s: %s", v.codigo_dominio_atributo, v.columnas)
        for cod, atributo, dominio in r.sin_coincidencia:
            logger.info("  SIN COINCIDENCIA %s: atributo='%s' dominio='%s'", cod, atributo, dominio)
        for c in r.conflictos:
            logger.warning("  CONFLICTO %s", c)

    return 1 if hubo_error else 0


if __name__ == "__main__":
    sys.exit(main())

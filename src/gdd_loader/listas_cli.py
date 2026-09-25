"""
Listas de referencia de la plantilla vs catalogos de la BD (solo lectura).

    python -m gdd_loader.listas_cli comparar
    python -m gdd_loader.listas_cli comparar --script-semilla semilla_pruebas.sql

`comparar` usa el molde VIGENTE de GDD_CARPETA_MOLDES y muestra, por grupo,
los valores que faltan en la BD y los que solo estan en la BD. Sirve para:
  - PRUEBAS: ver en que difiere la BD de pruebas de produccion (la plantilla
    registrada tiene los valores reales) y generar un script de semilla.
  - PRODUCCION: confirmar que todo coincide antes de GDD_LISTAS_REFERENCIA=BD.

--script-semilla escribe un script T-SQL (NO lo ejecuta) que inserta los
valores faltantes en los catalogos simples. Revisarlo y correrlo a mano, solo
en el ambiente de pruebas.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from gdd_loader.config import cargar_settings
from gdd_loader.exportar.generador import GeneracionError, buscar_molde, version_vigente
from gdd_loader.exportar.listas_referencia import (
    ListasRepository,
    comparar,
    leer_listas_molde,
    script_semilla,
)
from gdd_loader.load.control_repository import ControlRepository
from gdd_loader.load.db import construir_engine
from gdd_loader.logging_setup import configurar_logging


def main() -> int:
    parser = argparse.ArgumentParser(description="Compara las listas de la plantilla con los catalogos")
    sub = parser.add_subparsers(dest="accion", required=True)
    cmp_ = sub.add_parser("comparar")
    cmp_.add_argument("--script-semilla", type=Path, default=None)
    cmp_.add_argument("--max", type=int, default=10, help="Valores a mostrar por grupo")
    parser.add_argument("--env-file", default=None)
    args = parser.parse_args()

    settings = cargar_settings(args.env_file)
    logger = configurar_logging(settings.log_dir, settings.log_level)
    try:
        settings.validar()
        if settings.carpeta_moldes is None:
            raise ValueError("GDD_CARPETA_MOLDES no esta configurada")
    except ValueError as exc:
        logger.error(str(exc))
        return 1

    engine = construir_engine(settings)
    try:
        vigente = version_vigente(ControlRepository(engine), settings.id_plantilla)
        molde = buscar_molde(settings.carpeta_moldes, vigente)
    except GeneracionError as exc:
        logger.error(str(exc))
        return 1

    listas = leer_listas_molde(molde)
    difs = comparar(listas, ListasRepository(engine))
    print(f"Molde: {molde.name} (version {vigente.version})\n")
    for d in difs:
        estado = "OK" if d.coincide else "DIFERENCIAS"
        print(f"[{estado}] {d.grupo} <- {d.origen_bd}")
        for titulo, valores in (("faltan en BD", d.faltan_en_bd), ("solo en BD", d.solo_en_bd)):
            if valores:
                muestra = ", ".join(valores[:args.max]) + (" ..." if len(valores) > args.max else "")
                print(f"    {titulo} ({len(valores)}): {muestra}")
    total = sum(not d.coincide for d in difs)
    print(f"\n{total} grupo(s) con diferencias de {len(difs)}.")

    if args.script_semilla:
        args.script_semilla.write_text(script_semilla(listas, difs), encoding="utf-8")
        print(f"Script de semilla (NO ejecutado): {args.script_semilla}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

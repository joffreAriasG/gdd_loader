"""
Herramienta del equipo central para registrar y publicar versiones de la
plantilla en gdd.plantilla_version (Fase 1).

1) Registrar una version a partir del MOLDE real (el .xlsx que se publica en
   SharePoint). Lee los encabezados de las hojas del contrato, calcula la
   huella y la inserta como BORRADOR. Sin --aplicar solo muestra lo que haria.

    python -m gdd_loader.plantilla_cli registrar --molde Plantilla_v1.0.0.xlsx --version 1.0.0
    python -m gdd_loader.plantilla_cli registrar --molde Plantilla_v1.0.0.xlsx --version 1.0.0 --aplicar

2) Publicarla: pasa a VIGENTE y la vigente anterior a DEPRECADA (con dias de
   gracia) o RETIRADA.

    python -m gdd_loader.plantilla_cli publicar --version 1.1.0 --anterior DEPRECADA --dias-gracia 15
    python -m gdd_loader.plantilla_cli publicar --version 2.0.0 --anterior RETIRADA

Reglas de versionado (ver db/README.md):
  MAJOR: renombrar/quitar hoja o columna, cambiar tipo o clave -> requiere
         cambio de loader; la anterior pasa a RETIRADA.
  MINOR: agregar columna u hoja -> la anterior pasa a DEPRECADA con gracia.
  PATCH: formatos, validaciones, listas (no cambia la huella).
"""

from __future__ import annotations

import argparse
import getpass
import sys
from datetime import date, timedelta
from pathlib import Path

from gdd_loader import __version__
from gdd_loader.config import cargar_settings
from gdd_loader.domain import plantilla as pl
from gdd_loader.domain.sheet_config import HOJAS_ADS
from gdd_loader.extract.control_reader import leer_control, leer_estructura
from gdd_loader.logging_setup import configurar_logging


def construir_version(molde: Path, id_plantilla: str, version: str,
                      version_loader_minima: str, hojas: list[str]) -> tuple[pl.VersionPlantilla, list[str]]:
    """Arma la VersionPlantilla desde el molde y devuelve tambien una lista
    de problemas encontrados (vacia = todo bien). Sin efectos en la base."""
    pl.parsear_version(version)
    pl.parsear_version(version_loader_minima)
    problemas: list[str] = []
    estructura = leer_estructura(molde)
    faltantes = [h for h in hojas if h not in estructura]
    if faltantes:
        problemas.append(f"El molde no tiene las hojas del contrato: {faltantes}")
    columnas = pl.restringir_a_contrato(estructura, hojas)

    # Cada columna que el loader LEE tiene que existir en el molde.
    for cfg in HOJAS_ADS:
        if cfg.nombre_hoja in columnas:
            falta = [c for c in cfg.columnas if c not in columnas[cfg.nombre_hoja]]
            if falta:
                problemas.append(f"Hoja '{cfg.nombre_hoja}': el loader necesita columnas ausentes {falta}")

    control = leer_control(molde)
    if control is None:
        problemas.append("El molde no tiene hoja _Control (agregarla antes de publicar)")
    else:
        c = pl.ControlPlantilla.desde_dict(control)
        if c.id_plantilla != id_plantilla or c.version_plantilla != version:
            problemas.append(
                f"_Control del molde declara {c.id_plantilla} {c.version_plantilla}, "
                f"se esta registrando {id_plantilla} {version}"
            )

    return pl.VersionPlantilla(
        id_plantilla=id_plantilla, version=version, estado="BORRADOR",
        hash_estructura=pl.calcular_hash_estructura(columnas),
        version_loader_minima=version_loader_minima, columnas=columnas,
    ), problemas


def main() -> int:
    parser = argparse.ArgumentParser(description="Registro de versiones de plantilla")
    sub = parser.add_subparsers(dest="accion", required=True)

    reg = sub.add_parser("registrar", help="Registrar una version (BORRADOR) desde el molde")
    reg.add_argument("--molde", required=True, type=Path)
    reg.add_argument("--version", required=True)
    reg.add_argument("--version-loader-minima", default=__version__)
    reg.add_argument("--hojas", nargs="*", default=[c.nombre_hoja for c in HOJAS_ADS],
                     help="Hojas del contrato (por defecto, las que lee el loader)")
    reg.add_argument("--notas", default=None)
    reg.add_argument("--aplicar", action="store_true", help="Insertar en la base (sin esto, solo muestra)")
    reg.add_argument("--forzar", action="store_true", help="Registrar aunque haya advertencias")

    pub = sub.add_parser("publicar", help="Publicar una version registrada (VIGENTE)")
    pub.add_argument("--version", required=True)
    pub.add_argument("--anterior", choices=["DEPRECADA", "RETIRADA"], required=True)
    pub.add_argument("--dias-gracia", type=int, default=15)

    parser.add_argument("--env-file", default=None)
    args = parser.parse_args()

    settings = cargar_settings(args.env_file)
    logger = configurar_logging(settings.log_dir, settings.log_level)

    if args.accion == "registrar":
        version, problemas = construir_version(
            args.molde, settings.id_plantilla, args.version, args.version_loader_minima, args.hojas)
        print(f"Plantilla {version.id_plantilla} {version.version}")
        print(f"  hash_estructura: {version.hash_estructura}")
        print(f"  version_loader_minima: {version.version_loader_minima}")
        for hoja, cols in version.columnas.items():
            print(f"  {hoja}: {len(cols)} columnas")
        for p in problemas:
            print(f"  ADVERTENCIA: {p}")
        if not args.aplicar:
            print("Simulacion: no se escribio nada (agregar --aplicar).")
            return 0
        if problemas and not args.forzar:
            print("No se registra por las advertencias (usar --forzar si son esperadas).")
            return 1

    try:
        settings.validar()
    except ValueError as exc:
        logger.error(str(exc))
        return 1

    from gdd_loader.load.control_repository import ControlRepository
    from gdd_loader.load.db import construir_engine

    repo = ControlRepository(construir_engine(settings))
    if args.accion == "registrar":
        repo.registrar_version(version, args.notas, getpass.getuser())
        logger.info("Version %s registrada como BORRADOR.", version.version)
    else:
        gracia = date.today() + timedelta(days=args.dias_gracia) if args.anterior == "DEPRECADA" else None
        anterior = repo.publicar_version(settings.id_plantilla, args.version, args.anterior, gracia)
        logger.info("Version %s publicada como VIGENTE. Anterior: %s -> %s%s", args.version,
                    anterior or "(ninguna)", args.anterior if anterior else "-",
                    f" hasta {gracia}" if anterior and gracia else "")
    return 0


if __name__ == "__main__":
    sys.exit(main())

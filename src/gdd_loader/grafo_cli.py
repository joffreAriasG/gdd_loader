"""
Genera el grafo de relaciones entre dominios (atributos Compartido / Similar)
como un archivo HTML autocontenido. SOLO LECTURA: no escribe en la base.

Uso:

    python -m gdd_loader.grafo_cli                          # HTML en la carpeta actual
    python -m gdd_loader.grafo_cli --salida "C:\\ruta\\carpeta"
    python -m gdd_loader.grafo_cli --salida grafo.html --titulo "Relaciones PRODUCCION"

Fuente: gdd.dominio_dato y la vista gdd.v_relacion_atributo (correr antes
db/migraciones/20261105_vistas_relacion_dominio.sql). No usa staging.

Codigo de salida: 0 si genero el archivo, 1 si fallo.
"""

from __future__ import annotations

import argparse
import datetime
import sys
from pathlib import Path

from gdd_loader.config import cargar_settings
from gdd_loader.exportar.grafo_relaciones import construir_modelo, generar_html
from gdd_loader.load.db import construir_engine
from gdd_loader.load.grafo_repository import VistaNoExisteError, leer_dominios, leer_relaciones
from gdd_loader.logging_setup import configurar_logging


def _construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Grafo HTML de relaciones entre dominios (solo lectura)")
    parser.add_argument("--salida", default=".",
                        help="Carpeta destino o ruta .html. Por defecto, la carpeta actual.")
    parser.add_argument("--titulo", default="Relaciones entre dominios", help="Titulo de la pagina")
    parser.add_argument("--env-file", default=None, help="Ruta a un .env especifico (opcional)")
    return parser


def resolver_ruta_salida(salida: str, ahora: datetime.datetime) -> Path:
    ruta = Path(salida)
    if ruta.suffix.lower() == ".html":
        return ruta
    return ruta / f"grafo_relaciones_{ahora:%Y%m%d_%H%M}.html"


def main() -> int:
    args = _construir_parser().parse_args()
    settings = cargar_settings(args.env_file)
    logger = configurar_logging(settings.log_dir, settings.log_level)
    try:
        settings.validar()
    except ValueError as exc:
        logger.error(str(exc))
        return 1

    ahora = datetime.datetime.now()
    engine = construir_engine(settings)
    try:
        with engine.connect() as conn:
            dominios = leer_dominios(conn)
            relaciones = leer_relaciones(conn)
    except VistaNoExisteError as exc:
        logger.error(str(exc))
        return 1

    modelo = construir_modelo(dominios, relaciones)
    r = modelo["resumen"]
    logger.info(
        "Grafo: %d dominios (%d con relaciones), %d aristas, %d de %d atributos relacionados en el grafo",
        r["n_dominios"], r["n_dominios_con_relacion"], r["n_aristas"],
        r["n_atributos_en_grafo"], r["n_atributos_relacionados"],
    )
    for estado, n in r["por_estado"].items():
        nivel = logger.info if estado in ("RESUELTO", "MISMO_DOMINIO") else logger.warning
        nivel("  estado %s: %d", estado, n)

    ruta = resolver_ruta_salida(args.salida, ahora)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(generar_html(modelo, f"{ahora:%Y-%m-%d %H:%M}", args.titulo), encoding="utf-8")
    logger.info("Archivo generado: %s", ruta.resolve())
    return 0


if __name__ == "__main__":
    sys.exit(main())

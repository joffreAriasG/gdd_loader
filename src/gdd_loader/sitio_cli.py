"""
Genera el sitio de consulta por dominio como un archivo HTML autocontenido
(sin internet ni servidor). SOLO LECTURA: no escribe en la base.

Secciones del menu lateral, por dominio:
  1. Dominio y estructura (datos del dominio, responsables y respaldos)
  2. Atributos y fuentes (fuentes oficiales y de consumo de cada atributo)
  3. Investigacion (sin los terminos)
  4. Glosario de terminos (clasificacion "Término" de Investigacion, todos los dominios)
  5. Relaciones entre dominios (grafo de atributos Compartido / Similar)
  6. Indicadores (puntaje de fuente oficial y cumplimiento por dominio)

Uso:

    python -m gdd_loader.sitio_cli                          # HTML en la carpeta actual
    python -m gdd_loader.sitio_cli --salida "C:\\ruta\\carpeta"
    python -m gdd_loader.sitio_cli --salida sitio.html --titulo "Glosario PRODUCCION"

Fuente: tablas gdd.* (solo registros activos) y, para la seccion 4, la vista
gdd.v_relacion_atributo (migracion 20261105_vistas_relacion_dominio.sql). Si la
vista no existe, el sitio se genera igual y la seccion 4 lo informa. No usa staging.
El HTML contiene valores reales (incluidos nombres de responsables): entregarlo
solo al equipo de gobierno de datos.

Codigo de salida: 0 si genero el archivo, 1 si fallo.
"""

from __future__ import annotations

import argparse
import datetime
import sys
from pathlib import Path

from gdd_loader.config import cargar_settings
from gdd_loader.exportar.grafo_relaciones import construir_modelo as construir_modelo_grafo
from gdd_loader.exportar.sitio_dominios import construir_modelo, generar_html
from gdd_loader.load.db import construir_engine
from gdd_loader.load.grafo_repository import VistaNoExisteError, leer_dominios, leer_relaciones
from gdd_loader.load.sitio_repository import leer_todo
from gdd_loader.logging_setup import configurar_logging


def _construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sitio HTML de consulta por dominio (solo lectura)")
    parser.add_argument("--salida", default=".",
                        help="Carpeta destino o ruta .html. Por defecto, la carpeta actual.")
    parser.add_argument("--titulo", default="Glosario de datos por dominio", help="Titulo de la pagina")
    parser.add_argument("--env-file", default=None, help="Ruta a un .env especifico (opcional)")
    return parser


def resolver_ruta_salida(salida: str, ahora: datetime.datetime) -> Path:
    ruta = Path(salida)
    if ruta.suffix.lower() == ".html":
        return ruta
    return ruta / f"sitio_dominios_{ahora:%Y%m%d_%H%M}.html"


def leer_relaciones_o_none(conn, logger) -> dict | None:
    """Modelo del grafo (seccion 4) o None si falta la vista gdd.v_relacion_atributo."""
    try:
        return construir_modelo_grafo(leer_dominios(conn), leer_relaciones(conn))
    except VistaNoExisteError as exc:
        logger.warning("%s La seccion 'Relaciones entre dominios' queda sin grafo.", exc)
        return None


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
    with engine.connect() as conn:
        datos = leer_todo(conn)
        relaciones = leer_relaciones_o_none(conn, logger)  # al final: si falla, no afecta lo anterior

    modelo = construir_modelo(datos, relaciones)
    if relaciones is not None:
        rr = relaciones["resumen"]
        logger.info("Relaciones: %d de %d dominios relacionados, %d aristas, %d de %d atributos relacionados en el grafo",
                    rr["n_dominios_con_relacion"], rr["n_dominios"], rr["n_aristas"],
                    rr["n_atributos_en_grafo"], rr["n_atributos_relacionados"])
    r = modelo["resumen"]
    t = r["totales"]
    logger.info(
        "Sitio: %d dominios (%d con atributos), %d atributos, %d fuentes oficiales, "
        "%d fuentes de consumo, %d responsables, %d respaldos, %d registros de investigacion, %d terminos",
        r["n_dominios"], r["n_dominios_con_atributos"], t["atributos"], t["fuentes_oficiales"],
        t["fuentes_consumo"], t["estructura"], t["respaldos"], t["investigacion"], t["terminos"],
    )
    for entidad, n in r["huerfanos"].items():
        logger.warning("  %d registros activos de %s sin dominio/atributo valido (no se muestran)", n, entidad)

    ruta = resolver_ruta_salida(args.salida, ahora)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(generar_html(modelo, f"{ahora:%Y-%m-%d %H:%M}", args.titulo), encoding="utf-8")
    logger.info("Archivo generado: %s", ruta.resolve())
    return 0


if __name__ == "__main__":
    sys.exit(main())

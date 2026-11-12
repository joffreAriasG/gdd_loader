"""
Genera el sitio de consulta por dominio (sin internet ni servidor). SOLO LECTURA:
no escribe en la base.

Secciones del menu lateral, por dominio:
  1. Dominio y estructura (datos del dominio, responsables y respaldos)
  2. Atributos y fuentes (fuentes oficiales y de consumo de cada atributo)
  3. Explorador de atributos (filtros combinables, columnas y descarga a Excel)
  4. Investigacion (sin los terminos)
  5. Glosario de terminos (clasificacion "Término" de Investigacion, todos los dominios)
  6. Relaciones entre dominios (grafo de atributos Compartido / Similar)
  7. Indicadores (puntaje de fuente oficial y cumplimiento por dominio)
  8. Planes de remediacion (v0.14.0: resumen, tabla con detalle desplegable, filtros y Excel)

Salida (v0.13.0):
  - --salida <carpeta> (por defecto): crea <carpeta>\\sitio_dominios_AAAAMMDD_HHMM\\ con
    index.html (seccion 1) y una pagina por seccion (atributos.html, explorador.html,
    investigacion.html, terminos.html, relaciones.html, indicadores.html, planes.html). Mismo
    diseno; el menu lateral enlaza las paginas: sirve para abrir la carpeta en el equipo o en una
    unidad compartida. Ademas (v0.14.2) deja sitio_completo.html: todas las secciones en un solo
    archivo. ES EL QUE SE PUBLICA EN SHAREPOINT: el visor "Paginas HTML" corre cada .html en un
    iframe aislado (sandbox="allow-scripts", srcdoc) donde no se puede pasar de un archivo a otro,
    ni descargar (el Excel se ofrece como tablas para copiar), ni guardar preferencias.
  - --salida <archivo>.html: solo el archivo unico con todas las secciones.
  Los datos van comprimidos dentro de cada pagina. Si alguna pagina supera --max-mb
  (por defecto 10, el limite por archivo de SharePoint), se registra una advertencia.

Uso:

    python -m gdd_loader.sitio_cli                          # carpeta del sitio en la carpeta actual
    python -m gdd_loader.sitio_cli --salida "C:\\ruta\\carpeta"
    python -m gdd_loader.sitio_cli --salida sitio.html --titulo "Glosario PRODUCCION"

Fuente: tablas gdd.* (solo registros activos) y, para la seccion 6, la vista
gdd.v_relacion_atributo (migracion 20261105_vistas_relacion_dominio.sql). Si la
vista no existe, el sitio se genera igual y la seccion 6 lo informa. No usa staging.
El HTML contiene valores reales (incluidos nombres de responsables): entregarlo
solo al equipo de gobierno de datos.

Codigo de salida: 0 si genero el sitio, 1 si fallo.
"""

from __future__ import annotations

import argparse
import datetime
import sys
from pathlib import Path

from gdd_loader.config import cargar_settings
from gdd_loader.exportar.grafo_relaciones import construir_modelo as construir_modelo_grafo
from gdd_loader.exportar.sitio_dominios import (ARCHIVO_UNICO, codigos_inactivos, construir_modelo,
                                                describir_estado_inactivo, filtrar_relaciones_vigencia, generar_html,
                                                generar_sitio)
from gdd_loader.load.db import construir_engine
from gdd_loader.load.grafo_repository import VistaNoExisteError, leer_dominios, leer_relaciones
from gdd_loader.load.sitio_repository import FaltaEstadoVigenciaError, leer_todo
from gdd_loader.logging_setup import configurar_logging


def _construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sitio HTML de consulta por dominio (solo lectura)")
    parser.add_argument("--salida", default=".",
                        help="Carpeta donde se crea el sitio de varias paginas, o ruta .html para un solo "
                             "archivo. Por defecto, la carpeta actual.")
    parser.add_argument("--titulo", default="Glosario de dominios", help="Titulo de la pagina")
    parser.add_argument("--max-mb", type=float, default=10.0,
                        help="Advertir si una pagina supera este tamano en MB (por defecto 10)")
    parser.add_argument("--env-file", default=None, help="Ruta a un .env especifico (opcional)")
    return parser


def resolver_ruta_salida(salida: str, ahora: datetime.datetime) -> Path:
    """Ruta .html (un solo archivo) o carpeta nueva del sitio de varias paginas."""
    ruta = Path(salida)
    if ruta.suffix.lower() == ".html":
        return ruta
    return ruta / f"sitio_dominios_{ahora:%Y%m%d_%H%M}"


def paginas_excedidas(paginas: dict[str, str], max_mb: float) -> list[tuple[str, float]]:
    """[(archivo, MB)] de las paginas que superan max_mb (tamano en UTF-8)."""
    tamanos = {nombre: len(html.encode("utf-8")) / 1_000_000 for nombre, html in paginas.items()}
    return [(nombre, mb) for nombre, mb in tamanos.items() if mb > max_mb]


def escribir_sitio(destino: Path, paginas: dict[str, str]) -> None:
    """Escribe las paginas en una carpeta nueva; no sobrescribe un sitio anterior."""
    destino.mkdir(parents=True, exist_ok=False)
    for nombre, html in paginas.items():
        (destino / nombre).write_text(html, encoding="utf-8")


def leer_relaciones_o_none(conn, logger, inactivos: set[str] | None = None) -> dict | None:
    """Modelo del grafo (seccion 6) o None si falta la vista gdd.v_relacion_atributo.
    Los dominios Inactivos no aparecen en el grafo (ver filtrar_relaciones_vigencia)."""
    try:
        dominios, relaciones = filtrar_relaciones_vigencia(leer_dominios(conn), leer_relaciones(conn),
                                                           inactivos or set())
        return describir_estado_inactivo(construir_modelo_grafo(dominios, relaciones))
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
        try:
            datos = leer_todo(conn)
        except FaltaEstadoVigenciaError as exc:
            logger.error("%s No se genera el sitio: sin el estado no se sabe que dominios no publicar.", exc)
            return 1
        # al final: si falla, no afecta lo anterior
        relaciones = leer_relaciones_o_none(conn, logger, codigos_inactivos(datos["dominios"]))

    modelo = construir_modelo(datos, relaciones)
    r = modelo["resumen"]
    logger.info("Estado de vigencia: %d dominio(s) Inactivo(s) excluidos del sitio; %d Confidencial(es) "
                "publicados sin fuentes oficiales ni de consumo", r["n_inactivos"], r["n_confidenciales"])
    for entidad, n in r["excluidos_inactivos"].items():
        logger.info("  %d registros de %s de dominios Inactivos no se publican", n, entidad)
    if r["planes_sin_atributo_vigente"]:
        logger.warning("%d plan(es) de remediacion activos son de atributos sin version vigente (dados de baja); "
                       "el sitio los muestra marcados. Revisar la baja en cascada de planes.",
                       r["planes_sin_atributo_vigente"])
    if r["planes_version_anterior"]:
        logger.warning("%d plan(es) de remediacion activos siguen ligados a una version anterior (inactiva) de su "
                       "atributo; el sitio los muestra con la version vigente. Revisar el merge de planes.",
                       r["planes_version_anterior"])
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

    generado = f"{ahora:%Y-%m-%d %H:%M}"
    ruta = resolver_ruta_salida(args.salida, ahora)
    if ruta.suffix.lower() == ".html":
        paginas = {ruta.name: generar_html(modelo, generado, args.titulo)}
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_text(paginas[ruta.name], encoding="utf-8")
        logger.info("Archivo generado: %s (%.2f MB)", ruta.resolve(), len(paginas[ruta.name].encode("utf-8")) / 1e6)
    else:
        paginas = generar_sitio(modelo, generado, args.titulo)
        try:
            escribir_sitio(ruta, paginas)
        except FileExistsError:
            logger.error("La carpeta %s ya existe; no se sobrescribe. Vuelva a intentarlo en un minuto "
                         "o indique otra --salida.", ruta.resolve())
            return 1
        for nombre, html in paginas.items():
            logger.info("  %-20s %.2f MB", nombre, len(html.encode("utf-8")) / 1e6)
        logger.info("Sitio generado (%d paginas + %s): %s  -> abrir index.html", len(paginas) - 1, ARCHIVO_UNICO,
                    ruta.resolve())
        logger.info("Para SharePoint (Paginas del sitio) publicar SOLO %s: el visor de SharePoint no permite pasar de un "
                    "archivo a otro; las paginas sueltas sirven para abrir la carpeta en el equipo o en una unidad compartida.",
                    ARCHIVO_UNICO)
    for nombre, mb in paginas_excedidas(paginas, args.max_mb):
        logger.warning("La pagina %s pesa %.2f MB: supera el limite de %.0f MB.", nombre, mb, args.max_mb)
    return 0


if __name__ == "__main__":
    sys.exit(main())

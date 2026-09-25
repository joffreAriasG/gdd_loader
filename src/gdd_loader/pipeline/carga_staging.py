"""
Capa de orquestacion (caso de uso): conecta extraccion + persistencia.
No sabe leer Excel ni escribir SQL directamente — delega en las otras
capas. Si mañana el origen cambia (Graph API) o el destino cambia
(otra base), este modulo no se toca.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from gdd_loader.domain.sheet_config import SheetConfig
from gdd_loader.extract.excel_reader import leer_hoja
from gdd_loader.load.staging_repository import StagingRepository

logger = logging.getLogger("gdd_loader.pipeline")


@dataclass
class ResultadoHoja:
    archivo: str
    hoja: str
    tabla: str
    filas_cargadas: int
    error: str | None = None


def _codigo_dominio_de_hoja(archivo: Path, hojas: list[SheetConfig], nombre_hoja_fuente: str) -> str:
    """Lee la hoja `nombre_hoja_fuente` del mismo archivo (ej. DetalleAtributos)
    y devuelve su codigo_dominio, para heredarlo en una hoja que no trae esa
    columna propia (ej. Investigacion). Falla -- en vez de adivinar -- si la
    hoja fuente no esta configurada, no tiene columna codigo_dominio, o trae
    mas de un codigo_dominio distinto (el archivo mezclaria mas de un
    dominio, y no hay forma de saber a cual pertenece cada fila de la hoja
    que hereda).
    """
    cfg_fuente = next((c for c in hojas if c.nombre_hoja == nombre_hoja_fuente), None)
    if cfg_fuente is None:
        raise ValueError(
            f"No se encontro la configuracion de la hoja '{nombre_hoja_fuente}' "
            "para heredar codigo_dominio."
        )
    df_fuente = leer_hoja(archivo, cfg_fuente)
    if "codigo_dominio" not in df_fuente.columns:
        raise ValueError(
            f"La hoja '{nombre_hoja_fuente}' no tiene columna codigo_dominio -- "
            "no se puede heredar desde ella."
        )
    valores = df_fuente["codigo_dominio"].dropna().unique().tolist()
    if len(valores) != 1:
        raise ValueError(
            f"No se pudo heredar codigo_dominio desde '{nombre_hoja_fuente}': se "
            f"esperaba exactamente un codigo_dominio en esa hoja y se encontraron "
            f"{len(valores)} ({valores}). Agregar la columna codigo_dominio "
            "directamente a la hoja que hereda para desambiguar."
        )
    return str(valores[0]).strip()


def ejecutar(
    archivo: Path,
    hojas: list[SheetConfig],
    repo: StagingRepository,
    estrategia_staging: str,
) -> list[ResultadoHoja]:
    resultados: list[ResultadoHoja] = []
    codigos_dominio_heredados: dict[str, str] = {}

    # El dominio de TODO el archivo se determina UNA sola vez, siempre
    # anclado en DetalleAtributos -- se usa para acotar el borrado de
    # staging de CADA hoja bajo estrategia DOMINIO (no solo las que heredan
    # codigo_dominio), incluso si esa hoja puntual viene vacia en este
    # archivo. Bug real corregido 2026-09-17 (segunda vuelta, ver docstring
    # de StagingRepository.cargar): antes el borrado se acotaba con los
    # valores presentes en el propio DataFrame de cada hoja -- si una hoja
    # quedaba completamente vacia (el usuario borro todas sus filas para
    # probar, ej. vacio toda PlanDeRemediacion), no habia ningun valor del
    # que derivar el dominio y el DELETE nunca se ejecutaba, dejando filas
    # huerfanas en staging para siempre. DetalleAtributos siempre debe
    # tener filas del dominio (es la hoja ancla de todo el archivo), asi
    # que anclar el borrado ahi elimina la dependencia de que CADA hoja
    # tenga datos propios. Si no se puede determinar (DetalleAtributos
    # falta, no tiene codigo_dominio, o trae mas de un dominio distinto),
    # no hay forma segura de acotar el borrado para NINGUNA hoja -- se
    # aborta la carga completa del archivo en vez de arriesgar un borrado
    # a medias o silenciosamente omitido.
    codigo_dominio_archivo: str | None = None
    if estrategia_staging == "DOMINIO":
        try:
            codigo_dominio_archivo = _codigo_dominio_de_hoja(archivo, hojas, "DetalleAtributos")
        except Exception as exc:  # noqa: BLE001 - no se puede acotar el borrado de ninguna hoja sin esto
            logger.error(
                "No se pudo determinar el dominio del archivo %s (leyendo DetalleAtributos): %s -- "
                "se aborta la carga a staging completa de este archivo.",
                archivo.name, exc,
            )
            return [
                ResultadoHoja(archivo.name, cfg.nombre_hoja, cfg.tabla_staging, 0, error=str(exc))
                for cfg in hojas
            ]
        codigos_dominio_heredados["DetalleAtributos"] = codigo_dominio_archivo

    for cfg in hojas:
        try:
            logger.info("Leyendo hoja '%s' de %s", cfg.nombre_hoja, archivo.name)
            df = leer_hoja(archivo, cfg)

            if cfg.hereda_codigo_dominio_de:
                fuente = cfg.hereda_codigo_dominio_de
                if fuente not in codigos_dominio_heredados:
                    codigos_dominio_heredados[fuente] = _codigo_dominio_de_hoja(
                        archivo, hojas, fuente
                    )
                df["codigo_dominio"] = codigos_dominio_heredados[fuente]

            logger.info(
                "Cargando %d filas a %s (estrategia=%s)",
                len(df), cfg.tabla_staging, estrategia_staging,
            )
            filas = repo.cargar(
                df,
                cfg.tabla_staging,
                cfg.columna_clave,
                estrategia_staging,
                columna_clave_prefijo_dominio=cfg.columna_clave_prefijo_dominio,
                codigo_dominio_archivo=codigo_dominio_archivo,
            )

            resultados.append(ResultadoHoja(archivo.name, cfg.nombre_hoja, cfg.tabla_staging, filas))
            logger.info("OK -> %s (%d filas)", cfg.tabla_staging, filas)

        except Exception as exc:  # noqa: BLE001 - se registra y se sigue con la siguiente hoja (y con el siguiente archivo)
            logger.error("Fallo en hoja '%s' de %s: %s", cfg.nombre_hoja, archivo.name, exc)
            resultados.append(
                ResultadoHoja(archivo.name, cfg.nombre_hoja, cfg.tabla_staging, 0, error=str(exc))
            )

    return resultados

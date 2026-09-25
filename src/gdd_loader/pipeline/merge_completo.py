"""
Merge staging -> gdd de un dominio COMPLETO (las 5 transacciones que hoy
corre merge_cli, en el mismo orden) devuelto como una lista uniforme de
ResumenMerge por tabla gdd -- para registrar en gdd.carga_control_detalle.

No cambia la logica de merge (carga_gdd.py): solo la invoca y normaliza sus
distintos objetos de resultado.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.engine import Engine

from gdd_loader.load.staging_repository import StagingRepository
from gdd_loader.pipeline.carga_gdd import (
    ejecutar_merge_dominio,
    ejecutar_merge_estructura_dominio,
    ejecutar_merge_investigacion_dominio,
    ejecutar_merge_plan_remediacion_dominio,
    ejecutar_merge_respaldos_dominio,
)


@dataclass
class ResumenMerge:
    objeto: str  # tabla gdd
    insertadas: int = 0
    actualizadas: int = 0
    reemplazadas: int = 0
    eliminadas: int = 0
    omitidas: int = 0
    sin_cambios: int = 0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    def conteos(self) -> dict:
        return {
            "insertadas": self.insertadas, "actualizadas": self.actualizadas,
            "reemplazadas": self.reemplazadas, "eliminadas": self.eliminadas,
            "omitidas": self.omitidas, "sin_cambios": self.sin_cambios,
            "error": self.error,
        }


def _resumen_simple(objeto: str, r) -> ResumenMerge:
    return ResumenMerge(
        objeto=objeto, insertadas=r.insertadas, actualizadas=r.actualizadas,
        eliminadas=r.eliminadas, sin_cambios=r.sin_cambios, error=r.error,
    )


def mergear_dominio_completo(
    engine: Engine,
    staging_repo: StagingRepository,
    codigo_dominio: str,
    id_carga: int | None = None,
) -> list[ResumenMerge]:
    r = ejecutar_merge_dominio(engine, staging_repo, codigo_dominio, id_carga=id_carga)
    # atributo / fuente_oficial / fuente_consumo comparten UNA transaccion:
    # si falla, las tres quedan con el mismo error.
    resumenes = [
        ResumenMerge(
            "gdd.atributo", r.atributos_insertados, r.atributos_actualizados,
            r.atributos_reemplazados, r.atributos_eliminados,
            r.atributos_omitidos_fecha_retrocede, r.atributos_sin_cambios, r.error,
        ),
        ResumenMerge(
            "gdd.atributo_fuente_oficial", r.fuentes_insertadas, r.fuentes_actualizadas,
            r.fuentes_reemplazadas, r.fuentes_eliminadas,
            r.fuentes_omitidas_fecha_retrocede, r.fuentes_sin_cambios, r.error,
        ),
        ResumenMerge(
            "gdd.atributo_fuente_consumo", r.consumos_insertados, r.consumos_actualizados,
            0, r.consumos_eliminados, 0, r.consumos_sin_cambios, r.error,
        ),
    ]
    resumenes.append(_resumen_simple(
        "gdd.investigacion",
        ejecutar_merge_investigacion_dominio(engine, staging_repo, codigo_dominio)))
    resumenes.append(_resumen_simple(
        "gdd.dominio_responsable",
        ejecutar_merge_estructura_dominio(engine, staging_repo, codigo_dominio)))
    resumenes.append(_resumen_simple(
        "gdd.respaldo",
        ejecutar_merge_respaldos_dominio(engine, staging_repo, codigo_dominio)))
    resumenes.append(_resumen_simple(
        "gdd.plan_remediacion",
        ejecutar_merge_plan_remediacion_dominio(engine, staging_repo, codigo_dominio)))
    return resumenes

"""
Regla de versionamiento por fecha_aprobacion, acordada para la capa `gdd`
(staging se mantiene como espejo simple; esta logica es la que decide como
pasa de staging a gdd). Es logica pura -- no toca la base de datos -- para
poder probarla exhaustivamente sin mocks.

Regla (misma clave = misma combinacion de atributos que identifica la fila
de negocio, ej. (codigo_dominio, codigo_atributo)):

- Clave nueva (solo en "entrantes"): INSERTAR.
- Clave en ambos, misma fecha_aprobacion: ACTUALIZAR (mismo id, se
  sobreescriben los campos mutables).
- Clave en ambos, fecha_aprobacion entrante mas reciente: REEMPLAZAR
  (se borra la fila vieja y se inserta una nueva -- no un update in-place,
  para no arrastrar campos de la version anterior).
- Clave en ambos, fecha_aprobacion entrante MAS ANTIGUA que la ya
  registrada: no deberia pasar en operacion normal (una fecha de
  aprobacion no "retrocede"). Se trata como anomalia: se OMITE (no se
  toca la fila existente) y se reporta como advertencia, nunca se
  retrocede una version silenciosamente.
- Clave que ya no aparece en "entrantes" (solo en "existentes", dentro del
  mismo dominio que se esta procesando): ELIMINAR.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from enum import Enum
from typing import Generic, TypeVar

ClaveT = TypeVar("ClaveT")
EntranteT = TypeVar("EntranteT")


class Accion(str, Enum):
    INSERTAR = "INSERTAR"
    ACTUALIZAR = "ACTUALIZAR"
    REEMPLAZAR = "REEMPLAZAR"
    ELIMINAR = "ELIMINAR"
    OMITIR_FECHA_RETROCEDE = "OMITIR_FECHA_RETROCEDE"


@dataclass(frozen=True)
class ExistenteVersionado:
    """Lo minimo que hace falta saber de una fila ya presente en gdd para
    decidir que hacer con ella: su id y su fecha_aprobacion actual.
    """

    id: int
    fecha_aprobacion: datetime.date | None


@dataclass(frozen=True)
class OperacionMerge(Generic[ClaveT, EntranteT]):
    accion: Accion
    clave: ClaveT
    entrante: EntranteT | None = None  # None cuando accion == ELIMINAR
    id_existente: int | None = None  # None cuando accion == INSERTAR


def calcular_plan_merge(
    existentes: dict[ClaveT, ExistenteVersionado],
    entrantes: dict[ClaveT, tuple[EntranteT, datetime.date | None]],
) -> list[OperacionMerge[ClaveT, EntranteT]]:
    """`entrantes`: clave -> (objeto_entrante, fecha_aprobacion_entrante).

    fecha_aprobacion puede ser None (ej. metadata_tecnica hoy no siempre
    trae fecha parseable) -- en ese caso se trata como "sin fecha para
    comparar" y siempre se ACTUALIZA in-place (no se puede saber si es mas
    reciente o no).
    """
    plan: list[OperacionMerge[ClaveT, EntranteT]] = []

    for clave, (entrante, fecha_entrante) in entrantes.items():
        existente = existentes.get(clave)
        if existente is None:
            plan.append(OperacionMerge(Accion.INSERTAR, clave, entrante=entrante))
            continue

        if fecha_entrante is None or existente.fecha_aprobacion is None:
            plan.append(
                OperacionMerge(
                    Accion.ACTUALIZAR, clave, entrante=entrante, id_existente=existente.id
                )
            )
        elif fecha_entrante == existente.fecha_aprobacion:
            plan.append(
                OperacionMerge(
                    Accion.ACTUALIZAR, clave, entrante=entrante, id_existente=existente.id
                )
            )
        elif fecha_entrante > existente.fecha_aprobacion:
            plan.append(
                OperacionMerge(
                    Accion.REEMPLAZAR, clave, entrante=entrante, id_existente=existente.id
                )
            )
        else:
            plan.append(
                OperacionMerge(
                    Accion.OMITIR_FECHA_RETROCEDE,
                    clave,
                    entrante=entrante,
                    id_existente=existente.id,
                )
            )

    for clave, existente in existentes.items():
        if clave not in entrantes:
            plan.append(OperacionMerge(Accion.ELIMINAR, clave, id_existente=existente.id))

    return plan

"""
Funcion ADICIONAL (no forma parte del merge): completa en gdd.atributo los
vinculos id_atributo_primario / id_dominio_primario que quedaron en NULL
porque, al procesar el dominio, el atributo primario todavia no existia en
gdd (por ejemplo, su dominio se cargo despues).

Fuente de los textos: staging.detalle_atributos (atributo_primario,
dominio_primario). Resolucion: gdd_repository.buscar_atributo_primario
(coincidencia CONJUNTA de nombre de atributo y de dominio, "mejor esfuerzo").

Reglas:
  - SIMULACION por defecto: sin aplicar=True no escribe nada.
  - Solo toca atributos ACTIVOS de gdd con alguno de los dos campos en NULL y
    cuya fila de staging trae AMBOS textos.
  - Solo se aplica si fecha_aprobada de staging == fecha_aprobacion de gdd (es
    decir, si el merge ya consolido esa version). Si difieren, se omite: no
    se aplican datos de una carga que el merge todavia no proceso.
  - Solo completa NULL; nunca pisa un valor existente. Si el valor existente
    difiere del que resolveria, se informa como CONFLICTO y no se toca.
  - Escribe UNICAMENTE id_atributo_primario / id_dominio_primario: no cambia
    version, release ni fecha_aprobacion y no genera versiones nuevas.
  - Una linea ACTUALIZAR en gdd.bitacora_carga por atributo (sin id_carga).
  - Una transaccion por dominio; idempotente.
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass, field

from sqlalchemy.engine import Engine

from gdd_loader.domain.gdd_mapping import _texto_o_none
from gdd_loader.load import gdd_repository as repo
from gdd_loader.load import vinculos_repository as vrepo
from gdd_loader.load.staging_repository import StagingRepository

logger = logging.getLogger("gdd_loader.vinculos")


@dataclass
class VinculoResuelto:
    codigo_dominio_atributo: str
    columnas: dict  # {columna: id} que se completan (o se completarian)


@dataclass
class ResultadoVinculos:
    codigo_dominio: str
    aplicado: bool
    resueltos: list[VinculoResuelto] = field(default_factory=list)
    sin_coincidencia: list[tuple[str, str, str]] = field(default_factory=list)  # (cod_dom_atr, atributo, dominio)
    conflictos: list[str] = field(default_factory=list)
    omitidos_fecha: int = 0
    sin_texto: int = 0
    ya_vinculados: int = 0
    no_en_gdd: int = 0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def _a_fecha(valor):
    if valor is None:
        return None
    if isinstance(valor, datetime.datetime) or (hasattr(valor, "date") and callable(valor.date)):
        return valor.date()
    return valor


def resolver_vinculos_dominio(
    engine: Engine, staging_repo: StagingRepository, codigo_dominio: str, aplicar: bool = False
) -> ResultadoVinculos:
    resultado = ResultadoVinculos(codigo_dominio=codigo_dominio, aplicado=aplicar)
    try:
        filas = staging_repo.leer("staging.detalle_atributos", "codigo_dominio", codigo_dominio)
        # begin() confirma al salir; connect() no escribe (simulacion).
        contexto = engine.begin() if aplicar else engine.connect()
        with contexto as conn:
            for fila in filas:
                try:
                    codigo_atributo = int(fila["codigo_atributo"])
                except (TypeError, ValueError):
                    continue
                cod_dom_atr = f"{codigo_dominio}-{codigo_atributo}"

                texto_atributo = _texto_o_none(fila.get("atributo_primario"))
                texto_dominio = _texto_o_none(fila.get("dominio_primario"))
                if texto_atributo is None or texto_dominio is None:
                    resultado.sin_texto += 1
                    continue

                actual = vrepo.atributo_activo(conn, codigo_dominio, codigo_atributo)
                if actual is None:
                    resultado.no_en_gdd += 1
                    continue

                if (actual["id_atributo_primario"] is not None
                        and actual["id_dominio_primario"] is not None):
                    resultado.ya_vinculados += 1
                    continue

                if _a_fecha(fila.get("fecha_aprobada")) != _a_fecha(actual["fecha_aprobacion"]):
                    resultado.omitidos_fecha += 1
                    continue

                id_atributo_primario, id_dominio_primario = repo.buscar_atributo_primario(
                    conn, texto_atributo, texto_dominio
                )
                if id_atributo_primario is None:
                    resultado.sin_coincidencia.append((cod_dom_atr, texto_atributo, texto_dominio))
                    continue

                resueltos = {
                    "id_atributo_primario": id_atributo_primario,
                    "id_dominio_primario": id_dominio_primario,
                }
                choque = [
                    c for c, nuevo in resueltos.items()
                    if actual[c] is not None and actual[c] != nuevo
                ]
                if choque:
                    resultado.conflictos.append(
                        f"{cod_dom_atr}: gdd tiene {', '.join(f'{c}={actual[c]}' for c in choque)} "
                        f"y la coincidencia actual resolveria {resueltos}"
                    )
                    continue

                a_completar = {c: v for c, v in resueltos.items() if actual[c] is None}
                resultado.resueltos.append(VinculoResuelto(cod_dom_atr, a_completar))
                if aplicar:
                    vrepo.completar_vinculos(conn, actual["id"], a_completar)
                    repo.registrar_bitacora(
                        conn, "atributo", actual["id"], "ACTUALIZAR",
                        codigo_dominio, cod_dom_atr, actual["version"],
                    )
    except Exception as exc:  # noqa: BLE001 - se reporta y se revierte SOLO este dominio
        logger.error("Vinculos del dominio '%s' abortados: %s", codigo_dominio, exc)
        resultado.error = str(exc)
        resultado.resueltos.clear()
    return resultado

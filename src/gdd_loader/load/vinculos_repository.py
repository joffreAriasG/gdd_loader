"""
Acceso a datos de la funcion adicional de vinculos pendientes
(pipeline/vinculos_gdd.py). Archivo APARTE de gdd_repository.py a proposito:
esta funcion no forma parte del merge y no debe tocarlo.

Solo dos operaciones:
  - atributo_activo: fila activa de gdd.atributo por (codigo_dominio,
    codigo_atributo), con los campos que hacen falta para decidir.
  - completar_vinculos: UPDATE de id_atributo_primario / id_dominio_primario,
    unicamente de las columnas que el llamador indica (las que estan NULL).
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Connection

# Unicas columnas que esta funcion tiene permitido escribir en gdd.atributo.
COLUMNAS_VINCULO = ("id_atributo_primario", "id_dominio_primario")


def atributo_activo(conn: Connection, codigo_dominio: str, codigo_atributo: int) -> dict | None:
    fila = conn.execute(
        text(
            "SELECT id, version, fecha_aprobacion, id_atributo_primario, id_dominio_primario "
            "FROM gdd.atributo "
            "WHERE codigo_dominio = :cod AND codigo_atributo = :atr AND activo = 1"
        ),
        {"cod": codigo_dominio, "atr": codigo_atributo},
    ).mappings().first()
    return dict(fila) if fila is not None else None


def completar_vinculos(conn: Connection, id_atributo: int, valores: dict) -> None:
    """`valores`: {columna: id} solo con las columnas a completar. Nunca
    escribe fuera de COLUMNAS_VINCULO ni sobre una columna que ya tenga
    valor (el WHERE exige que siga en NULL: protege ante una corrida
    concurrente del merge)."""
    invalidas = set(valores) - set(COLUMNAS_VINCULO)
    if invalidas or not valores:
        raise ValueError(f"completar_vinculos: columnas no permitidas o vacias {sorted(invalidas)}")
    set_clause = ", ".join(f"[{c}] = :{c}" for c in valores)
    solo_si_null = " AND ".join(f"[{c}] IS NULL" for c in valores)
    conn.execute(
        text(f"UPDATE gdd.atributo SET {set_clause} WHERE id = :id AND {solo_si_null}"),
        {**valores, "id": id_atributo},
    )

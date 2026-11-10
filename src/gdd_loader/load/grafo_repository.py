"""
Lectura (solo SELECT) de los datos para el grafo de relaciones entre dominios.

Fuente unica: tablas gdd.* y la vista gdd.v_relacion_atributo
(db/migraciones/20261105_vistas_relacion_dominio.sql). No lee staging.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Connection

SQL_DOMINIOS = text(
    "SELECT d.id AS id_dominio, d.codigo_dominio, d.nombre_dominio, td.tipo_dominio, "
    "       (SELECT COUNT(*) FROM gdd.atributo a WHERE a.id_dominio = d.id AND a.activo = 1) "
    "           AS n_atributos_activos "
    "FROM gdd.dominio_dato d "
    "LEFT JOIN gdd.cat_tipo_dominio td ON td.id = d.id_tipo_dominio"
)

SQL_RELACIONES = text(
    "SELECT id_atributo_origen, codigo_atributo_origen, nombre_atributo_origen, "
    "       id_dominio_origen, codigo_dominio_origen, nombre_dominio_origen, tipo_relacion, "
    "       id_atributo_destino, nombre_atributo_destino, atributo_destino_activo, "
    "       id_dominio_destino, codigo_dominio_destino, nombre_dominio_destino, estado_vinculo "
    "FROM gdd.v_relacion_atributo"
)


class VistaNoExisteError(RuntimeError):
    """La vista gdd.v_relacion_atributo no existe (falta correr la migracion)."""


def _filas(conn: Connection, sql) -> list[dict]:
    return [dict(f._mapping) for f in conn.execute(sql)]


def leer_dominios(conn: Connection) -> list[dict]:
    return _filas(conn, SQL_DOMINIOS)


def leer_relaciones(conn: Connection) -> list[dict]:
    try:
        return _filas(conn, SQL_RELACIONES)
    except Exception as exc:  # pyodbc: "Invalid object name 'gdd.v_relacion_atributo'"
        if "v_relacion_atributo" in str(exc):
            raise VistaNoExisteError(
                "No existe gdd.v_relacion_atributo. Correr antes "
                "db/migraciones/20261105_vistas_relacion_dominio.sql en DGODELIVERY."
            ) from exc
        raise

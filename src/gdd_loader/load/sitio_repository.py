"""
Lectura (solo SELECT) de los datos para el sitio de consulta por dominio (v0.10.0).

Fuente unica: tablas gdd.* (no lee staging ni vistas). Solo registros ACTIVOS.
Las uniones y nombres de catalogo son los mismos de notificacion/instantanea.py
(ya probados contra la base), pero aqui se leen TODOS los dominios de una vez
(una consulta por entidad, no una por dominio) e incluyen codigo_dominio para
agrupar.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Connection

# Solo lo que muestra el sitio (2026-11-10): la ficha del dominio lleva tipo y
# frontera; el atributo no lleva release. estado_vigencia (v0.13.0, migracion
# 20261111_dominio_estado_vigencia.sql): Activo / Inactivo / Confidencial.
SQL_DOMINIOS = text("""
    SELECT d.codigo_dominio, d.nombre_dominio, td.tipo_dominio, d.frontera,
           ev.nombre_estado_vigencia AS estado_vigencia
    FROM gdd.dominio_dato d
    LEFT JOIN gdd.cat_tipo_dominio td ON td.id = d.id_tipo_dominio
    LEFT JOIN gdd.cat_estado_vigencia_dominio ev ON ev.id = d.id_estado_vigencia
""")

SQL_TIENE_ESTADO_VIGENCIA = text("SELECT COL_LENGTH('gdd.dominio_dato', 'id_estado_vigencia')")


class FaltaEstadoVigenciaError(RuntimeError):
    """La base no tiene gdd.dominio_dato.id_estado_vigencia (falta correr la migracion)."""

SQL_RESPONSABLES = text("""
    SELECT d.codigo_dominio, r.id_rol, cr.nombre_rol AS rol, r.area, r.codigo_plaza, r.nombre_plaza,
           r.nombre_responsable, r.fecha_aprobada
    FROM gdd.dominio_responsable r
    JOIN gdd.dominio_dato d  ON d.id = r.id_dominio
    LEFT JOIN gdd.cat_rol cr ON cr.id = r.id_rol
    WHERE r.activo = 1
""")

SQL_RESPALDOS = text("""
    SELECT p.codigo_dominio, tr.nombre_tipo_respaldo AS tipo_respaldo, p.enlace_respaldo,
           p.observaciones, p.fecha_aprobada
    FROM gdd.respaldo p
    LEFT JOIN gdd.cat_tipo_respaldo tr ON tr.id = p.id_tipo_respaldo
    WHERE p.activo = 1
""")

SQL_ATRIBUTOS = text("""
    SELECT d.codigo_dominio, a.codigo_atributo, a.nombre_atributo, a.descripcion_atributo,
           cr.nombre_criticidad         AS criticidad,
           ta.nombre_tipo_atributo      AS tipo_atributo,
           cs.nombre_compartido_similar AS compartido_similar,
           a.es_dato_personal,
           dp.codificacion              AS clasificacion_dato_personal,
           a.reporte, a.proyecto, a.proceso, a.estructura,
           ap.nombre_atributo           AS atributo_primario,
           dpp.nombre_dominio           AS dominio_primario,
           a.fecha_aprobacion, a.version
    FROM gdd.atributo a
    JOIN gdd.dominio_dato d                 ON d.id = a.id_dominio
    LEFT JOIN gdd.cat_criticidad cr         ON cr.id = a.id_criticidad
    LEFT JOIN gdd.cat_tipo_atributo ta      ON ta.id = a.id_tipo_atributo
    LEFT JOIN gdd.cat_compartido_similar cs ON cs.id = a.id_compartido_similar
    LEFT JOIN gdd.cat_dato_personal dp      ON dp.id = a.id_dato_personal
    LEFT JOIN gdd.atributo ap               ON ap.id = a.id_atributo_primario
    LEFT JOIN gdd.dominio_dato dpp          ON dpp.id = a.id_dominio_primario
    WHERE a.activo = 1
""")

SQL_FUENTES_OFICIALES = text("""
    SELECT d.codigo_dominio, a.codigo_atributo,
           sv.nombre_servidor AS servidor, bd.nombre_bdd AS base_datos,
           f.nombre_tabla AS tabla, f.nombre_campo, f.es_fuente_primaria,
           f.longitud_campo, vv.valor AS tipo_campo, f.acepta_valores_nulos,
           f.formula_calculo, f.lista_valores_validos, f.fecha_aprobacion, f.version
    FROM gdd.atributo_fuente_oficial f
    JOIN gdd.atributo a           ON a.id = f.id_atributo
    JOIN gdd.dominio_dato d       ON d.id = a.id_dominio
    JOIN gdd.base_datos_fuente bd ON bd.id = f.id_bdd
    JOIN gdd.servidor_fuente sv   ON sv.id = bd.id_servidor
    LEFT JOIN gdd.campo_fuente_valor_valido vv ON vv.id = f.id_campo_fuente_valor_valido
    WHERE a.activo = 1 AND f.activo = 1
""")

SQL_FUENTES_CONSUMO = text("""
    SELECT d.codigo_dominio, a.codigo_atributo,
           sv.nombre_servidor AS servidor, bd.nombre_bdd AS base_datos,
           t.nombre_tabla AS tabla, c.nombre_campo, c.clase, c.fecha_aprobacion
    FROM gdd.atributo_fuente_consumo c
    JOIN gdd.atributo a                ON a.id = c.id_atributo
    JOIN gdd.dominio_dato d            ON d.id = a.id_dominio
    JOIN gdd.base_datos_fuente_tabla t ON t.id = c.id_tabla
    JOIN gdd.base_datos_fuente bd      ON bd.id = c.id_bdd
    JOIN gdd.servidor_fuente sv        ON sv.id = bd.id_servidor
    WHERE a.activo = 1 AND c.activo = 1
""")

SQL_INVESTIGACION = text("""
    SELECT i.codigo_dominio, ci.nombre_clasificacion AS clasificacion, i.nombre,
           i.descripcion, i.referencia_normativa
    FROM gdd.investigacion i
    LEFT JOIN gdd.cat_clasificacion_investigacion ci ON ci.id = i.id_clasificacion
    WHERE i.activo = 1
""")

# Catalogo de roles (indicador "estructura completa": roles requeridos por id, ver sitio_dominios).
SQL_ROLES = text("SELECT id, nombre_rol FROM gdd.cat_rol")

# Planes de remediacion activos (hoja PlanDeRemediacion), catalogos resueltos a texto. El dominio sale
# del atributo (igual que el resto del sitio).
# OJO (hallazgo 2026-11-12): el merge identifica el plan por (codigo_dominio_atributo, id_problema) y no
# cambia id_atributo al actualizar; cuando el atributo se REEMPLAZA (version nueva), el plan sigue ligado
# a la version anterior (inactiva). Por eso el plan se une a la version ACTIVA del mismo atributo
# (mismo dominio y codigo) y se informa la version ligada (version_ligada / version_ligada_activa).
# OUTER APPLY (no CROSS): un plan activo cuyo atributo ya no tiene ninguna version activa (dado de baja sin
# que el plan se diera de baja) NO se pierde en silencio: llega con atributo_vigente = 0 y se cuenta.
SQL_PLANES = text("""
    SELECT d.codigo_dominio,
           COALESCE(a.codigo_atributo, av.codigo_atributo) AS codigo_atributo,
           COALESCE(a.nombre_atributo, av.nombre_atributo) AS nombre_atributo,
           p.id_problema,
           av.version AS version_ligada,
           CASE WHEN av.activo = 1 THEN 1 ELSE 0 END AS version_ligada_activa,
           a.version AS version_vigente,
           CASE WHEN a.id IS NULL THEN 0 ELSE 1 END AS atributo_vigente,
           td.nombre_tipo_dimension      AS dimension,
           p.categoria, p.fuente_oficial_sistema,
           tp.nombre_tipo_plan           AS tipo_plan,
           cp.nombre_categoria_plan      AS categoria_plan,
           sc.nombre_sub_categoria_plan  AS subcategoria_plan,
           p.priorizacion,
           ep.nombre_estado_plan         AS estado,
           p.fecha_identificacion, p.fecha_finalizacion_definitiva, p.fecha_ultima_modificacion,
           p.avance, p.descripcion_causa_raiz, p.persona_responsable, p.dependencias, p.observacion,
           p.acciones_corto_plazo, p.acciones_definitivo, p.siro
    FROM gdd.plan_remediacion p
    JOIN gdd.atributo av                    ON av.id = p.id_atributo          -- version a la que apunta el plan
    OUTER APPLY (SELECT TOP 1 x.id, x.codigo_atributo, x.nombre_atributo, x.version
                 FROM gdd.atributo x
                 WHERE x.id_dominio = av.id_dominio AND x.codigo_atributo = av.codigo_atributo AND x.activo = 1
                 ORDER BY x.id DESC) a                                         -- version activa (NULL si no hay)
    JOIN gdd.dominio_dato d                 ON d.id = av.id_dominio
    LEFT JOIN gdd.cat_tipo_dimension td     ON td.id = p.id_dimension
    LEFT JOIN gdd.cat_tipo_plan tp          ON tp.id = p.id_tipo_plan
    LEFT JOIN gdd.cat_categoria_plan cp     ON cp.id = p.id_categoria_plan
    LEFT JOIN gdd.cat_sub_categoria_plan sc ON sc.id = p.id_sub_categoria_plan
    LEFT JOIN gdd.cat_estado_plan ep        ON ep.id = p.id_estado_plan
    WHERE p.activo = 1
""")

# Catalogo de clasificaciones de investigacion: la seccion Investigacion muestra TODAS (sin Termino),
# en el orden del catalogo, aunque no tengan registros (p. ej. "Actualización de datos").
SQL_CLASIFICACIONES = text("SELECT id, nombre_clasificacion FROM gdd.cat_clasificacion_investigacion")

CONSULTAS = {
    "dominios": SQL_DOMINIOS,
    "roles": SQL_ROLES,
    "clasificaciones": SQL_CLASIFICACIONES,
    "responsables": SQL_RESPONSABLES,
    "respaldos": SQL_RESPALDOS,
    "atributos": SQL_ATRIBUTOS,
    "fuentes_oficiales": SQL_FUENTES_OFICIALES,
    "fuentes_consumo": SQL_FUENTES_CONSUMO,
    "investigacion": SQL_INVESTIGACION,
    "planes": SQL_PLANES,
}


def leer_todo(conn: Connection) -> dict[str, list[dict]]:
    """Solo lectura. Devuelve {entidad: [filas como dict]} para todas las entidades.

    Sin la columna de estado de vigencia se detiene: sin ella el sitio no sabria que
    dominios son Inactivos (no se publican) o Confidenciales (sin fuentes)."""
    if conn.execute(SQL_TIENE_ESTADO_VIGENCIA).scalar() is None:
        raise FaltaEstadoVigenciaError(
            "gdd.dominio_dato no tiene id_estado_vigencia. Correr antes "
            "db/migraciones/20261111_dominio_estado_vigencia.sql en DGODELIVERY.")
    return {nombre: [dict(f._mapping) for f in conn.execute(sql)] for nombre, sql in CONSULTAS.items()}

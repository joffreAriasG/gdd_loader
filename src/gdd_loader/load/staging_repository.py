"""Capa de persistencia: unico modulo que hace INSERT/TRUNCATE/DELETE. El
resto del proyecto no importa pyodbc ni escribe SQL directamente.
"""

from __future__ import annotations

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

ESTRATEGIAS_VALIDAS = ("TRUNCATE", "DOMINIO")


class StagingRepository:
    def __init__(self, engine: Engine):
        self._engine = engine

    def cargar(
        self,
        df: pd.DataFrame,
        tabla_staging: str,
        columna_clave: str,
        estrategia: str,
        columna_clave_prefijo_dominio: bool = False,
        codigo_dominio_archivo: str | None = None,
    ) -> int:
        """Carga un DataFrame a una tabla de staging. Retorna filas cargadas.

        estrategia:
          - "TRUNCATE": vacia TODA la tabla antes de insertar. Borra tambien
            otros dominios ya cargados ahi — usar solo para un reset manual
            completo, no para la operacion normal con multiples dominios.
          - "DOMINIO": borra las filas de staging del dominio
            `codigo_dominio_archivo` (obligatorio con esta estrategia), y
            despues inserta `df` completo (puede tener 0 filas -- ver mas
            abajo). Deja intactas las filas de otros dominios ya presentes
            en la misma tabla. Es el modo normal de operacion con multiples
            dominios. Como se decide QUE borrar depende de
            `columna_clave_prefijo_dominio`:

            - False (default, columna_clave="codigo_dominio" en la mayoria
              de las hojas): `DELETE ... WHERE columna_clave = codigo_dominio_archivo`.
            - True (columna_clave="codigo_dominio_atributo", ej.
              MetadataTecnica/PlanDeRemediacion): `DELETE ... WHERE
              columna_clave LIKE 'codigo_dominio_archivo-%'`.

            IMPORTANTE (2 bugs reales corregidos 2026-09-17, mismo sintoma):
            versiones anteriores acotaban el borrado a partir de los
            valores presentes en el propio `df` que se esta cargando (por
            igualdad exacta, o luego por prefijo derivado de esos mismos
            valores). Eso falla en cualquier caso donde una fila desaparece
            del archivo: (a) un valor puntual (ej. un codigo_dominio_atributo)
            que ya no aparece en NINGUNA fila del archivo nunca calzaba con
            el IN-list; (b) si la HOJA COMPLETA queda vacia (el usuario
            borro todas sus filas para probar), no hay ningun valor del que
            derivar el dominio a borrar y el DELETE nunca se ejecutaba. En
            ambos casos la fila vieja quedaba huerfana en staging para
            siempre y el merge nunca la detectaba como eliminada
            (activo=1, sin fecha_baja en gdd). El fix real: el dominio a
            borrar (`codigo_dominio_archivo`) SIEMPRE se determina aparte
            (ver pipeline/carga_staging.ejecutar, anclado en la hoja
            DetalleAtributos, que siempre tiene filas del dominio), nunca a
            partir del contenido de la hoja que se esta refrescando. Asi el
            borrado se ejecuta SIEMPRE que la estrategia sea DOMINIO, sin
            importar si `df` viene vacio, parcial o completo.
        """
        if estrategia not in ESTRATEGIAS_VALIDAS:
            raise ValueError(
                f"Estrategia de staging invalida: '{estrategia}' "
                f"(debe ser una de {ESTRATEGIAS_VALIDAS})"
            )

        schema, nombre = tabla_staging.split(".", 1)

        with self._engine.begin() as conn:
            if estrategia == "TRUNCATE":
                conn.exec_driver_sql(f"TRUNCATE TABLE {schema}.{nombre}")
            else:  # "DOMINIO"
                if not columna_clave:
                    raise ValueError(
                        f"Estrategia 'DOMINIO' requiere columna_clave para {tabla_staging} "
                        "(definir SheetConfig.columna_clave)"
                    )
                if not codigo_dominio_archivo:
                    raise ValueError(
                        f"Estrategia 'DOMINIO' requiere codigo_dominio_archivo para {tabla_staging} "
                        "-- no se puede acotar el borrado sin saber que dominio se esta cargando "
                        "(no se deriva del contenido de df a proposito, ver docstring)."
                    )
                if columna_clave_prefijo_dominio:
                    conn.execute(
                        text(f"DELETE FROM {schema}.{nombre} WHERE {columna_clave} LIKE :patron"),
                        {"patron": f"{codigo_dominio_archivo}-%"},
                    )
                else:
                    conn.execute(
                        text(f"DELETE FROM {schema}.{nombre} WHERE {columna_clave} = :dominio"),
                        {"dominio": codigo_dominio_archivo},
                    )

            df.to_sql(
                nombre,
                con=conn,
                schema=schema,
                if_exists="append",
                index=False,
                chunksize=500,
            )
        return len(df)

    def leer(self, tabla_staging: str, columna_clave: str, valor_clave: str) -> list[dict]:
        """Lee de staging solo las filas de un dominio puntual (columna_clave =
        valor_clave), para pasarlas al merge hacia `gdd`. No usa pandas aqui
        porque el resultado se consume como dicts, no como DataFrame.
        """
        schema, nombre = tabla_staging.split(".", 1)
        with self._engine.connect() as conn:
            filas = conn.execute(
                text(f"SELECT * FROM {schema}.{nombre} WHERE {columna_clave} = :valor"),
                {"valor": valor_clave},
            ).all()
            return [dict(fila._mapping) for fila in filas]

    def leer_por_prefijo(self, tabla_staging: str, columna: str, prefijo: str) -> list[dict]:
        """Como `leer`, pero filtra por columna LIKE 'prefijo-%' -- para
        staging.metadata_tecnica, cuya llave es codigo_dominio_atributo
        (ej. "ADS-7") y no trae una columna codigo_dominio propia.
        """
        schema, nombre = tabla_staging.split(".", 1)
        patron = f"{prefijo}-%"
        with self._engine.connect() as conn:
            filas = conn.execute(
                text(f"SELECT * FROM {schema}.{nombre} WHERE {columna} LIKE :patron"),
                {"patron": patron},
            ).all()
            return [dict(fila._mapping) for fila in filas]

    def dominios_distintos(self, tabla_staging: str, columna_clave: str) -> list[str]:
        """Todos los valores distintos de columna_clave presentes en staging
        (ej. todos los codigo_dominio cargados) -- para mergear "todos los
        dominios pendientes" sin que el CLI tenga que enumerarlos a mano.
        """
        schema, nombre = tabla_staging.split(".", 1)
        with self._engine.connect() as conn:
            filas = conn.execute(
                text(f"SELECT DISTINCT {columna_clave} FROM {schema}.{nombre} ORDER BY {columna_clave}")
            ).all()
            return [fila[0] for fila in filas]

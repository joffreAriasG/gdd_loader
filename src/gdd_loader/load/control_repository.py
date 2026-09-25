"""
Capa de persistencia del control de cargas y del registro de plantillas:
gdd.carga_control, gdd.carga_control_detalle, gdd.plantilla_version,
gdd.plantilla_version_columna.

Mismo criterio que gdd_repository.py: este modulo solo escribe/lee SQL; las
decisiones viven en domain/plantilla.py y pipeline/proceso_carga.py. Cada
escritura de control usa su PROPIA transaccion corta (engine.begin()), a
proposito separada de las transacciones de staging y merge: si el merge hace
rollback, el registro de la carga (y su estado de error) debe quedar igual.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import text
from sqlalchemy.engine import Engine

from gdd_loader.domain.plantilla import VersionPlantilla

# Columnas de gdd.carga_control que el loader puede escribir (lista blanca:
# los nombres de columna se interpolan en el SQL, los valores van como
# parametros).
_COLUMNAS_CARGA = {
    "codigo_dominio", "nombre_archivo_original", "ruta_archivo_archivado",
    "hash_archivo", "tamano_bytes", "id_plantilla", "version_plantilla",
    "origen_version", "hash_estructura_calculado", "id_carga_base", "id_envio",
    "version_loader", "equipo", "usuario_ejecucion", "subido_por",
    "origen_subido_por", "estado", "fecha_fin", "mensaje", "fecha_purga_archivo",
}

_COLUMNAS_DETALLE = (
    "filas", "insertadas", "actualizadas", "reemplazadas",
    "eliminadas", "omitidas", "sin_cambios", "error",
)


@dataclass(frozen=True)
class CargaAnterior:
    id_carga: int
    hash_archivo: str
    ruta_archivo: str | None = None


class ControlRepository:
    def __init__(self, engine: Engine):
        self._engine = engine

    # --- Registro de versiones ------------------------------------------------

    def _columnas_version(self, conn, id_plantilla: str, version: str) -> dict[str, list[str]]:
        filas = conn.execute(
            text(
                "SELECT hoja, columna FROM gdd.plantilla_version_columna "
                "WHERE id_plantilla = :p AND version = :v ORDER BY hoja, orden"
            ),
            {"p": id_plantilla, "v": version},
        ).all()
        columnas: dict[str, list[str]] = {}
        for hoja, columna in filas:
            columnas.setdefault(hoja, []).append(columna)
        return columnas

    def _a_version(self, conn, fila) -> VersionPlantilla:
        m = fila._mapping
        return VersionPlantilla(
            id_plantilla=m["id_plantilla"],
            version=m["version"],
            estado=m["estado"],
            hash_estructura=m["hash_estructura"].strip(),
            version_loader_minima=m["version_loader_minima"],
            columnas=self._columnas_version(conn, m["id_plantilla"], m["version"]),
            fecha_fin_gracia=m["fecha_fin_gracia"],
        )

    _SELECT_VERSION = (
        "SELECT id_plantilla, version, estado, hash_estructura, "
        "version_loader_minima, fecha_fin_gracia FROM gdd.plantilla_version "
    )

    def obtener_version(self, id_plantilla: str, version: str) -> VersionPlantilla | None:
        with self._engine.connect() as conn:
            fila = conn.execute(
                text(self._SELECT_VERSION + "WHERE id_plantilla = :p AND version = :v"),
                {"p": id_plantilla, "v": version},
            ).first()
            return self._a_version(conn, fila) if fila else None

    def versiones(self, id_plantilla: str) -> list[VersionPlantilla]:
        with self._engine.connect() as conn:
            filas = conn.execute(
                text(self._SELECT_VERSION + "WHERE id_plantilla = :p"), {"p": id_plantilla}
            ).all()
            return [self._a_version(conn, f) for f in filas]

    def registrar_version(
        self,
        version: VersionPlantilla,
        notas_cambio: str | None,
        registrado_por: str | None,
    ) -> None:
        """Inserta una version nueva como BORRADOR, con su contrato de
        columnas. Publicarla (VIGENTE) es un paso aparte: `publicar_version`."""
        with self._engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO gdd.plantilla_version (id_plantilla, version, estado, "
                    "hash_estructura, version_loader_minima, notas_cambio, registrado_por) "
                    "VALUES (:p, :v, 'BORRADOR', :h, :lm, :n, :r)"
                ),
                {
                    "p": version.id_plantilla, "v": version.version, "h": version.hash_estructura,
                    "lm": version.version_loader_minima, "n": notas_cambio, "r": registrado_por,
                },
            )
            for hoja, columnas in version.columnas.items():
                for orden, columna in enumerate(columnas, start=1):
                    conn.execute(
                        text(
                            "INSERT INTO gdd.plantilla_version_columna "
                            "(id_plantilla, version, hoja, orden, columna) "
                            "VALUES (:p, :v, :hoja, :orden, :col)"
                        ),
                        {"p": version.id_plantilla, "v": version.version,
                         "hoja": hoja, "orden": orden, "col": columna},
                    )

    def publicar_version(
        self,
        id_plantilla: str,
        version: str,
        estado_anterior: str,
        fecha_fin_gracia: date | None,
    ) -> str | None:
        """En UNA transaccion: la VIGENTE actual pasa a `estado_anterior`
        (DEPRECADA con fecha de gracia, o RETIRADA) y `version` pasa a VIGENTE.
        Retorna la version que estaba vigente (o None)."""
        if estado_anterior not in ("DEPRECADA", "RETIRADA"):
            raise ValueError("estado_anterior debe ser DEPRECADA o RETIRADA")
        with self._engine.begin() as conn:
            existe = conn.execute(
                text("SELECT estado FROM gdd.plantilla_version WHERE id_plantilla = :p AND version = :v"),
                {"p": id_plantilla, "v": version},
            ).first()
            if existe is None:
                raise ValueError(f"La version {version} de {id_plantilla} no esta registrada")
            anterior = conn.execute(
                text("SELECT version FROM gdd.plantilla_version "
                     "WHERE id_plantilla = :p AND estado = 'VIGENTE'"),
                {"p": id_plantilla},
            ).scalar()
            if anterior == version:
                return anterior
            if anterior is not None:
                conn.execute(
                    text("UPDATE gdd.plantilla_version SET estado = :e, fecha_fin_gracia = :g "
                         "WHERE id_plantilla = :p AND version = :v"),
                    {"e": estado_anterior,
                     "g": fecha_fin_gracia if estado_anterior == "DEPRECADA" else None,
                     "p": id_plantilla, "v": anterior},
                )
            conn.execute(
                text("UPDATE gdd.plantilla_version SET estado = 'VIGENTE', "
                     "fecha_publicacion = SYSDATETIME(), fecha_fin_gracia = NULL "
                     "WHERE id_plantilla = :p AND version = :v"),
                {"p": id_plantilla, "v": version},
            )
            return anterior

    # --- Control de cargas ------------------------------------------------------

    def iniciar_carga(self, **campos) -> int:
        self._validar_columnas(campos)
        columnas = ", ".join(campos)
        parametros = ", ".join(f":{c}" for c in campos)
        with self._engine.begin() as conn:
            return int(conn.execute(
                text(f"INSERT INTO gdd.carga_control ({columnas}) "
                     f"OUTPUT INSERTED.id_carga VALUES ({parametros})"),
                campos,
            ).scalar_one())

    def actualizar_carga(self, id_carga: int, **campos) -> None:
        if not campos:
            return
        self._validar_columnas(campos)
        asignaciones = ", ".join(f"{c} = :{c}" for c in campos)
        with self._engine.begin() as conn:
            conn.execute(
                text(f"UPDATE gdd.carga_control SET {asignaciones} WHERE id_carga = :id_carga"),
                {**campos, "id_carga": id_carga},
            )

    def registrar_detalle(self, id_carga: int, etapa: str, objeto: str, **conteos) -> None:
        desconocidas = set(conteos) - set(_COLUMNAS_DETALLE)
        if desconocidas:
            raise ValueError(f"Columnas de detalle desconocidas: {sorted(desconocidas)}")
        columnas = ["id_carga", "etapa", "objeto", *conteos]
        with self._engine.begin() as conn:
            conn.execute(
                text(f"INSERT INTO gdd.carga_control_detalle ({', '.join(columnas)}) "
                     f"VALUES ({', '.join(':' + c for c in columnas)})"),
                {"id_carga": id_carga, "etapa": etapa, "objeto": objeto, **conteos},
            )

    def ultima_carga_ok(self, codigo_dominio: str) -> CargaAnterior | None:
        with self._engine.connect() as conn:
            fila = conn.execute(
                text("SELECT TOP 1 id_carga, hash_archivo, ruta_archivo_archivado FROM gdd.carga_control "
                     "WHERE codigo_dominio = :d AND estado = 'MERGE_OK' ORDER BY id_carga DESC"),
                {"d": codigo_dominio},
            ).first()
            return CargaAnterior(int(fila[0]), fila[1].strip(), fila[2]) if fila else None

    def archivos_a_purgar(self, antes_de: datetime) -> list[tuple[int, str]]:
        with self._engine.connect() as conn:
            filas = conn.execute(
                text("SELECT id_carga, ruta_archivo_archivado FROM gdd.carga_control "
                     "WHERE fecha_purga_archivo IS NULL AND ruta_archivo_archivado IS NOT NULL "
                     "AND fecha_inicio < :antes "
                     # Fase 2: el archivo de la ULTIMA carga OK de cada dominio nunca se
                     # purga -- es la fuente del generador de plantillas precargadas.
                     "AND id_carga NOT IN (SELECT MAX(id_carga) FROM gdd.carga_control "
                     "WHERE estado = 'MERGE_OK' GROUP BY codigo_dominio) "
                     "ORDER BY id_carga"),
                {"antes": antes_de},
            ).all()
            return [(int(f[0]), f[1]) for f in filas]

    @staticmethod
    def _validar_columnas(campos: dict) -> None:
        desconocidas = set(campos) - _COLUMNAS_CARGA
        if desconocidas:
            raise ValueError(f"Columnas de carga_control desconocidas: {sorted(desconocidas)}")

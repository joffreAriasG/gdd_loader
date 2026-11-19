"""
Respaldo logico de los esquemas de gdd_loader (GDD-04), SOLO LECTURA.

Exporta cada tabla de los esquemas pedidos (por defecto gdd y staging) a un
CSV dentro de UN zip, con un manifiesto (manifiesto.json) que guarda, por
tabla: columnas y tipos, filas leidas y SHA-256 del CSV. No necesita permiso
de BACKUP: solo SELECT y ver la definicion de las tablas.

Formato del CSV (UTF-8, coma, primera fila = nombres de columna):
  - NULL           -> campo vacio SIN comillas
  - texto          -> siempre entre comillas ("" = texto vacio, distinto de NULL)
  - numeros        -> sin comillas; Decimal con su precision exacta
  - fechas/horas   -> ISO 8601 entre comillas
  - binarios       -> hexadecimal "0x..." entre comillas

`verificar_respaldo` relee el zip y compara SHA-256 y filas con el manifiesto.
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import json
import zipfile
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Iterable, Iterator, Sequence

from sqlalchemy import text

ESQUEMAS_POR_DEFECTO = ("gdd", "staging")
NOMBRE_MANIFIESTO = "manifiesto.json"
FORMATO = "gdd_loader-respaldo-csv/1"
_LOTE = 5000


@dataclass
class Columna:
    nombre: str
    tipo: str
    nulable: bool


@dataclass
class TablaRespaldo:
    esquema: str
    tabla: str
    archivo: str
    columnas: list[Columna]
    filas: int = 0
    filas_bd: int | None = None  # COUNT(*) leido al terminar, para comparar
    sha256: str = ""
    bytes: int = 0


@dataclass
class Manifiesto:
    formato: str
    version_loader: str
    servidor: str
    base_datos: str
    fecha: str
    esquemas: list[str]
    tablas: list[TablaRespaldo] = field(default_factory=list)

    @property
    def total_filas(self) -> int:
        return sum(t.filas for t in self.tablas)


def _campo(valor) -> str:
    """Un campo del CSV: NULL vacio, numeros tal cual, todo lo demas entre comillas."""
    if valor is None:
        return ""
    if isinstance(valor, _Numero):
        return valor.texto
    if isinstance(valor, (int, float)) and not isinstance(valor, bool):
        return repr(valor)
    return '"' + str(valor).replace('"', '""') + '"'


class _CsvHash:
    """Escribe lineas CSV en un stream binario y va calculando SHA-256 y bytes.
    El CSV se arma a mano (no con el modulo csv) porque csv no distingue NULL de
    texto vacio en todas las versiones de Python."""

    def __init__(self, destino):
        self._destino = destino
        self._sha = hashlib.sha256()
        self.bytes = 0

    def fila(self, valores: Sequence) -> None:
        datos = (",".join(_campo(v) for v in valores) + "\n").encode("utf-8")
        self._sha.update(datos)
        self.bytes += len(datos)
        self._destino.write(datos)

    @property
    def sha256(self) -> str:
        return self._sha.hexdigest()


@dataclass(frozen=True)
class _Numero:
    """Numero que se escribe sin comillas con su texto exacto (Decimal)."""
    texto: str


def formatear_valor(valor):
    """Convierte un valor de pyodbc a lo que se escribe en el CSV (ver docstring del modulo)."""
    if valor is None:
        return None
    if isinstance(valor, bool):
        return 1 if valor else 0
    if isinstance(valor, (int, float)):
        return valor
    if isinstance(valor, Decimal):
        return _Numero(format(valor, "f"))
    if isinstance(valor, (dt.datetime, dt.date, dt.time)):
        return valor.isoformat()
    if isinstance(valor, (bytes, bytearray, memoryview)):
        return "0x" + bytes(valor).hex().upper()
    return str(valor)


def _q(nombre: str) -> str:
    return "[" + nombre.replace("]", "]]") + "]"


def listar_tablas(conn, esquemas: Sequence[str]) -> list[tuple[str, str]]:
    """(esquema, tabla) de las tablas de usuario de esos esquemas, ordenadas."""
    params = {f"e{i}": e for i, e in enumerate(esquemas)}
    marcas = ", ".join(f":e{i}" for i in range(len(esquemas)))
    filas = conn.execute(text(
        "SELECT s.name, t.name FROM sys.tables t "
        "JOIN sys.schemas s ON s.schema_id = t.schema_id "
        f"WHERE t.is_ms_shipped = 0 AND s.name IN ({marcas}) "
        "ORDER BY s.name, t.name"
    ), params).fetchall()
    return [(f[0], f[1]) for f in filas]


def columnas_tabla(conn, esquema: str, tabla: str) -> list[Columna]:
    filas = conn.execute(text(
        "SELECT c.name, TYPE_NAME(c.user_type_id), c.max_length, c.precision, c.scale, c.is_nullable "
        "FROM sys.columns c WHERE c.object_id = OBJECT_ID(:obj) ORDER BY c.column_id"
    ), {"obj": f"{_q(esquema)}.{_q(tabla)}"}).fetchall()
    columnas = []
    for nombre, tipo, largo, precision, escala, nulable in filas:
        if tipo in ("varchar", "char", "varbinary", "binary"):
            tipo = f"{tipo}({'max' if largo == -1 else largo})"
        elif tipo in ("nvarchar", "nchar"):
            tipo = f"{tipo}({'max' if largo == -1 else largo // 2})"
        elif tipo in ("decimal", "numeric"):
            tipo = f"{tipo}({precision},{escala})"
        columnas.append(Columna(nombre, tipo, bool(nulable)))
    return columnas


def leer_filas(conn, esquema: str, tabla: str, columnas: Sequence[Columna]) -> Iterator[tuple]:
    lista = ", ".join(_q(c.nombre) for c in columnas)
    resultado = conn.execute(text(f"SELECT {lista} FROM {_q(esquema)}.{_q(tabla)}"))
    while True:
        lote = resultado.fetchmany(_LOTE)
        if not lote:
            break
        yield from lote


def contar_filas(conn, esquema: str, tabla: str) -> int:
    return int(conn.execute(text(f"SELECT COUNT_BIG(*) FROM {_q(esquema)}.{_q(tabla)}")).scalar())


def escribir_respaldo(
    ruta_zip: Path,
    manifiesto: Manifiesto,
    tablas: Iterable[tuple[str, str, list[Columna], Iterable[Sequence], "callable | None"]],
) -> Manifiesto:
    """Escribe el zip. `tablas` da (esquema, tabla, columnas, filas, contar) donde
    `contar()` devuelve el COUNT(*) al terminar (o None para no comparar).
    Escribe en <ruta>.parcial y lo renombra al final: un zip con el nombre final
    siempre esta completo."""
    parcial = ruta_zip.with_name(ruta_zip.name + ".parcial")
    with zipfile.ZipFile(parcial, "w", zipfile.ZIP_DEFLATED) as zf:
        for esquema, tabla, columnas, filas, contar in tablas:
            archivo = f"{esquema}/{tabla}.csv"
            info = TablaRespaldo(esquema, tabla, archivo, list(columnas))
            with zf.open(archivo, "w", force_zip64=True) as destino:
                csvh = _CsvHash(destino)
                csvh.fila([c.nombre for c in columnas])
                for fila in filas:
                    csvh.fila([formatear_valor(v) for v in fila])
                    info.filas += 1
            info.sha256, info.bytes = csvh.sha256, csvh.bytes
            info.filas_bd = contar() if contar else None
            manifiesto.tablas.append(info)
        zf.writestr(NOMBRE_MANIFIESTO, json.dumps(asdict(manifiesto), ensure_ascii=False, indent=2))
    parcial.replace(ruta_zip)
    return manifiesto


def respaldar(engine, destino: Path, *, version_loader: str, servidor: str, base_datos: str,
              esquemas: Sequence[str] = ESQUEMAS_POR_DEFECTO, ahora: dt.datetime | None = None) -> tuple[Path, Manifiesto]:
    """Respalda los esquemas en destino/respaldo_<base>_<AAAAMMDD_HHMMSS>.zip. Solo SELECT."""
    ahora = ahora or dt.datetime.now()
    destino.mkdir(parents=True, exist_ok=True)
    ruta = destino / f"respaldo_{base_datos}_{ahora:%Y%m%d_%H%M%S}.zip"
    if ruta.exists():
        raise FileExistsError(f"Ya existe {ruta}")
    manifiesto = Manifiesto(FORMATO, version_loader, servidor, base_datos,
                            ahora.isoformat(timespec="seconds"), list(esquemas))
    with engine.connect() as conn:
        lista = listar_tablas(conn, esquemas)
        if not lista:
            raise ValueError(f"No hay tablas en los esquemas {', '.join(esquemas)} (o no hay permiso para verlas)")

        def generar():
            for esquema, tabla in lista:
                columnas = columnas_tabla(conn, esquema, tabla)
                yield (esquema, tabla, columnas, leer_filas(conn, esquema, tabla, columnas),
                       lambda e=esquema, t=tabla: contar_filas(conn, e, t))

        escribir_respaldo(ruta, manifiesto, generar())
        conn.rollback()  # solo lectura: no hay nada que confirmar
    return ruta, manifiesto


def verificar_respaldo(ruta_zip: Path) -> list[str]:
    """Relee el zip y devuelve la lista de problemas (vacia = respaldo integro)."""
    problemas: list[str] = []
    with zipfile.ZipFile(ruta_zip) as zf:
        danado = zf.testzip()
        if danado is not None:
            problemas.append(f"zip danado: {danado}")
        try:
            datos = json.loads(zf.read(NOMBRE_MANIFIESTO).decode("utf-8"))
        except KeyError:
            return [f"falta {NOMBRE_MANIFIESTO}"]
        for t in datos["tablas"]:
            try:
                contenido = zf.read(t["archivo"])
            except KeyError:
                problemas.append(f"falta {t['archivo']}")
                continue
            if hashlib.sha256(contenido).hexdigest() != t["sha256"]:
                problemas.append(f"{t['archivo']}: SHA-256 no coincide")
            lineas = sum(1 for _ in csv.reader(io.StringIO(contenido.decode("utf-8"))))
            if lineas - 1 != t["filas"]:
                problemas.append(f"{t['archivo']}: {lineas - 1} filas en el CSV, manifiesto dice {t['filas']}")
            if t.get("filas_bd") is not None and t["filas_bd"] != t["filas"]:
                problemas.append(f"{t['archivo']}: se leyeron {t['filas']} filas pero la tabla tenia "
                                 f"{t['filas_bd']} al terminar (hubo una carga a la vez?)")
    return problemas

"""
Manejo de archivos en las carpetas sincronizadas de SharePoint:

    Entrada  --(procesado OK)-->     Archivo      (solo gobierno de datos)
             --(rechazado/error)-->  Rechazados   (solo gobierno de datos)

El nombre con el que llega el archivo es libre (no estandar), asi que nunca
se usa para decidir nada: al archivarlo se renombra a
`{dominio}_{id_carga}_{hash8}.xlsx`.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path


def listar_pendientes(carpeta: Path) -> list[Path]:
    """.xlsx de la carpeta de entrada, sin los archivos de bloqueo de Excel
    (~$archivo.xlsx) y sin subcarpetas."""
    return sorted(
        p for p in carpeta.glob("*.xlsx")
        if p.is_file() and not p.name.startswith("~$")
    )


def archivo_estable(archivo: Path, segundos: int, ahora: float | None = None) -> bool:
    """True si el archivo no se modifico en los ultimos `segundos` y tiene
    contenido. Evita procesar un archivo que la sincronizacion de OneDrive
    todavia esta escribiendo, o que alguien esta guardando en este momento."""
    try:
        st = archivo.stat()
    except FileNotFoundError:
        return False
    if st.st_size == 0:
        return False
    ahora = time.time() if ahora is None else ahora
    return (ahora - st.st_mtime) >= segundos


def nombre_archivado(codigo_dominio: str | None, id_carga: int, hash_archivo: str) -> str:
    dominio = (codigo_dominio or "SIN_DOMINIO").strip() or "SIN_DOMINIO"
    return f"{dominio}_{id_carga:06d}_{hash_archivo[:8]}.xlsx"


def mover(archivo: Path, carpeta_destino: Path, nombre: str) -> Path:
    """Mueve `archivo` a `carpeta_destino/nombre`. Si ya existe un archivo con
    ese nombre (no deberia: id_carga es unico), agrega un sufijo en vez de
    sobrescribir."""
    carpeta_destino.mkdir(parents=True, exist_ok=True)
    destino = carpeta_destino / nombre
    n = 1
    while destino.exists():
        destino = carpeta_destino / f"{Path(nombre).stem}_{n}{Path(nombre).suffix}"
        n += 1
    shutil.move(str(archivo), str(destino))
    return destino

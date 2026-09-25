"""
Capa de dominio: identidad y version de la plantilla Excel.

Tres conceptos, sin SQL ni lectura de archivos (eso vive en extract/ y load/):

- ControlPlantilla: lo que el archivo DECLARA en su hoja oculta `_Control`
  (id_plantilla, version_plantilla, ...). Es informacion declarada, no se
  confia en ella sola.
- VersionPlantilla: lo que el REGISTRO central (gdd.plantilla_version*) dice
  de una version: estado, contrato de columnas, huella esperada, version
  minima del loader.
- Huella de estructura (hash_estructura): SHA-256 calculado sobre las hojas
  del contrato y sus encabezados reales. Detecta un archivo cuya estructura
  no coincide con la version que declara (columnas agregadas, renombradas,
  movidas, hojas faltantes).

`evaluar_version` junta todo y decide si un archivo se acepta o se rechaza,
con un estado de rechazo explicito para gdd.carga_control.
"""

from __future__ import annotations

import hashlib
import unicodedata
from dataclasses import dataclass, field
from datetime import date

HOJA_CONTROL = "_Control"

ESTADOS_VERSION = ("BORRADOR", "VIGENTE", "DEPRECADA", "RETIRADA")

# Estados de rechazo (deben coincidir con ck_cc_estado en el DDL)
RECHAZADA_SIN_CONTROL = "RECHAZADA_SIN_CONTROL"
RECHAZADA_VERSION = "RECHAZADA_VERSION"
RECHAZADA_ESTRUCTURA = "RECHAZADA_ESTRUCTURA"
RECHAZADA_LOADER = "RECHAZADA_LOADER"


@dataclass(frozen=True)
class ControlPlantilla:
    id_plantilla: str | None
    version_plantilla: str | None
    codigo_dominio: str | None = None
    id_carga_base: int | None = None  # Fase 2
    id_envio: str | None = None  # Fase 2

    @classmethod
    def desde_dict(cls, valores: dict[str, str | None]) -> "ControlPlantilla":
        def txt(clave: str) -> str | None:
            v = valores.get(clave)
            v = None if v is None else str(v).strip()
            return v or None

        id_carga_base = txt("id_carga_base")
        return cls(
            id_plantilla=txt("id_plantilla"),
            version_plantilla=txt("version_plantilla"),
            codigo_dominio=txt("codigo_dominio"),
            id_carga_base=int(id_carga_base) if id_carga_base and id_carga_base.isdigit() else None,
            id_envio=txt("id_envio"),
        )

    @property
    def completo(self) -> bool:
        return bool(self.id_plantilla and self.version_plantilla)


@dataclass(frozen=True)
class VersionPlantilla:
    id_plantilla: str
    version: str
    estado: str
    hash_estructura: str
    version_loader_minima: str
    # hoja -> encabezados en orden (contrato estructural de la version)
    columnas: dict[str, list[str]] = field(default_factory=dict)
    fecha_fin_gracia: date | None = None


@dataclass
class Decision:
    aceptada: bool
    estado_rechazo: str | None = None
    mensaje: str = ""
    advertencias: list[str] = field(default_factory=list)


# --- Huella de estructura ---------------------------------------------------


def normalizar_encabezado(valor: object) -> str:
    """Encabezado tal como lo compara el loader: texto, sin espacios en los
    extremos y en forma Unicode NFC (una tilde puede venir compuesta o
    descompuesta segun quien guardo el archivo; sin esto "raíz" != "raíz")."""
    if valor is None:
        return ""
    return unicodedata.normalize("NFC", str(valor).strip())


def limpiar_encabezados(valores: list[object]) -> list[str]:
    """Normaliza y quita las celdas vacias al FINAL de la fila de encabezado
    (Excel suele reportar columnas vacias con formato como parte de la hoja).
    Un vacio EN MEDIO se conserva: es un cambio real de estructura."""
    cols = [normalizar_encabezado(v) for v in valores]
    while cols and cols[-1] == "":
        cols.pop()
    return cols


def calcular_hash_estructura(estructura: dict[str, list[str]]) -> str:
    """SHA-256 de las hojas (ordenadas por nombre) y sus encabezados (en el
    orden de la hoja). El orden de las PESTANAS no importa; el orden de las
    COLUMNAS si (mover una columna es un cambio de estructura)."""
    lineas = []
    for hoja in sorted(estructura, key=normalizar_encabezado):
        cols = [normalizar_encabezado(c) for c in estructura[hoja]]
        lineas.append(normalizar_encabezado(hoja) + "\t" + "|".join(cols))
    return hashlib.sha256("\n".join(lineas).encode("utf-8")).hexdigest()


def restringir_a_contrato(
    estructura_archivo: dict[str, list[str]], hojas_contrato
) -> dict[str, list[str]]:
    """Deja solo las hojas que forman parte del contrato (las auxiliares, como
    'Lista De Referencia', pueden cambiar libremente sin afectar la huella).
    Una hoja del contrato que falta en el archivo simplemente no aparece -- la
    huella entonces no coincide, que es el comportamiento buscado."""
    return {h: estructura_archivo[h] for h in hojas_contrato if h in estructura_archivo}


def diferencias_estructura(
    esperada: dict[str, list[str]], real: dict[str, list[str]]
) -> list[str]:
    """Explicacion legible de por que no coincide la huella (para el mensaje
    de rechazo). No decide nada, solo describe."""
    difs: list[str] = []
    for hoja in sorted(esperada):
        if hoja not in real:
            difs.append(f"falta la hoja '{hoja}'")
            continue
        esp, act = esperada[hoja], real[hoja]
        faltan = [c for c in esp if c not in act]
        sobran = [c for c in act if c not in esp]
        if faltan:
            difs.append(f"hoja '{hoja}': faltan columnas {faltan}")
        if sobran:
            difs.append(f"hoja '{hoja}': columnas no esperadas {sobran}")
        if not faltan and not sobran and esp != act:
            difs.append(f"hoja '{hoja}': mismas columnas en distinto orden")
    return difs


# --- Versiones ----------------------------------------------------------------


def parsear_version(version: str) -> tuple[int, int, int]:
    partes = str(version).strip().split(".")
    if len(partes) != 3 or not all(p.isdigit() for p in partes):
        raise ValueError(f"Version invalida '{version}': se espera MAJOR.MINOR.PATCH (ej. 1.2.0)")
    return int(partes[0]), int(partes[1]), int(partes[2])


def version_minima_cumplida(version_loader: str, version_minima: str) -> bool:
    return parsear_version(version_loader) >= parsear_version(version_minima)


def identificar_por_huella(
    estructura_archivo: dict[str, list[str]], versiones: list[VersionPlantilla]
) -> VersionPlantilla | None:
    """Transicion (archivos sin hoja _Control): busca la version registrada
    cuya huella coincide con la del archivo. Prioriza VIGENTE, luego la mas
    reciente. BORRADOR nunca se considera."""
    candidatas = []
    for v in versiones:
        if v.estado == "BORRADOR":
            continue
        real = restringir_a_contrato(estructura_archivo, v.columnas)
        if calcular_hash_estructura(real) == v.hash_estructura:
            candidatas.append(v)
    if not candidatas:
        return None
    candidatas.sort(key=lambda v: (v.estado == "VIGENTE", parsear_version(v.version)), reverse=True)
    return candidatas[0]


def evaluar_version(
    version: VersionPlantilla | None,
    estructura_archivo: dict[str, list[str]],
    version_loader: str,
    hoy: date,
    version_declarada: str | None = None,
) -> Decision:
    """Reglas, en orden (la primera que falla decide el rechazo):

    1. La version existe en el registro y no es BORRADOR ni RETIRADA.
    2. Si esta DEPRECADA, todavia esta dentro de fecha_fin_gracia (se acepta
       con advertencia).
    3. Este loader cumple version_loader_minima (evita que un equipo con
       codigo viejo procese una plantilla que no sabe leer completa).
    4. La huella calculada del archivo coincide con la del registro.
    """
    etiqueta = version_declarada or (version.version if version else "?")

    if version is None:
        return Decision(False, RECHAZADA_VERSION,
                        f"La version de plantilla '{etiqueta}' no esta registrada.")

    if version.estado == "BORRADOR":
        return Decision(False, RECHAZADA_VERSION,
                        f"La version {version.version} todavia no esta publicada (BORRADOR).")
    if version.estado == "RETIRADA":
        return Decision(False, RECHAZADA_VERSION,
                        f"La version {version.version} fue retirada. Descargue la plantilla vigente.")

    advertencias: list[str] = []
    if version.estado == "DEPRECADA":
        if version.fecha_fin_gracia is None or hoy > version.fecha_fin_gracia:
            return Decision(False, RECHAZADA_VERSION,
                            f"La version {version.version} esta deprecada y su periodo de gracia "
                            f"termino ({version.fecha_fin_gracia}). Descargue la plantilla vigente.")
        advertencias.append(
            f"Version {version.version} deprecada; se acepta hasta {version.fecha_fin_gracia}."
        )

    if not version_minima_cumplida(version_loader, version.version_loader_minima):
        return Decision(False, RECHAZADA_LOADER,
                        f"La plantilla {version.version} requiere gdd_loader >= "
                        f"{version.version_loader_minima}; este equipo tiene {version_loader}. "
                        "Actualice el codigo (git pull) antes de cargar.")

    if calcular_hash_estructura(version.columnas) != version.hash_estructura:
        return Decision(False, RECHAZADA_VERSION,
                        f"Registro inconsistente: el hash_estructura de la version {version.version} "
                        "no coincide con su contrato de columnas. Revisar gdd.plantilla_version.")

    real = restringir_a_contrato(estructura_archivo, version.columnas)
    if calcular_hash_estructura(real) != version.hash_estructura:
        difs = diferencias_estructura(version.columnas, real)
        detalle = "; ".join(difs) if difs else "diferencias en encabezados"
        return Decision(False, RECHAZADA_ESTRUCTURA,
                        f"La estructura del archivo no coincide con la version {version.version}: {detalle}.")

    return Decision(True, None, f"Plantilla {version.id_plantilla} {version.version} valida.", advertencias)

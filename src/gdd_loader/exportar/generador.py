"""
Generador de plantilla precargada (Fase 2).

    molde VIGENTE (carpeta de moldes)  +  datos de la ultima MERGE_OK del dominio
        -> {dominio}_v{version}_c{id_carga_base}.xlsx con hoja _Control:
           id_plantilla, version_plantilla, codigo_dominio, id_carga_base,
           id_envio (GUID), fecha_generacion

El molde se identifica por su HUELLA (no por nombre de archivo): se usa el
.xlsx de la carpeta de moldes cuya estructura coincide con la version VIGENTE
registrada. Como la plantilla siempre sale del molde vigente, un cambio de
version de plantilla se aplica solo con volver a generar: los datos pasan al
molde nuevo por nombre de columna (ver fuente_datos.alinear_a_molde).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from gdd_loader.domain import plantilla as pl
from gdd_loader.domain.sheet_config import SheetConfig
from gdd_loader.exportar.escritor_molde import llenar_molde
from gdd_loader.exportar.fuente_datos import (
    FuenteNoDisponibleError,
    alinear_a_molde,
    datos_desde_archivo,
    datos_desde_staging,
)
from gdd_loader.exportar.listas_referencia import HOJA_ERRORES, construir_listas, leer_listas_molde
from gdd_loader.extract.control_reader import leer_control, leer_estructura

logger = logging.getLogger("gdd_loader.exportar")


class GeneracionError(Exception):
    pass


@dataclass
class ContextoGenerador:
    control_repo: object
    staging_repo: object
    hojas: list[SheetConfig]
    id_plantilla: str
    carpeta_moldes: Path
    carpeta_salida: Path
    modo_listas: str = "MOLDE"  # MOLDE | BD (ver exportar/listas_referencia.py)
    listas_repo: object = None


@dataclass
class ResultadoGeneracion:
    ruta: Path
    codigo_dominio: str
    version_plantilla: str
    id_carga_base: int
    fuente: str  # ARCHIVO | STAGING
    id_envio: str
    filas_por_hoja: dict[str, int]
    advertencias: list[str] = field(default_factory=list)


def version_vigente(control_repo, id_plantilla: str) -> pl.VersionPlantilla:
    vigente = next((v for v in control_repo.versiones(id_plantilla) if v.estado == "VIGENTE"), None)
    if vigente is None:
        raise GeneracionError(f"No hay version VIGENTE de {id_plantilla} en gdd.plantilla_version")
    return vigente


HOJA_ANCLA = "DetalleAtributos"


def _sin_datos(archivo: Path, hojas) -> bool:
    """True si el archivo es un molde en blanco: sin atributos en la hoja
    ancla (DetalleAtributos). Una plantilla llenada tiene la misma huella que
    el molde, asi que la huella sola no alcanza para distinguirlos. No se
    exige que TODAS las hojas esten vacias: el molde real trae filas
    predefinidas (p. ej. los roles de Estructura)."""
    ancla = [HOJA_ANCLA] if HOJA_ANCLA in hojas else list(hojas)
    return all(len(filas) == 0 for filas in datos_desde_archivo(archivo, ancla).values())


def buscar_molde(carpeta: Path, version: pl.VersionPlantilla) -> Path:
    """El .xlsx de `carpeta` que (1) tiene la huella de `version` y (2) esta
    en blanco. Si hay mas de uno, se prefiere el que declara esa version en su
    hoja _Control; si sigue habiendo ambiguedad, se detiene."""
    candidatos = sorted(p for p in carpeta.glob("*.xlsx") if not p.name.startswith("~$"))
    moldes = []
    for p in candidatos:
        try:
            real = pl.restringir_a_contrato(leer_estructura(p), version.columnas)
            if pl.calcular_hash_estructura(real) == version.hash_estructura and _sin_datos(p, version.columnas):
                moldes.append(p)
        except Exception:  # noqa: BLE001 - archivo ilegible: no es el molde
            continue
    if len(moldes) > 1:
        declarados = []
        for p in moldes:
            valores = leer_control(p) or {}
            if valores.get("version_plantilla") == version.version:
                declarados.append(p)
        if len(declarados) == 1:
            return declarados[0]
        raise GeneracionError(
            f"Hay {len(moldes)} moldes en blanco para la version {version.version} en {carpeta}: "
            f"{[p.name for p in moldes]}. Deje solo el molde publicado."
        )
    if not moldes:
        raise GeneracionError(
            f"Ningun .xlsx en blanco de {carpeta} coincide con la estructura de la version vigente "
            f"{version.version} ({len(candidatos)} revisados). Copie ahi el molde publicado."
        )
    return moldes[0]


def generar_plantilla(codigo_dominio: str, ctx: ContextoGenerador,
                      ahora: datetime | None = None) -> ResultadoGeneracion:
    ahora = ahora or datetime.now()
    vigente = version_vigente(ctx.control_repo, ctx.id_plantilla)
    molde = buscar_molde(ctx.carpeta_moldes, vigente)

    ultima = ctx.control_repo.ultima_carga_ok(codigo_dominio)
    if ultima is None:
        raise GeneracionError(
            f"El dominio {codigo_dominio} no tiene ninguna carga MERGE_OK: no hay base para generar. "
            "La primera carga de un dominio se hace sobre la plantilla en blanco."
        )

    hojas_contrato = list(vigente.columnas)
    advertencias: list[str] = []
    try:
        if not ultima.ruta_archivo:
            raise FuenteNoDisponibleError("la carga no tiene archivo archivado")
        datos = datos_desde_archivo(Path(ultima.ruta_archivo), hojas_contrato)
        fuente = "ARCHIVO"
    except FuenteNoDisponibleError as exc:
        logger.warning("Carga %d: archivo no disponible (%s); se intenta desde staging.", ultima.id_carga, exc)
        try:
            datos = datos_desde_staging(
                ctx.staging_repo, [c for c in ctx.hojas if c.nombre_hoja in hojas_contrato],
                codigo_dominio, ultima.id_carga)
        except FuenteNoDisponibleError as exc2:
            raise GeneracionError(
                f"No se puede reconstruir la carga {ultima.id_carga} de {codigo_dominio}: "
                f"archivo ({exc}) y staging ({exc2}) no disponibles."
            ) from exc2
        fuente = "STAGING"
        advertencias.append("Generada desde staging: el orden de las filas puede diferir del original.")

    estructura_molde = leer_estructura(molde)
    encabezados = pl.restringir_a_contrato(estructura_molde, hojas_contrato)
    filas, avisos = alinear_a_molde(datos, encabezados)
    advertencias.extend(avisos)

    # Hojas auxiliares: el reporte de errores de los Office Scripts siempre
    # sale vacio; las listas de referencia salen de la BD solo en modo BD.
    if HOJA_ERRORES in estructura_molde:
        filas[HOJA_ERRORES] = []
    if ctx.modo_listas == "BD":
        if ctx.listas_repo is None:
            raise GeneracionError("Modo de listas BD sin repositorio de listas configurado")
        listas, avisos_listas = construir_listas(leer_listas_molde(molde), ctx.listas_repo)
        filas.update({h: f for h, f in listas.items() if h in estructura_molde})
        advertencias.extend(avisos_listas)

    id_envio = str(uuid.uuid4())
    control = {
        "id_plantilla": vigente.id_plantilla,
        "version_plantilla": vigente.version,
        "codigo_dominio": codigo_dominio,
        "id_carga_base": str(ultima.id_carga),
        "id_envio": id_envio,
        "fecha_generacion": ahora.strftime("%Y-%m-%d %H:%M:%S"),
    }
    destino = ctx.carpeta_salida / f"{codigo_dominio}_v{vigente.version}_c{ultima.id_carga:06d}.xlsx"
    resultado = llenar_molde(molde, destino, filas, control)
    advertencias.extend(resultado.advertencias)

    # Control de calidad: lo generado debe tener la huella de la version vigente.
    real = pl.restringir_a_contrato(leer_estructura(destino), vigente.columnas)
    if pl.calcular_hash_estructura(real) != vigente.hash_estructura:
        destino.unlink(missing_ok=True)
        raise GeneracionError("La plantilla generada no coincide con la huella de la version vigente (no se publica).")

    return ResultadoGeneracion(destino, codigo_dominio, vigente.version, ultima.id_carga, fuente,
                               id_envio, resultado.filas_por_hoja, advertencias)

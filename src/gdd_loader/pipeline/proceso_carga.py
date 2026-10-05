"""
Orquestacion de UNA carga completa (Fase 1): un archivo de la carpeta de
Entrada -> validacion de plantilla -> staging -> merge gdd -> archivo.

Todo queda registrado bajo un mismo id_carga en gdd.carga_control:

    EN_PROCESO
      -> RECHAZADA_SIN_CONTROL | RECHAZADA_VERSION | RECHAZADA_ESTRUCTURA
         | RECHAZADA_LOADER | RECHAZADA_DOMINIO
         | RECHAZADA_DESACTUALIZADA                        (no toca staging)
      -> OMITIDA_SIN_CAMBIOS    (mismo archivo que la ultima carga OK del dominio)
      -> ERROR_STAGING          (alguna hoja fallo; NO se corre el merge)
      -> MERGE_OK | MERGE_PARCIAL | ERROR_MERGE
      -> ERROR                  (excepcion no prevista)

Destino del archivo: MERGE_OK y OMITIDA_SIN_CAMBIOS -> carpeta Archivo;
cualquier otro estado -> carpeta Rechazados. En ambos casos se renombra a
`{dominio}_{id_carga}_{hash8}.xlsx` y la ruta queda en carga_control.

Todas las dependencias se inyectan (ContextoCarga) para poder probar la
orquestacion completa sin SQL Server ni carpetas reales.

Notificacion (v0.5.0, opcional: ContextoCarga.notificador): antes y despues
del merge se toma una foto legible del dominio; la diferencia queda en
ResultadoCarga.cambios. Al final de cada archivo (ya archivado) se deja el
evento para el correo. Ninguna falla de notificacion cambia el estado de la
carga: solo se registra como advertencia en el log.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Callable

from gdd_loader.domain import plantilla as pl
from gdd_loader.domain.sheet_config import SheetConfig
from gdd_loader.notificacion.diferencias import CambiosEntidad, comparar
from gdd_loader.extract.control_reader import (
    hash_archivo,
    leer_control,
    leer_estructura,
    leer_modificado_por,
)
from gdd_loader.pipeline.archivos import mover, nombre_archivado
from gdd_loader.pipeline.carga_staging import _codigo_dominio_de_hoja, ejecutar

logger = logging.getLogger("gdd_loader.pipeline.proceso")

EN_PROCESO = "EN_PROCESO"
RECHAZADA_DOMINIO = "RECHAZADA_DOMINIO"
RECHAZADA_DESACTUALIZADA = "RECHAZADA_DESACTUALIZADA"
OMITIDA_SIN_CAMBIOS = "OMITIDA_SIN_CAMBIOS"
ERROR_STAGING = "ERROR_STAGING"
ERROR_MERGE = "ERROR_MERGE"
MERGE_PARCIAL = "MERGE_PARCIAL"
MERGE_OK = "MERGE_OK"
ERROR = "ERROR"

ESTADOS_EXITOSOS = (MERGE_OK, OMITIDA_SIN_CAMBIOS)


@dataclass
class ContextoCarga:
    control_repo: object
    staging_repo: object
    engine: object
    hojas: list[SheetConfig]
    carpeta_archivo: Path
    carpeta_rechazados: Path
    id_plantilla: str
    exigir_control: bool
    version_loader: str
    equipo: str
    usuario_ejecucion: str
    # (engine, staging_repo, codigo_dominio, id_carga) -> list[ResumenMerge]
    funcion_merge: Callable
    estrategia_staging: str = "DOMINIO"
    hoy: Callable[[], date] = date.today
    # Opcional (notificacion/evento.Notificador): instantanea(dominio) y
    # notificar(resultado). None = sin notificacion (comportamiento anterior).
    notificador: object | None = None


@dataclass
class ResultadoCarga:
    archivo_original: str
    id_carga: int | None
    estado: str
    codigo_dominio: str | None = None
    mensaje: str = ""
    advertencias: list[str] = field(default_factory=list)
    ruta_final: Path | None = None
    # --- datos para la notificacion ---
    version_plantilla: str | None = None
    subido_por: str | None = None
    cambios: list[CambiosEntidad] | None = None   # None = no hubo merge o no se pudo calcular
    motivo_sin_detalle: str | None = None
    ruta_notificacion: Path | None = None

    @property
    def exitosa(self) -> bool:
        return self.estado in ESTADOS_EXITOSOS


class _Rechazo(Exception):
    def __init__(self, estado: str, mensaje: str):
        super().__init__(mensaje)
        self.estado = estado
        self.mensaje = mensaje


def _identificar_version(ctx: ContextoCarga, control: pl.ControlPlantilla | None,
                         estructura: dict[str, list[str]], advertencias: list[str]):
    """Devuelve (VersionPlantilla, origen_version, version_declarada)."""
    if control is not None and control.completo:
        if control.id_plantilla != ctx.id_plantilla:
            raise _Rechazo(pl.RECHAZADA_VERSION,
                           f"La hoja _Control declara la plantilla '{control.id_plantilla}', "
                           f"se esperaba '{ctx.id_plantilla}'.")
        version = ctx.control_repo.obtener_version(control.id_plantilla, control.version_plantilla)
        return version, "CONTROL", control.version_plantilla

    if ctx.exigir_control:
        raise _Rechazo(pl.RECHAZADA_SIN_CONTROL,
                       "El archivo no tiene la hoja _Control con id_plantilla y version_plantilla. "
                       "Descargue la plantilla vigente desde SharePoint.")

    # Transicion: plantillas anteriores a la Fase 1, sin hoja _Control.
    versiones = ctx.control_repo.versiones(ctx.id_plantilla)
    version = pl.identificar_por_huella(estructura, versiones)
    if version is None:
        vigente = next((v for v in versiones if v.estado == "VIGENTE"), None)
        detalle = ""
        if vigente is not None:
            difs = pl.diferencias_estructura(
                vigente.columnas, pl.restringir_a_contrato(estructura, vigente.columnas))
            detalle = f" Diferencias con la vigente {vigente.version}: " + "; ".join(difs) if difs else ""
        raise _Rechazo(pl.RECHAZADA_ESTRUCTURA,
                       "El archivo no tiene hoja _Control y su estructura no coincide con ninguna "
                       f"version registrada de {ctx.id_plantilla}.{detalle}")
    advertencias.append(
        f"Archivo sin hoja _Control: version {version.version} identificada por huella (transicion)."
    )
    return version, "HUELLA", version.version


def procesar_archivo(archivo: Path, ctx: ContextoCarga) -> ResultadoCarga:
    nombre = archivo.name
    hash_arch = hash_archivo(archivo)
    subido_por = None
    try:
        subido_por = leer_modificado_por(archivo)
    except Exception as exc:  # noqa: BLE001 - dato informativo, nunca bloquea
        logger.warning("No se pudo leer 'Modificado por' de %s: %s", nombre, exc)

    id_carga = ctx.control_repo.iniciar_carga(
        nombre_archivo_original=nombre[:260],
        hash_archivo=hash_arch,
        tamano_bytes=archivo.stat().st_size,
        version_loader=ctx.version_loader,
        equipo=ctx.equipo[:100],
        usuario_ejecucion=ctx.usuario_ejecucion[:100],
        subido_por=subido_por[:200] if subido_por else None,
        origen_subido_por="DOCPROPS" if subido_por else None,
        estado=EN_PROCESO,
    )
    resultado = ResultadoCarga(nombre, id_carga, EN_PROCESO, subido_por=subido_por)
    logger.info("Carga %d iniciada: %s (sha256 %s)", id_carga, nombre, hash_arch[:12])

    try:
        _procesar(archivo, ctx, id_carga, hash_arch, resultado)
    except _Rechazo as rechazo:
        resultado.estado, resultado.mensaje = rechazo.estado, rechazo.mensaje
    except Exception as exc:  # noqa: BLE001 - se registra en carga_control y se sigue con el siguiente archivo
        logger.exception("Carga %d: error no previsto", id_carga)
        resultado.estado, resultado.mensaje = ERROR, f"{type(exc).__name__}: {exc}"

    _archivar(archivo, ctx, resultado, hash_arch)
    _notificar(ctx, resultado)
    return resultado


def _procesar(archivo: Path, ctx: ContextoCarga, id_carga: int, hash_arch: str,
              resultado: ResultadoCarga) -> None:
    # 1. Identidad y version de la plantilla ------------------------------------
    valores_control = leer_control(archivo)
    control = pl.ControlPlantilla.desde_dict(valores_control) if valores_control is not None else None
    estructura = leer_estructura(archivo)

    version, origen, declarada = _identificar_version(ctx, control, estructura, resultado.advertencias)
    resultado.version_plantilla = declarada
    real = pl.restringir_a_contrato(estructura, version.columnas) if version else estructura
    ctx.control_repo.actualizar_carga(
        id_carga,
        id_plantilla=ctx.id_plantilla,
        version_plantilla=(declarada or "")[:20] or None,
        origen_version=origen,
        hash_estructura_calculado=pl.calcular_hash_estructura(real),
        id_carga_base=control.id_carga_base if control else None,
        id_envio=control.id_envio if control else None,
    )

    decision = pl.evaluar_version(version, estructura, ctx.version_loader, ctx.hoy(), declarada)
    resultado.advertencias.extend(decision.advertencias)
    if not decision.aceptada:
        raise _Rechazo(decision.estado_rechazo, decision.mensaje)

    # 2. Dominio del archivo (anclado en DetalleAtributos) ----------------------
    try:
        dominio = _codigo_dominio_de_hoja(archivo, ctx.hojas, "DetalleAtributos")
    except Exception as exc:  # noqa: BLE001
        raise _Rechazo(RECHAZADA_DOMINIO, f"No se pudo determinar el dominio del archivo: {exc}") from exc
    if control is not None and control.codigo_dominio and control.codigo_dominio != dominio:
        raise _Rechazo(RECHAZADA_DOMINIO,
                       f"_Control declara el dominio '{control.codigo_dominio}' pero "
                       f"DetalleAtributos trae '{dominio}'.")
    resultado.codigo_dominio = dominio
    ctx.control_repo.actualizar_carga(id_carga, codigo_dominio=dominio)

    # 3. La plantilla parte de la ultima carga exitosa (Fase 2) --------------
    anterior = ctx.control_repo.ultima_carga_ok(dominio)
    if control is not None and control.id_carga_base is not None:
        vigente = anterior.id_carga if anterior else None
        if control.id_carga_base != vigente:
            raise _Rechazo(
                RECHAZADA_DESACTUALIZADA,
                f"La plantilla se genero sobre la carga {control.id_carga_base}, pero la ultima carga "
                f"exitosa de {dominio} es la {vigente}. Genere de nuevo la plantilla y vuelva a "
                "aplicar sus cambios.",
            )
    elif anterior is not None:
        resultado.advertencias.append(
            "Plantilla sin id_carga_base (no generada): no se pudo verificar que parta de la "
            f"ultima carga exitosa ({anterior.id_carga})."
        )

    # 4. Mismo archivo que la ultima carga exitosa del dominio -----------------
    if anterior is not None and anterior.hash_archivo == hash_arch:
        resultado.estado = OMITIDA_SIN_CAMBIOS
        resultado.mensaje = f"Archivo identico a la carga {anterior.id_carga}; no se reprocesa."
        return

    # 5. Staging ---------------------------------------------------------------
    resultados_stg = ejecutar(archivo, ctx.hojas, ctx.staging_repo, ctx.estrategia_staging,
                              id_carga=id_carga)
    for r in resultados_stg:
        ctx.control_repo.registrar_detalle(
            id_carga, "STAGING", r.hoja[:60], filas=r.filas_cargadas, error=r.error)
    fallidas = [r.hoja for r in resultados_stg if r.error]
    if fallidas:
        resultado.estado = ERROR_STAGING
        resultado.mensaje = f"Fallo la carga a staging de: {', '.join(fallidas)}. No se ejecuto el merge."
        return

    # 6. Merge staging -> gdd ----------------------------------------------------
    antes = _instantanea(ctx, dominio, resultado)
    resumenes = ctx.funcion_merge(ctx.engine, ctx.staging_repo, dominio, id_carga)
    despues = _instantanea(ctx, dominio, resultado) if antes is not None else None
    if antes is not None and despues is not None:
        resultado.cambios = comparar(antes, despues)
    for rm in resumenes:
        ctx.control_repo.registrar_detalle(id_carga, "MERGE", rm.objeto[:60], **rm.conteos())
    con_error = [rm for rm in resumenes if not rm.ok]
    if not con_error:
        resultado.estado = MERGE_OK
        resultado.mensaje = "Carga aplicada completa."
    else:
        resultado.estado = ERROR_MERGE if len(con_error) == len(resumenes) else MERGE_PARCIAL
        resultado.mensaje = "Merge con errores: " + " | ".join(
            f"{rm.objeto}: {rm.error}" for rm in con_error)


def _archivar(archivo: Path, ctx: ContextoCarga, resultado: ResultadoCarga, hash_arch: str) -> None:
    carpeta = ctx.carpeta_archivo if resultado.exitosa else ctx.carpeta_rechazados
    nombre = nombre_archivado(resultado.codigo_dominio, resultado.id_carga, hash_arch)
    campos = {"estado": resultado.estado, "fecha_fin": datetime.now()}
    mensaje = resultado.mensaje
    if resultado.advertencias:
        mensaje = (mensaje + " | Advertencias: " + " ".join(resultado.advertencias)).strip(" |")
    try:
        destino = mover(archivo, carpeta, nombre)
        resultado.ruta_final = destino
        campos["ruta_archivo_archivado"] = str(destino)[:1000]
    except Exception as exc:  # noqa: BLE001 - el estado de la carga no depende de poder mover el archivo
        logger.error("Carga %s: no se pudo mover %s a %s: %s", resultado.id_carga, archivo, carpeta, exc)
        mensaje = (mensaje + f" | No se pudo archivar el archivo: {exc}").strip(" |")
    campos["mensaje"] = mensaje or None
    ctx.control_repo.actualizar_carga(resultado.id_carga, **campos)
    resultado.mensaje = mensaje
    nivel = logging.INFO if resultado.exitosa else logging.WARNING
    logger.log(nivel, "Carga %d -> %s (%s) %s", resultado.id_carga, resultado.estado,
               resultado.codigo_dominio or "sin dominio", mensaje)


def _instantanea(ctx: ContextoCarga, dominio: str, resultado: ResultadoCarga):
    """Foto legible del dominio para la minuta. Nunca interrumpe la carga."""
    if ctx.notificador is None:
        return None
    try:
        return ctx.notificador.instantanea(dominio)
    except Exception as exc:  # noqa: BLE001 - la minuta sale sin detalle, la carga sigue
        logger.warning("Carga %s: no se pudo leer el estado del dominio %s para la minuta: %s",
                       resultado.id_carga, dominio, exc)
        resultado.motivo_sin_detalle = (
            "No se pudo calcular el detalle de cambios (revisar el log de gdd_loader). "
            "La carga se aplicó igual; ver el resultado arriba.")
        return None


def _notificar(ctx: ContextoCarga, resultado: ResultadoCarga) -> None:
    if ctx.notificador is None:
        return
    try:
        resultado.ruta_notificacion = ctx.notificador.notificar(resultado)
        logger.info("Carga %s: notificacion generada en %s", resultado.id_carga,
                    resultado.ruta_notificacion)
    except Exception as exc:  # noqa: BLE001 - el estado de la carga no depende del correo
        logger.error("Carga %s: no se pudo generar la notificacion: %s", resultado.id_carga, exc)

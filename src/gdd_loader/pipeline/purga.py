"""
Retencion de archivos: se conservan 12 meses (configurable con
GDD_DIAS_RETENCION). Pasado ese plazo se elimina el ARCHIVO de las carpetas
Archivo/Rechazados, pero el registro en gdd.carga_control se conserva (con
fecha_purga_archivo) -- la trazabilidad de la carga no se pierde.

Se decide por la fecha de la carga en la base (no por la fecha del archivo
en disco, que la sincronizacion de OneDrive puede alterar).

Nota: en una carpeta sincronizada, el borrado local pasa a la papelera de
reciclaje de SharePoint (retencion propia de SharePoint, ~93 dias).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger("gdd_loader.pipeline.purga")


def purgar(control_repo, dias_retencion: int, ahora: datetime | None = None,
           carpetas_permitidas: tuple[Path, ...] = ()) -> int:
    """Elimina los archivos archivados con mas de `dias_retencion` dias.
    Por seguridad solo borra archivos que esten dentro de `carpetas_permitidas`
    (Archivo/Rechazados): una ruta fuera de ellas se reporta y no se toca.
    Retorna cuantas cargas quedaron marcadas como purgadas."""
    if dias_retencion <= 0:
        return 0
    ahora = ahora or datetime.now()
    limite = ahora - timedelta(days=dias_retencion)
    raices = [p.resolve() for p in carpetas_permitidas]
    purgadas = 0
    for id_carga, ruta in control_repo.archivos_a_purgar(limite):
        archivo = Path(ruta)
        try:
            resuelto = archivo.resolve()
            if raices and not any(resuelto.is_relative_to(r) for r in raices):
                logger.error("Carga %d: %s esta fuera de las carpetas de archivo; no se purga.", id_carga, ruta)
                continue
            if archivo.exists():
                archivo.unlink()
            else:
                logger.warning("Carga %d: %s ya no existe; se marca como purgada.", id_carga, ruta)
            control_repo.actualizar_carga(id_carga, fecha_purga_archivo=ahora)
            purgadas += 1
        except Exception as exc:  # noqa: BLE001 - se reintenta en la proxima ejecucion
            logger.error("Carga %d: no se pudo purgar %s: %s", id_carga, ruta, exc)
    if purgadas:
        logger.info("Purga: %d archivo(s) con mas de %d dias eliminados.", purgadas, dias_retencion)
    return purgadas

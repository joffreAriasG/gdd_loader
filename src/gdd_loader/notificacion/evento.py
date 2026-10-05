"""
Evento de notificacion: JSON que toma el flujo de Power Automate.

Contrato (version_contrato = "1.2"):
    id_evento, ambiente, id_carga, archivo_original, codigo_dominio,
    nombre_dominio, version_plantilla, estado, estado_descripcion, exitosa,
    fecha_proceso, mensaje, asunto, responsables (nombres, rol GDD_NOTIF_ID_ROL),
    resumen {seccion: {nuevos, modificados, dados_de_baja}}, minuta_html,
    archivo_html (1.2: nombre del .html hermano).

Junto a cada JSON se escribe un .html con el MISMO nombre base: la minuta
como pagina completa, para abrirla en el navegador o reenviarla a mano
(copiar/pegar en Outlook conserva las tablas) sin depender del flujo.

El flujo solo resuelve nombres -> correo, usa `asunto` y pone `minuta_html`
como cuerpo. Toda la logica de contenido vive aqui (versionada y con tests).

Escritura atomica: cada archivo se escribe como `<nombre>.tmp` y luego se
renombra, para que nadie lea un archivo a medio escribir. El .html se escribe
ANTES que el .json: cuando el flujo (que filtra *.json) se dispara, el .html
ya existe.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Callable

from gdd_loader.notificacion.diferencias import resumen
from gdd_loader.notificacion.instantanea import (
    Instantanea,
    nombre_dominio,
    responsables_a_notificar,
    tomar_instantanea,
)
from gdd_loader.notificacion.minuta import DatosMinuta, describir_estado, generar_minuta_html

logger = logging.getLogger("gdd_loader.notificacion")

VERSION_CONTRATO = "1.2"


def armar_asunto(ambiente: str, codigo_dominio: str | None, estado: str, archivo: str) -> str:
    prefijo = "" if ambiente.upper() == "PRODUCCION" else f"[{ambiente.upper()}] "
    corto = describir_estado(estado).split(":")[0]
    return f"{prefijo}GDD | {codigo_dominio or 'SIN DOMINIO'} | {corto} | {archivo}"[:250]


def armar_evento(resultado, datos: DatosMinuta, responsables: list[str], exitosa: bool) -> dict:
    return {
        "version_contrato": VERSION_CONTRATO,
        "id_evento": str(uuid.uuid4()),
        "ambiente": datos.ambiente.upper(),
        "id_carga": resultado.id_carga,
        "archivo_original": resultado.archivo_original,
        "codigo_dominio": resultado.codigo_dominio,
        "nombre_dominio": datos.nombre_dominio,
        "version_plantilla": datos.version_plantilla,
        "estado": resultado.estado,
        "estado_descripcion": describir_estado(resultado.estado),
        "exitosa": exitosa,
        "fecha_proceso": datos.fecha_proceso,
        "mensaje": resultado.mensaje,
        "asunto": armar_asunto(datos.ambiente, resultado.codigo_dominio, resultado.estado,
                               resultado.archivo_original),
        "responsables": responsables,
        "resumen": resumen(datos.cambios) if datos.cambios is not None else {},
        "minuta_html": generar_minuta_html(datos),
    }


def documento_html(evento: dict) -> str:
    """La minuta como pagina HTML completa (titulo = asunto del correo)."""
    return (
        "<!DOCTYPE html>\n<html lang=\"es\">\n<head>\n<meta charset=\"utf-8\">\n"
        f"<title>{escape(evento.get('asunto') or 'Minuta de carga')}</title>\n</head>\n"
        "<body style=\"margin:20px;max-width:1100px;background:#ffffff;\">\n"
        f"{evento.get('minuta_html', '')}\n</body>\n</html>\n"
    )


def _escribir_atomico(destino: Path, contenido: str) -> None:
    temporal = destino.with_name(destino.name + ".tmp")
    temporal.write_text(contenido, encoding="utf-8")
    os.replace(temporal, destino)


def escribir_evento(carpeta: Path, evento: dict) -> Path:
    """Escribe `<base>.html` y luego `<base>.json`. Devuelve la ruta del JSON."""
    carpeta.mkdir(parents=True, exist_ok=True)
    dominio = evento.get("codigo_dominio") or "SIN_DOMINIO"
    base = f"resultado_{dominio}_{evento.get('id_carga') or 'sin_id'}_{evento['id_evento'][:8]}"
    ruta_html = carpeta / f"{base}.html"
    evento = {**evento, "archivo_html": ruta_html.name}
    _escribir_atomico(ruta_html, documento_html(evento))
    destino = carpeta / f"{base}.json"
    _escribir_atomico(destino, json.dumps(evento, ensure_ascii=False, indent=2))
    return destino


class Notificador:
    """Se inyecta en ContextoCarga.notificador. Sin estado entre cargas."""

    def __init__(self, engine, carpeta: Path, id_rol: int, ambiente: str, version_loader: str,
                 max_filas: int = 200,
                 reloj: Callable[[], datetime] = lambda: datetime.now().astimezone()):
        self.engine = engine
        self.carpeta = carpeta
        self.id_rol = id_rol
        self.ambiente = ambiente
        self.version_loader = version_loader
        self.max_filas = max_filas
        self.reloj = reloj

    def instantanea(self, codigo_dominio: str) -> Instantanea:
        with self.engine.connect() as conn:
            return tomar_instantanea(conn, codigo_dominio)

    def _datos_dominio(self, codigo_dominio: str | None) -> tuple[str | None, list[str]]:
        if not codigo_dominio:
            return None, []
        try:
            with self.engine.connect() as conn:
                return (nombre_dominio(conn, codigo_dominio),
                        responsables_a_notificar(conn, codigo_dominio, self.id_rol))
        except Exception as exc:  # noqa: BLE001 - el flujo usa el correo de respaldo
            logger.warning("No se pudieron leer nombre/responsables de %s: %s", codigo_dominio, exc)
            return None, []

    def notificar(self, resultado) -> Path:
        nombre, responsables = self._datos_dominio(resultado.codigo_dominio)
        datos = DatosMinuta(
            estado=resultado.estado,
            archivo=resultado.archivo_original,
            id_carga=resultado.id_carga,
            fecha_proceso=self.reloj().isoformat(timespec="seconds"),
            codigo_dominio=resultado.codigo_dominio,
            nombre_dominio=nombre,
            version_plantilla=resultado.version_plantilla,
            subido_por=resultado.subido_por,
            mensaje=resultado.mensaje,
            advertencias=list(resultado.advertencias),
            cambios=resultado.cambios,
            motivo_sin_detalle=resultado.motivo_sin_detalle,
            ambiente=self.ambiente,
            version_loader=self.version_loader,
            max_filas=self.max_filas,
        )
        evento = armar_evento(resultado, datos, responsables, resultado.exitosa)
        ruta = escribir_evento(self.carpeta, evento)
        if resultado.codigo_dominio and not responsables:
            logger.warning("Carga %s: el dominio %s no tiene responsables activos con id_rol=%s; "
                           "el flujo enviara al correo de respaldo.",
                           resultado.id_carga, resultado.codigo_dominio, self.id_rol)
        return ruta

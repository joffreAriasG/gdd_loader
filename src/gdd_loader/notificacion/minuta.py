"""
Minuta de carga para el cuerpo del correo (tipo acta, para aprobacion).

HTML simple con estilos en linea (Outlook no respeta <style> ni CSS externo):
encabezado con los datos de la carga, resumen por entidad y, por cada entidad
con cambios, las listas de nuevos, modificados (valor anterior -> nuevo) y
dados de baja. Todo el texto de origen se escapa (html.escape).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from html import escape

from gdd_loader.notificacion.diferencias import CambiosEntidad

FUENTE = "font-family:Segoe UI,Arial,sans-serif;font-size:13px;color:#1f2328;"
FUENTE_TABLA = "font-family:Segoe UI,Arial,sans-serif;font-size:12px;color:#1f2328;"
BORDE = "border:1px solid #d0d7de;padding:4px 8px;vertical-align:top;text-align:left;"
CABECERA = BORDE + "background:#f3f5f7;font-weight:600;"
LARGO_MAXIMO_VALOR = 500
LARGO_MAXIMO_CELDA = 200   # textos largos (descripciones) dentro de tablas de registros
# Color de la barra del titulo de cada tabla: nuevos / modificados / dados de baja
COLOR_TITULO = {"Nuevos": "#1a7f37", "Modificados": "#9a6700", "Dados de baja": "#cf222e"}

# Descripcion legible de cada estado final (proceso_carga.py / domain/plantilla.py)
DESCRIPCION_ESTADO = {
    "MERGE_OK": "Aplicada completa",
    "MERGE_PARCIAL": "Aplicada parcialmente (una o más secciones con error)",
    "ERROR_MERGE": "No aplicada: error al actualizar el glosario",
    "ERROR_STAGING": "No aplicada: error al leer las hojas del archivo",
    "OMITIDA_SIN_CAMBIOS": "Omitida: archivo idéntico a la última carga aplicada",
    "RECHAZADA_SIN_CONTROL": "Rechazada: plantilla sin hoja de control",
    "RECHAZADA_VERSION": "Rechazada: versión de plantilla no válida",
    "RECHAZADA_ESTRUCTURA": "Rechazada: estructura de plantilla no reconocida",
    "RECHAZADA_LOADER": "Rechazada: versión del cargador no compatible",
    "RECHAZADA_DOMINIO": "Rechazada: no se pudo determinar el dominio",
    "RECHAZADA_DESACTUALIZADA": "Rechazada: plantilla desactualizada",
    "ERROR": "Error no previsto",
}
COLOR_ESTADO = {"MERGE_OK": "#1a7f37", "OMITIDA_SIN_CAMBIOS": "#57606a", "MERGE_PARCIAL": "#9a6700"}


def describir_estado(estado: str) -> str:
    return DESCRIPCION_ESTADO.get(estado, estado)


@dataclass
class DatosMinuta:
    estado: str
    archivo: str
    id_carga: int | None
    fecha_proceso: str
    codigo_dominio: str | None = None
    nombre_dominio: str | None = None
    version_plantilla: str | None = None
    subido_por: str | None = None
    mensaje: str = ""
    advertencias: list[str] = field(default_factory=list)
    cambios: list[CambiosEntidad] | None = None   # None = no hubo merge o no se pudo calcular
    motivo_sin_detalle: str | None = None
    ambiente: str = "PRUEBAS"
    version_loader: str = ""
    max_filas: int = 200                          # por lista (nuevos / modificados / bajas)


def _e(valor, maximo: int = LARGO_MAXIMO_VALOR) -> str:
    texto = "" if valor is None else str(valor)
    if len(texto) > maximo:
        texto = texto[:maximo] + "…"
    return escape(texto) if texto else '<span style="color:#8c959f;">(vacío)</span>'


def _titulo_tabla(titulo: str, total: int) -> str:
    color = COLOR_TITULO.get(titulo, "#1f2328")
    return (f'<p style="{FUENTE}margin:10px 0 3px 0;border-left:4px solid {color};padding-left:6px;">'
            f'<b>{escape(titulo)} ({total})</b></p>')


def _fila(etiqueta: str, valor) -> str:
    return f'<tr><td style="{CABECERA}width:220px;">{escape(etiqueta)}</td><td style="{BORDE}">{_e(valor)}</td></tr>'


def _mas(total: int, maximo: int) -> str:
    if total <= maximo:
        return ""
    return (f'<p style="{FUENTE}color:#57606a;">… y {total - maximo} más (se muestran los primeros '
            f'{maximo}; el detalle completo está en el glosario).</p>')


def _tabla_registros(titulo: str, cambios, columnas: list[str], maximo: int) -> str:
    """Nuevos o dados de baja: una fila por registro, una columna por campo
    principal de la seccion. Sin columnas definidas, una sola columna con la
    etiqueta del registro."""
    if not cambios:
        return ""
    if columnas and any(c.fila for c in cambios):
        encabezado = "".join(f'<th style="{CABECERA}">{escape(c)}</th>' for c in columnas)
        filas = "".join(
            "<tr>" + "".join(f'<td style="{BORDE}">{_e(c.fila.get(col), LARGO_MAXIMO_CELDA)}</td>'
                             for col in columnas) + "</tr>"
            for c in cambios[:maximo])
    else:
        encabezado = f'<th style="{CABECERA}">Registro</th>'
        filas = "".join(f'<tr><td style="{BORDE}">{_e(c.etiqueta)}</td></tr>' for c in cambios[:maximo])
    return (f'{_titulo_tabla(titulo, len(cambios))}'
            f'<table style="{FUENTE_TABLA}border-collapse:collapse;width:100%;">'
            f'<tr>{encabezado}</tr>{filas}</table>{_mas(len(cambios), maximo)}')


def _modificados(cambios, maximo: int) -> str:
    if not cambios:
        return ""
    filas = []
    for c in cambios[:maximo]:
        for i, cc in enumerate(c.campos):
            registro = f"<b>{_e(c.etiqueta)}</b>" if i == 0 else ""
            filas.append(
                f'<tr><td style="{BORDE}">{registro}</td><td style="{BORDE}">{escape(cc.campo)}</td>'
                f'<td style="{BORDE}">{_e(cc.anterior)}</td><td style="{BORDE}">{_e(cc.nuevo)}</td></tr>')
    return (f'{_titulo_tabla("Modificados", len(cambios))}'
            f'<table style="{FUENTE_TABLA}border-collapse:collapse;width:100%;">'
            f'<tr><th style="{CABECERA}">Registro</th><th style="{CABECERA}">Campo</th>'
            f'<th style="{CABECERA}">Valor anterior</th><th style="{CABECERA}">Valor nuevo</th></tr>'
            f'{"".join(filas)}</table>{_mas(len(cambios), maximo)}')


def generar_minuta_html(d: DatosMinuta) -> str:
    dominio = " – ".join(p for p in (d.codigo_dominio, d.nombre_dominio) if p) or "(dominio no identificado)"
    color = COLOR_ESTADO.get(d.estado, "#cf222e")
    partes = [f'<div style="{FUENTE}">']
    if d.ambiente.upper() != "PRODUCCION":
        partes.append(f'<p style="{FUENTE}background:#fff8c5;padding:6px;">Ambiente de {escape(d.ambiente)}: '
                      'este correo es de prueba.</p>')
    partes.append(f'<h2 style="font-size:18px;margin:0 0 8px 0;">Minuta de carga · {escape(dominio)}</h2>')
    partes.append(f'<table style="{FUENTE}border-collapse:collapse;margin-bottom:10px;">')
    partes.append(
        f'<tr><td style="{CABECERA}width:220px;">Resultado</td>'
        f'<td style="{BORDE}color:{color};font-weight:600;">{escape(describir_estado(d.estado))}</td></tr>')
    partes.append(_fila("Archivo", d.archivo))
    partes.append(_fila("N.° de carga", d.id_carga))
    partes.append(_fila("Fecha de proceso", d.fecha_proceso.replace("T", " ")[:16]))
    partes.append(_fila("Versión de plantilla", d.version_plantilla))
    partes.append(_fila("Última modificación del archivo por", d.subido_por))
    partes.append("</table>")

    if d.mensaje:
        partes.append(f'<p style="{FUENTE}"><b>Detalle:</b> {_e(d.mensaje)}</p>')
    if d.advertencias:
        items = "".join(f"<li>{_e(a)}</li>" for a in d.advertencias)
        partes.append(f'<p style="{FUENTE}margin-bottom:2px;"><b>Advertencias</b></p>'
                      f'<ul style="{FUENTE}margin-top:0;">{items}</ul>')

    if d.cambios is None:
        motivo = d.motivo_sin_detalle or "La carga no llegó a aplicar cambios en el glosario."
        partes.append(f'<p style="{FUENTE}"><b>Cambios:</b> {_e(motivo)}</p>')
    else:
        total = sum(c.total for c in d.cambios)
        partes.append('<h3 style="font-size:15px;margin:14px 0 4px 0;">Resumen de cambios</h3>')
        if total == 0:
            partes.append(f'<p style="{FUENTE}">La carga no generó cambios: el glosario ya tenía '
                          'exactamente estos datos.</p>')
        else:
            filas = "".join(
                f'<tr><td style="{BORDE}">{escape(c.entidad)}</td>'
                f'<td style="{BORDE}text-align:right;">{len(c.nuevos)}</td>'
                f'<td style="{BORDE}text-align:right;">{len(c.modificados)}</td>'
                f'<td style="{BORDE}text-align:right;">{len(c.bajas)}</td></tr>'
                for c in d.cambios if c.total)
            partes.append(
                f'<table style="{FUENTE}border-collapse:collapse;">'
                f'<tr><th style="{CABECERA}">Sección</th><th style="{CABECERA}">Nuevos</th>'
                f'<th style="{CABECERA}">Modificados</th><th style="{CABECERA}">Dados de baja</th></tr>'
                f'{filas}</table>')
            for c in d.cambios:
                if not c.total:
                    continue
                partes.append(f'<h3 style="font-size:15px;margin:16px 0 4px 0;">{escape(c.entidad)}</h3>')
                partes.append(_tabla_registros("Nuevos", c.nuevos, c.columnas, d.max_filas))
                partes.append(_modificados(c.modificados, d.max_filas))
                partes.append(_tabla_registros("Dados de baja", c.bajas, c.columnas, d.max_filas))

    if d.estado in ("MERGE_OK", "MERGE_PARCIAL") and d.cambios and sum(c.total for c in d.cambios):
        cierre = ("Para aprobación: revise los cambios aplicados y responda a este correo con su "
                  "aprobación u observaciones.")
    else:
        cierre = "Este correo es informativo; no requiere aprobación."
    partes.append(f'<p style="{FUENTE}margin-top:16px;"><b>{escape(cierre)}</b></p>')
    partes.append(f'<p style="{FUENTE}color:#57606a;font-size:11px;">Generado automáticamente por '
                  f'gdd_loader {escape(d.version_loader)}. Ambiente: {escape(d.ambiente)}.</p>')
    partes.append("</div>")
    return "\n".join(p for p in partes if p)

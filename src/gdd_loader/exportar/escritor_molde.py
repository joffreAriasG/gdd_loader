"""
Llena el MOLDE de la plantilla con datos, sin abrirlo con openpyxl.

Por que no openpyxl: el molde real trae partes que openpyxl descarta al
guardar y que son necesarias en el banco:
  - customXml/item1.xml  -> referencias a los Office Scripts compartidos con
                            el libro (sin esto desaparecen de "Automatizar")
  - docMetadata/LabelInfo.xml, docProps/custom.xml -> clasificacion de la
                            informacion (etiqueta de sensibilidad)
  - customXml/item2..4    -> tipo de contenido / metadatos de SharePoint

Tecnica: el .xlsx es un zip. Se copian TODAS las partes del molde byte a byte
y solo se reescriben:
  - la <sheetData> de cada hoja que recibe datos (el encabezado y los estilos
    de columna se toman del propio molde),
  - el rango (ref) de la tabla de Excel de esa hoja y su autofiltro,
  - la hoja oculta `_Control` (se crea si el molde no la tiene).

Los valores de texto se escriben como "inline strings" (no se toca
sharedStrings.xml); Excel los convierte al guardar.
"""

from __future__ import annotations

import posixpath
import re
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from xml.sax.saxutils import escape

from gdd_loader.domain.plantilla import HOJA_CONTROL

_NS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_NS_REL_WORKSHEET = _NS_REL + "/worksheet"
_CT_WORKSHEET = "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"
_BUILTIN_FECHA = set(range(14, 23)) | {45, 46, 47}
_XML_INVALIDO = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")
_EPOCH = datetime(1899, 12, 30)


class MoldeInvalidoError(Exception):
    pass


@dataclass
class ResultadoEscritura:
    filas_por_hoja: dict[str, int]
    advertencias: list[str]


# --- utilidades de celdas -------------------------------------------------------


def _col_letras(n: int) -> str:
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _col_numero(letras: str) -> int:
    n = 0
    for ch in letras:
        n = n * 26 + (ord(ch) - 64)
    return n


def _attr(tag: str, nombre: str) -> str | None:
    m = re.search(rf'\b{nombre}="([^"]*)"', tag)
    return m.group(1) if m else None


def _serial_excel(valor: date | datetime) -> float:
    if not isinstance(valor, datetime):
        valor = datetime(valor.year, valor.month, valor.day)
    delta = valor - _EPOCH
    return delta.days + delta.seconds / 86400


def _num(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else repr(float(v))


# --- lectura de la estructura del paquete -------------------------------------


class _Paquete:
    def __init__(self, molde: Path):
        self.zin = zipfile.ZipFile(molde)
        self.nombres = self.zin.namelist()
        self.cambios: dict[str, bytes] = {}
        self.nuevos: list[str] = []

    def leer(self, parte: str) -> str:
        if parte in self.cambios:
            return self.cambios[parte].decode("utf-8")
        return self.zin.read(parte).decode("utf-8")

    def escribir(self, parte: str, contenido: str) -> None:
        if parte not in self.nombres and parte not in self.nuevos:
            self.nuevos.append(parte)
        self.cambios[parte] = contenido.encode("utf-8")

    @staticmethod
    def _rels_de(parte: str) -> str:
        d, f = posixpath.split(parte)
        return posixpath.join(d, "_rels", f + ".rels")

    def relaciones(self, parte: str) -> list[dict]:
        rels = self._rels_de(parte)
        if rels not in self.nombres and rels not in self.cambios:
            return []
        base = posixpath.dirname(parte)
        salida = []
        for tag in re.findall(r"<Relationship\b[^>]*/?>", self.leer(rels)):
            destino = _attr(tag, "Target")
            ruta = destino.lstrip("/") if destino.startswith("/") else posixpath.normpath(posixpath.join(base, destino))
            salida.append({"id": _attr(tag, "Id"), "tipo": _attr(tag, "Type"), "ruta": ruta, "target": destino})
        return salida

    def hojas(self) -> dict[str, str]:
        wb = self.leer("xl/workbook.xml")
        rid_a_ruta = {r["id"]: r["ruta"] for r in self.relaciones("xl/workbook.xml")}
        hojas = {}
        for tag in re.findall(r"<sheet\b[^>]*/>", wb):
            hojas[_unescape(_attr(tag, "name"))] = rid_a_ruta[_attr(tag, "r:id")]
        return hojas

    def guardar(self, destino: Path) -> None:
        destino.parent.mkdir(parents=True, exist_ok=True)
        tmp = destino.with_suffix(destino.suffix + ".tmp")
        with zipfile.ZipFile(tmp, "w") as zout:
            for info in self.zin.infolist():
                datos = self.cambios.get(info.filename)
                zout.writestr(info, datos if datos is not None else self.zin.read(info.filename))
            for parte in self.nuevos:
                zout.writestr(parte, self.cambios[parte], compress_type=zipfile.ZIP_DEFLATED)
        tmp.replace(destino)


def _unescape(s: str) -> str:
    return s.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&apos;", "'")


def _estilos_fecha(paquete: _Paquete) -> set[int]:
    """Indices de cellXfs cuyo formato numerico es de fecha."""
    estilos = paquete.leer("xl/styles.xml")
    formatos = {}
    for tag, codigo in re.findall(r'(<numFmt\b[^>]*formatCode="([^"]*)"[^>]*/>)', estilos):
        formatos[int(_attr(tag, "numFmtId"))] = _unescape(codigo)
    xfs = re.search(r"<cellXfs\b[^>]*>(.*?)</cellXfs>", estilos, re.S)
    fechas = set()
    if not xfs:
        return fechas
    for i, xf in enumerate(re.findall(r"<xf\b[^>]*?(?:/>|>.*?</xf>)", xfs.group(1), re.S)):
        nid = int(_attr(xf, "numFmtId") or 0)
        codigo = re.sub(r'"[^"]*"|\[[^\]]*\]', "", formatos.get(nid, "")).lower()
        if nid in _BUILTIN_FECHA or (codigo and re.search(r"[dmy]", codigo) and "general" not in codigo):
            fechas.add(i)
    return fechas


# --- escritura de una hoja ------------------------------------------------------


def _celda(ref: str, estilo: str | None, valor, estilos_fecha: set[int], avisos: list[str], hoja: str) -> str:
    s = f' s="{estilo}"' if estilo is not None else ""
    if valor is None or (isinstance(valor, str) and valor == ""):
        return f'<c r="{ref}"{s}/>'
    if isinstance(valor, bool):
        return f'<c r="{ref}"{s} t="b"><v>{int(valor)}</v></c>'
    if isinstance(valor, (int, float, Decimal)):
        return f'<c r="{ref}"{s}><v>{_num(float(valor))}</v></c>'
    if isinstance(valor, (date, datetime)):
        if estilo is not None and int(estilo) in estilos_fecha:
            return f'<c r="{ref}"{s}><v>{_num(_serial_excel(valor))}</v></c>'
        avisos.append(f"{hoja}!{ref}: la columna no tiene formato de fecha en el molde; se escribio como texto")
        # Mismo texto que produce una fecha real de Excel leida con dtype=str:
        # el loader lo acepta en cualquier columna de columnas_fecha.
        if not isinstance(valor, datetime):
            valor = datetime(valor.year, valor.month, valor.day)
        valor = valor.strftime("%Y-%m-%d %H:%M:%S")
    texto = _XML_INVALIDO.sub("", str(valor))
    preservar = ' xml:space="preserve"' if texto != texto.strip() or "\n" in texto else ""
    return f'<c r="{ref}"{s} t="inlineStr"><is><t{preservar}>{escape(texto)}</t></is></c>'


def _reescribir_hoja(paquete: _Paquete, parte: str, filas: list[list], estilos_fecha: set[int],
                     avisos: list[str], hoja: str) -> None:
    xml = paquete.leer(parte)
    m = re.search(r"<sheetData\s*/>|<sheetData\b[^>]*>(.*?)</sheetData>", xml, re.S)
    if not m:
        raise MoldeInvalidoError(f"Hoja '{hoja}': no se encontro <sheetData>")
    contenido = m.group(1) or ""
    filas_xml = re.findall(r"(<row\b[^>]*?(?:/>|>.*?</row>))", contenido, re.S)
    encabezado = next((f for f in filas_xml if _attr(f.split(">", 1)[0], "r") == "1"), None)
    if encabezado is None:
        raise MoldeInvalidoError(f"Hoja '{hoja}': el molde no tiene fila de encabezado")
    columnas = [_col_numero(re.match(r"[A-Z]+", r).group()) for r in re.findall(r'<c\b[^>]*\br="([A-Z]+)1"', encabezado)]
    ncols = max(columnas) if columnas else 0
    if ncols == 0:
        raise MoldeInvalidoError(f"Hoja '{hoja}': encabezado vacio")

    plantilla = next((f for f in filas_xml if _attr(f.split(">", 1)[0], "r") == "2"), None)
    estilos_col: dict[int, str] = {}
    attrs_fila = ""
    if plantilla is not None:
        apertura = re.match(r"<row\b[^>]*", plantilla).group(0)
        attrs_fila = re.sub(r'\s(r|spans)="[^"]*"', "", apertura[len("<row"):])
        for c in re.findall(r"<c\b[^>]*", plantilla):
            ref = _attr(c, "r")
            estilo = _attr(c, "s")
            if ref and estilo is not None:
                estilos_col[_col_numero(re.match(r"[A-Z]+", ref).group())] = estilo

    nuevas = [encabezado]
    total = max(len(filas), 1)  # una tabla de Excel necesita al menos una fila de datos
    for i in range(total):
        fila = filas[i] if i < len(filas) else []
        if len(fila) > ncols:
            raise MoldeInvalidoError(f"Hoja '{hoja}': la fila {i + 2} trae {len(fila)} valores y el molde tiene {ncols} columnas")
        r = i + 2
        celdas = "".join(
            _celda(f"{_col_letras(j)}{r}", estilos_col.get(j), fila[j - 1] if j - 1 < len(fila) else None,
                   estilos_fecha, avisos, hoja)
            for j in range(1, ncols + 1)
        )
        nuevas.append(f'<row r="{r}" spans="1:{ncols}"{attrs_fila}>{celdas}</row>')

    ultima = total + 1
    xml = xml[:m.start()] + "<sheetData>" + "".join(nuevas) + "</sheetData>" + xml[m.end():]
    xml = re.sub(r'<dimension ref="[^"]*"', f'<dimension ref="A1:{_col_letras(ncols)}{ultima}"', xml, count=1)
    paquete.escribir(parte, xml)

    for rel in paquete.relaciones(parte):
        if rel["tipo"] and rel["tipo"].endswith("/table"):
            tabla = paquete.leer(rel["ruta"])
            ref = re.search(r'<table\b[^>]*\bref="([A-Z]+)(\d+):([A-Z]+)(\d+)"', tabla)
            if ref:
                nuevo = f"{ref.group(1)}{ref.group(2)}:{ref.group(3)}{ultima}"
                tabla = re.sub(r'(<table\b[^>]*\bref=")[^"]*"', rf'\g<1>{nuevo}"', tabla, count=1)
                tabla = re.sub(r'(<autoFilter\b[^>]*\bref=")[^"]*"', rf'\g<1>{nuevo}"', tabla, count=1)
                paquete.escribir(rel["ruta"], tabla)


# --- hoja _Control --------------------------------------------------------------


def _xml_control(valores: dict[str, str | None]) -> str:
    filas = [("clave", "valor")] + [(k, "" if v is None else str(v)) for k, v in valores.items()]
    rows = []
    for i, (k, v) in enumerate(filas, start=1):
        rows.append(
            f'<row r="{i}"><c r="A{i}" t="inlineStr"><is><t>{escape(k)}</t></is></c>'
            f'<c r="B{i}" t="inlineStr"><is><t>{escape(v)}</t></is></c></row>'
        )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<dimension ref="A1:B{len(filas)}"/><sheetData>{"".join(rows)}</sheetData>'
        '<sheetProtection sheet="1" objects="1" scenarios="1"/></worksheet>'
    )


def _escribir_control(paquete: _Paquete, hojas: dict[str, str], valores: dict[str, str | None]) -> None:
    if HOJA_CONTROL in hojas:
        paquete.escribir(hojas[HOJA_CONTROL], _xml_control(valores))
        return
    existentes = [int(n) for n in re.findall(r"xl/worksheets/sheet(\d+)\.xml$", "\n".join(paquete.nombres), re.M)]
    parte = f"xl/worksheets/sheet{max(existentes, default=0) + 1}.xml"
    paquete.escribir(parte, _xml_control(valores))

    rels_parte = "xl/_rels/workbook.xml.rels"
    rels = paquete.leer(rels_parte)
    ids = [int(x) for x in re.findall(r'Id="rId(\d+)"', rels)]
    rid = f"rId{max(ids, default=0) + 1}"
    target = parte[len("xl/"):]
    rels = rels.replace("</Relationships>",
                        f'<Relationship Id="{rid}" Type="{_NS_REL_WORKSHEET}" Target="{target}"/></Relationships>')
    paquete.escribir(rels_parte, rels)

    wb = paquete.leer("xl/workbook.xml")
    sheet_ids = [int(x) for x in re.findall(r'<sheet\b[^>]*\bsheetId="(\d+)"', wb)]
    wb = wb.replace("</sheets>",
                    # xmlns:r se declara en el propio elemento: el molde puede declararlo
                    # en la raiz (Excel) o en cada <sheet> (otras herramientas).
                    f'<sheet xmlns:r="{_NS_REL}" name="{HOJA_CONTROL}" '
                    f'sheetId="{max(sheet_ids, default=0) + 1}" state="veryHidden" r:id="{rid}"/></sheets>', 1)
    paquete.escribir("xl/workbook.xml", wb)

    ct = paquete.leer("[Content_Types].xml")
    ct = ct.replace("</Types>", f'<Override PartName="/{parte}" ContentType="{_CT_WORKSHEET}"/></Types>')
    paquete.escribir("[Content_Types].xml", ct)


# --- API ---------------------------------------------------------------------------


def llenar_molde(
    molde: Path,
    destino: Path,
    datos: dict[str, list[list]],
    control: dict[str, str | None],
) -> ResultadoEscritura:
    """Genera `destino` a partir de `molde`, reemplazando las filas de datos
    de cada hoja de `datos` (hoja -> filas, cada fila en el orden de columnas
    del encabezado del molde) y escribiendo la hoja `_Control`. Las hojas que
    no vienen en `datos` quedan exactamente como en el molde."""
    paquete = _Paquete(molde)
    try:
        hojas = paquete.hojas()
        faltan = [h for h in datos if h not in hojas]
        if faltan:
            raise MoldeInvalidoError(f"El molde no tiene las hojas {faltan}")
        estilos_fecha = _estilos_fecha(paquete)
        avisos: list[str] = []
        for hoja, filas in datos.items():
            _reescribir_hoja(paquete, hojas[hoja], filas, estilos_fecha, avisos, hoja)
        _escribir_control(paquete, hojas, control)
        paquete.guardar(destino)
        return ResultadoEscritura({h: len(f) for h, f in datos.items()}, avisos)
    finally:
        paquete.zin.close()

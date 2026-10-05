"""
Capa de dominio: transforma filas de staging (texto plano, tal como salen
del Excel) en objetos listos para resolver contra catalogos e insertar en
el esquema `gdd`. No sabe leer Excel ni tocar SQL Server -- solo conoce las
reglas de negocio de la plantilla (que significa "X", "-", "Si"/"No", como
se separan fuente oficial y fuente "_foc", etc.).

Mantener esto separado de gdd_repository.py es lo que permite probar todas
estas reglas con pytest puro, sin mockear una conexion a base de datos.
"""

from __future__ import annotations

import datetime
import math
import re
from dataclasses import dataclass

VACIO = "-"

# gdd.cat_dato_personal tiene columnas de codigo (codigo_categoria_nivel_uno,
# codigo_tipo_dato, codigo_sensibilidad) separadas de las descriptivas
# (categoria_nivel_uno, tipo_dato, sensibilidad). El Excel trae codigo +
# descripcion concatenados en un solo texto (ej. "6. Crediticios",
# "6.1. Datos Identificativos...", "2. Medio") -- hay que extraer el codigo
# para cruzar contra las columnas de codigo, no contra las descriptivas.
_PATRON_CODIGO_NIVEL_UNO = re.compile(r"^(\d+)\.")
_PATRON_CODIGO_TIPO_DATO = re.compile(r"^(\d+\.\d+)\.")


def extraer_codigo_categoria_nivel_uno(texto: str | None) -> str | None:
    """"6. Crediticios" -> "6" """
    if texto is None:
        return None
    coincidencia = _PATRON_CODIGO_NIVEL_UNO.match(texto)
    if not coincidencia:
        raise ValueError(
            f"No se pudo extraer el codigo de categoria_nivel_1 de '{texto}' "
            "(se espera el formato 'N. Descripcion')"
        )
    return coincidencia.group(1)


def extraer_codigo_tipo_dato(texto: str | None) -> str | None:
    """"6.1. Datos Identificativos..." -> "6.1" """
    if texto is None:
        return None
    coincidencia = _PATRON_CODIGO_TIPO_DATO.match(texto)
    if not coincidencia:
        raise ValueError(
            f"No se pudo extraer el codigo de tipo_dato de '{texto}' "
            "(se espera el formato 'N.M. Descripcion')"
        )
    return coincidencia.group(1)


def extraer_codigo_sensibilidad(texto: str | None) -> str | None:
    """"2. Medio" -> "2" """
    if texto is None:
        return None
    coincidencia = _PATRON_CODIGO_NIVEL_UNO.match(texto)
    if not coincidencia:
        raise ValueError(
            f"No se pudo extraer el codigo de sensibilidad de '{texto}' "
            "(se espera el formato 'N. Descripcion')"
        )
    return coincidencia.group(1)


def _texto_o_none(valor: object) -> str | None:
    """"-" o vacio -> None. Cualquier otro texto se retorna recortado.

    Bug real detectado el 2026-09-15 probando PlanDeRemediacion contra el
    Excel real: una celda GENUINAMENTE vacia (a diferencia de "-", el
    placeholder que usa la plantilla) llega aqui como float('nan') -- no
    pasa por "tipos"/dtype=str columna por columna, solo las columnas
    listadas en SheetConfig.tipos lo hacen. Sin este chequeo,
    str(float('nan')) produce el TEXTO LITERAL 'nan', que quedaba guardado
    en gdd como si fuera un valor real. Afecta a cualquier columna opcional
    de texto libre de cualquier hoja (observaciones/descripcion/area/etc.),
    no solo PlanDeRemediacion -- por eso se corrige aqui, en el helper
    compartido, no solo para la columna donde se detecto.
    """
    if valor is None:
        return None
    if isinstance(valor, float) and math.isnan(valor):
        return None
    texto = str(valor).strip()
    if texto == "" or texto == VACIO:
        return None
    return texto


def _bit_x(valor: object) -> bool:
    """Columnas tipo reporte/proyecto/proceso/estructura: "X" = True, "-" = False."""
    texto = str(valor).strip() if valor is not None else ""
    if texto == "X":
        return True
    if texto == VACIO or texto == "":
        return False
    raise ValueError(f"Valor inesperado para columna tipo X/-: '{valor}'")


def _entero_o_none(valor: object) -> int | None:
    """Convierte a int, tolerando lo que puede llegar de una columna pandas
    "Int64" con nulos (tipos={"avance": "Int64"} en SheetConfig)
    una vez pasa por to_dict("records"): "<NA>", None, "-" o vacio se tratan
    como ausente. int(float(...)) tolera tambien texto tipo "70.0".
    """
    if valor is None:
        return None
    texto = str(valor).strip()
    if texto in ("", VACIO, "<NA>", "nan", "None"):
        return None
    try:
        return int(float(texto))
    except ValueError as exc:
        raise ValueError(f"No se pudo convertir '{valor}' a numero entero.") from exc


def _dividir_codigo_dominio_atributo(texto: str) -> tuple[str, int]:
    """"ADS-4" -> ("ADS", 4). Inverso de AtributoPendiente.codigo_dominio_atributo
    (f"{codigo_dominio}-{codigo_atributo}"). Se separa por el ULTIMO guion
    (rpartition), no el primero, por si codigo_dominio alguna vez trae un
    guion propio.
    """
    if "-" not in texto:
        raise ValueError(
            f"codigo_dominio_atributo='{texto}' no tiene el formato esperado "
            "'CODIGO_DOMINIO-CODIGO_ATRIBUTO' (ej. 'ADS-4')."
        )
    codigo_dominio, _, codigo_atributo_texto = texto.rpartition("-")
    try:
        codigo_atributo = int(codigo_atributo_texto)
    except ValueError as exc:
        raise ValueError(
            f"codigo_dominio_atributo='{texto}': la parte despues del ultimo guion "
            f"('{codigo_atributo_texto}') no es un numero de atributo valido."
        ) from exc
    return codigo_dominio, codigo_atributo


def _bit_si_no(valor: object) -> bool:
    """Columnas tipo es_dato_personal/acepta_valores_nulos: "Si"/"No"."""
    texto = str(valor).strip() if valor is not None else ""
    texto_normalizado = texto.replace("í", "i").replace("Í", "I").lower()
    if texto_normalizado == "si":
        return True
    if texto_normalizado == "no":
        return False
    raise ValueError(f"Valor inesperado para columna tipo Si/No: '{valor}'")


# staging.metadata_tecnica.fecha_aprobada se guarda como texto libre (ver
# sheet_config.py) y se acumula en la tabla a traves de MULTIPLES cargas en
# el tiempo -- a diferencia de detalle_atributos, que se relee completo en
# cada carga. Por eso este parser debe seguir aceptando el formato viejo
# "DD-MM-YYYY" (filas cargadas antes del 2026-09-16) ademas del nuevo
# "YYYY-MM-DD HH:MM:SS" (celda de fecha real de Excel leida con dtype=str,
# desde que el usuario cambio la plantilla). Se intentan en este orden --
# formato nuevo primero, ya que es el que trae la plantilla vigente.
# 2026-10-01: "%d/%m/%Y" (texto dd/mm/aaaa con barras, ej. "14/07/2026" en
# CLI.xlsx) -- misma convencion dia/mes que "%d-%m-%Y".
_FORMATOS_FECHA_METADATA_TECNICA = ("%Y-%m-%d %H:%M:%S", "%d-%m-%Y", "%d/%m/%Y")


def _parsear_fecha_aprobada_metadata(valor: object, contexto: str) -> datetime.date:
    texto = str(valor).strip() if valor is not None else ""
    for formato in _FORMATOS_FECHA_METADATA_TECNICA:
        try:
            return datetime.datetime.strptime(texto, formato).date()
        except (ValueError, TypeError):
            continue
    formatos = " o ".join(_FORMATOS_FECHA_METADATA_TECNICA)
    raise ValueError(
        f"{contexto}: no se pudo parsear fecha_aprobada='{valor}' con formato {formatos}"
    )


@dataclass(frozen=True)
class AtributoPendiente:
    """Una fila de staging.detalle_atributos, normalizada. Los campos que
    terminan en _texto todavia no estan resueltos contra un catalogo --
    eso lo hace gdd_repository, que si tiene acceso a la base de datos.
    """

    codigo_dominio: str
    codigo_atributo: int
    nombre_atributo: str | None
    descripcion_atributo: str | None
    nivel_criticidad_texto: str | None
    reporte: bool
    proyecto: bool
    proceso: bool
    estructura: bool
    es_dato_personal: bool
    categoria_nivel_1_texto: str | None
    tipo_dato_texto: str | None
    sensibilidad_texto: str | None
    tipo_atributo_texto: str | None
    compartido_similar_texto: str | None
    atributo_primario_texto: str | None
    dominio_primario_texto: str | None
    fecha_aprobacion: datetime.date

    @property
    def codigo_dominio_atributo(self) -> str:
        return f"{self.codigo_dominio}-{self.codigo_atributo}"

    @property
    def clave_natural(self) -> tuple[str, int]:
        return (self.codigo_dominio, self.codigo_atributo)


@dataclass(frozen=True)
class FuenteOficialPendiente:
    """Una fila destino de gdd.atributo_fuente_oficial, normalizada: UNA
    por fila de staging.metadata_tecnica, solo con las columnas "_oficial".
    Desde 2026-10-01 las columnas "_foc" ya no generan una fuente oficial
    secundaria (ver mapear_metadata_tecnica): van solo a
    gdd.atributo_fuente_consumo.
    """

    codigo_dominio_atributo: str
    es_fuente_primaria: bool
    clase_texto: str | None
    servidor_texto: str | None
    base_datos_texto: str | None
    nombre_campo: str
    longitud_campo: str
    tipo_campo_texto: str | None
    lista_valores_validos_texto: str | None
    acepta_valores_nulos: bool
    formula_calculo: str | None
    fecha_aprobacion: datetime.date | None
    # 2026-10-01: tabla/archivo de origen dentro de la base de datos fuente
    # (columna tabla_fuente_oficial de MetadataTecnica). Texto libre, sin
    # catalogo (puede ser una tabla o un archivo, ej.
    # "ReporteVentasDiario_dd.mm.yyyy.txt"). Es parte de la CLAVE NATURAL
    # (ver carga_gdd._clave_fuente): un mismo campo puede venir de varias
    # tablas/archivos del mismo servidor y base de datos.
    nombre_tabla: str | None = None

    # Nota: a diferencia de AtributoPendiente, esta clase NO expone una
    # `clave_natural` propia -- (clase, es_fuente_primaria) resulto
    # insuficiente (colapsaba filas de sistemas/tablas fuente distintos que
    # comparten `clase`). La clave real depende de id_bdd, que solo se
    # conoce tras resolver servidor+base de datos contra la BD -- por eso
    # se calcula en pipeline/carga_gdd.py (`_clave_fuente`), no aqui.


def mapear_detalle_atributos(registros: list[dict]) -> list[AtributoPendiente]:
    """`registros`: filas de staging.detalle_atributos como dicts (ej. desde
    `df.to_dict("records")` al leerlas de vuelta de SQL Server).
    """
    resultado = []
    for fila in registros:
        es_dato_personal = _bit_si_no(fila["es_dato_personal"])
        resultado.append(
            AtributoPendiente(
                codigo_dominio=str(fila["codigo_dominio"]).strip(),
                codigo_atributo=int(fila["codigo_atributo"]),
                nombre_atributo=_texto_o_none(fila["atributo"]),
                descripcion_atributo=_texto_o_none(fila["descripcion_atributo"]),
                nivel_criticidad_texto=_texto_o_none(fila["nivel_criticidad"]),
                reporte=_bit_x(fila["reporte"]),
                proyecto=_bit_x(fila["proyecto"]),
                proceso=_bit_x(fila["proceso"]),
                estructura=_bit_x(fila["estructura"]),
                es_dato_personal=es_dato_personal,
                # Solo tienen sentido si es_dato_personal=True; si no, se
                # ignora lo que venga (deberia ser "-", pero no confiamos
                # ciegamente en eso) y se guarda None.
                categoria_nivel_1_texto=(
                    _texto_o_none(fila["categoria_nivel_1"]) if es_dato_personal else None
                ),
                tipo_dato_texto=(
                    _texto_o_none(fila["tipo_dato"]) if es_dato_personal else None
                ),
                sensibilidad_texto=(
                    _texto_o_none(fila["sensibilidad"]) if es_dato_personal else None
                ),
                tipo_atributo_texto=_texto_o_none(fila["tipo_atributo"]),
                compartido_similar_texto=_texto_o_none(fila["compartido_similar"]),
                atributo_primario_texto=_texto_o_none(fila["atributo_primario"]),
                dominio_primario_texto=_texto_o_none(fila["dominio_primario"]),
                fecha_aprobacion=fila["fecha_aprobada"],  # ya viene como date (staging lo parsea)
            )
        )
    return resultado


def mapear_metadata_tecnica(registros: list[dict]) -> list[FuenteOficialPendiente]:
    """`registros`: filas de staging.metadata_tecnica como dicts.

    Cada fila genera UNA fuente oficial primaria, solo con las columnas
    "_oficial" (servidor, base de datos, tabla, clase, campo...).

    CORRECCION 2026-10-01 (definicion del usuario): las columnas "_foc"
    (servidor_foc, coleccion_foc, tabla_bv_foc, nombre_campo_foc) NUNCA van
    a gdd.atributo_fuente_oficial. Son exclusivas de
    gdd.atributo_fuente_consumo (mapear_atributo_fuente_consumo), con sus
    catalogos controlados (base_datos_fuente / base_datos_fuente_tabla).
    Antes se generaba ademas una fuente oficial "secundaria"
    (es_fuente_primaria=False) con tabla_bv_foc como base de datos: duplicaba
    el consumo y creaba filas en gdd.base_datos_fuente con nombres de tabla
    (get-or-create). Las existentes se dan de baja con
    db/migraciones/20261001b_baja_fuentes_secundarias_foc.sql.
    """
    resultado: list[FuenteOficialPendiente] = []
    for fila in registros:
        codigo_dominio_atributo = str(fila["codigo_dominio_atributo"]).strip()
        fecha = (
            _parsear_fecha_aprobada_metadata(fila["fecha_aprobada"], codigo_dominio_atributo)
            if _texto_o_none(fila.get("fecha_aprobada")) is not None
            else None
        )

        resultado.append(
            FuenteOficialPendiente(
                codigo_dominio_atributo=codigo_dominio_atributo,
                es_fuente_primaria=True,
                clase_texto=_texto_o_none(fila["clase"]),
                servidor_texto=_texto_o_none(fila["servidor_fuente_oficial"]),
                base_datos_texto=_texto_o_none(fila["base_datos_fuente_oficial"]),
                nombre_campo=str(fila["nombre_campo_fuente_oficial"]).strip(),
                longitud_campo=str(fila["longitud_campo_fuente_oficial"]).strip(),
                tipo_campo_texto=_texto_o_none(fila["tipo_campo"]),
                lista_valores_validos_texto=_texto_o_none(fila["lista_valores_validos"]),
                acepta_valores_nulos=_bit_si_no(fila["acepta_valores_nulos"]),
                formula_calculo=_texto_o_none(fila["formula_calculo"]),
                fecha_aprobacion=fecha,
                nombre_tabla=_texto_o_none(fila.get("tabla_fuente_oficial")),
            )
        )

    return resultado


@dataclass(frozen=True)
class ConsumoPendiente:
    """Una fila destino de gdd.atributo_fuente_consumo, normalizada.

    A diferencia de la fuente "_oficial" (de DONDE se carga un atributo),
    esto describe DONDE se consulta ese mismo atributo despues de la
    transformacion de carga a consumo -- confirmado con el usuario
    2026-09-17: no es "otra fuente mas" del mismo tipo, es el extremo
    opuesto de la trazabilidad (zona de carga -> zona de consumo). La
    cardinalidad es directa con el ATRIBUTO, no con cada fila de fuente
    oficial -- por eso mapear_atributo_fuente_consumo agrupa por
    codigo_dominio_atributo y deduplica, a diferencia de
    mapear_metadata_tecnica (que genera 1 o 2 filas POR CADA fila de
    staging).

    REFACTOR 2026-09-18 (la prueba con negocio del diseno anterior -- texto
    libre en las 4 columnas -- no fue satisfactoria): coleccion_texto pasa de
    OPCIONAL a OBLIGATORIO. Antes era solo descriptivo; ahora resuelve
    gdd.base_datos_fuente (catalogo CONTROLADO, ver
    gdd_repository.resolver_bdd_controlado) -- sin ese valor no hay como
    identificar la coleccion/base de datos destino, asi que una fila sin
    coleccion_foc ya no es un destino de consumo identificable, igual que si le
    faltara servidor/tabla/campo. nombre_tabla puede terminar resuelto
    contra un catalogo controlado (coleccion) o guardado como texto libre
    (base de datos transaccional) -- esa decision se toma en
    pipeline/carga_gdd.py (necesita tipo, que solo se conoce tras resolver
    contra la BD), no aqui.

    RENAME 2026-09-21: el campo Excel/staging `clase_foc` paso a llamarse
    `coleccion_foc` (y este atributo `clase_texto` a `coleccion_texto`) --
    cambio SOLO de este flujo de consumo, confirmado con el usuario. Desde
    2026-10-01 las columnas "_foc" se leen UNICAMENTE aqui (ya no generan la
    fuente oficial secundaria de mapear_metadata_tecnica).

    servidor_texto/coleccion_texto/nombre_tabla/nombre_campo identifican el
    destino de consumo -- no se expone aqui una property `clave_natural`
    porque la clave real usa id_servidor/id_bdd/id_tabla, que solo se
    conocen tras resolver contra el catalogo (igual que
    FuenteOficialPendiente/id_bdd, ver pipeline/carga_gdd._clave_consumo).
    """

    codigo_dominio: str
    codigo_atributo: int
    codigo_dominio_atributo: str
    servidor_texto: str
    coleccion_texto: str
    nombre_tabla: str
    nombre_campo: str


def mapear_atributo_fuente_consumo(registros: list[dict]) -> list[ConsumoPendiente]:
    """`registros`: filas de staging.metadata_tecnica como dicts (la MISMA
    fuente que mapear_metadata_tecnica -- las columnas _foc viven en la
    misma hoja/tabla que las columnas _oficial, ver SheetConfig de
    MetadataTecnica en domain/sheet_config.py).

    Agrupa por codigo_dominio_atributo y deduplica por
    (servidor_foc, coleccion_foc, tabla_bv_foc, nombre_campo_foc) -- confirmado
    con el usuario 2026-09-17: la cardinalidad de consumo es directa con el
    atributo, y la plantilla puede repetir codigo_dominio_atributo en varias
    filas cuando un atributo tiene mas de un destino de consumo.

    Una fila solo cuenta como "destino de consumo real" si trae servidor_foc,
    coleccion_foc, tabla_bv_foc Y nombre_campo_foc (las 4 columnas que
    identifican DONDE se consulta el atributo) -- sin al menos esas 4 no hay
    destino identificable, se omite en vez de insertar una fila a medias.
    REFACTOR 2026-09-18: coleccion_foc paso de ser solo descriptivo a obligatorio
    (ver ConsumoPendiente) -- ahora forma parte de la clave de agrupacion,
    no solo un campo mutable, porque resuelve la coleccion/base de datos
    destino (id_bdd), que identifica al destino tanto como servidor/tabla/
    campo. Si el mismo (atributo, servidor, clase, tabla, campo) aparece mas
    de una vez (duplicado exacto), la ULTIMA fila del grupo gana -- no se
    reporta como error, describe el mismo destino de consumo.
    """
    vistos: dict[tuple[str, str, str, str, str], ConsumoPendiente] = {}
    for fila in registros:
        codigo_dominio_atributo = str(fila["codigo_dominio_atributo"]).strip()
        codigo_dominio, codigo_atributo = _dividir_codigo_dominio_atributo(codigo_dominio_atributo)

        servidor = _texto_o_none(fila.get("servidor_foc"))
        coleccion = _texto_o_none(fila.get("coleccion_foc"))
        tabla = _texto_o_none(fila.get("tabla_bv_foc"))
        campo = _texto_o_none(fila.get("nombre_campo_foc"))
        if servidor is None or coleccion is None or tabla is None or campo is None:
            continue

        clave = (codigo_dominio_atributo, servidor, coleccion, tabla, campo)
        vistos[clave] = ConsumoPendiente(
            codigo_dominio=codigo_dominio,
            codigo_atributo=codigo_atributo,
            codigo_dominio_atributo=codigo_dominio_atributo,
            servidor_texto=servidor,
            coleccion_texto=coleccion,
            nombre_tabla=tabla,
            nombre_campo=campo,
        )

    return list(vistos.values())


@dataclass(frozen=True)
class InvestigacionPendiente:
    """Una fila de staging.investigacion, normalizada. A diferencia de
    AtributoPendiente/FuenteOficialPendiente, esta hoja no trae fecha de
    aprobacion por fila -- el diff hacia gdd.investigacion (INSERTAR/
    ACTUALIZAR/ELIMINAR) se hace por clave natural (codigo_dominio, nombre)
    en vez de por fecha_aprobacion (ver gdd_repository.investigacion_existentes
    y pipeline/carga_gdd.ejecutar_merge_investigacion_dominio).
    codigo_dominio no viene en la hoja -- se hereda de DetalleAtributos al
    leer el Excel (ver pipeline/carga_staging._codigo_dominio_de_hoja), asi
    que ya llega resuelto en cada fila de staging.

    referencia_normativa (agregada 2026-09-16): texto libre largo, sin
    catalogo -- se guarda tal cual, mutable igual que descripcion (no es
    parte de la clave natural).
    """

    codigo_dominio: str
    clasificacion_texto: str
    nombre: str
    descripcion: str | None
    referencia_normativa: str | None


def mapear_investigacion(registros: list[dict]) -> list[InvestigacionPendiente]:
    """`registros`: filas de staging.investigacion como dicts. clasificacion
    y nombre son obligatorios (identifican la fila); descripcion y
    referencia_normativa pueden venir vacias.
    """
    resultado = []
    for fila in registros:
        clasificacion = _texto_o_none(fila["clasificacion"])
        nombre = _texto_o_none(fila["nombre"])
        if clasificacion is None or nombre is None:
            raise ValueError(
                "staging.investigacion: fila con 'clasificacion' o 'nombre' vacio "
                f"(codigo_dominio='{fila.get('codigo_dominio')}') -- ambos son obligatorios."
            )
        resultado.append(
            InvestigacionPendiente(
                codigo_dominio=str(fila["codigo_dominio"]).strip(),
                clasificacion_texto=clasificacion,
                nombre=nombre,
                descripcion=_texto_o_none(fila.get("descripcion")),
                referencia_normativa=_texto_o_none(fila.get("referencia_normativa")),
            )
        )
    return resultado


@dataclass(frozen=True)
class EstructuraPendiente:
    """Una fila de staging.estructura, normalizada. Alimenta
    gdd.dominio_responsable (quien es responsable de cada "plaza"/rol dentro
    del dominio). codigo_dominio no viene en la hoja -- se hereda de
    DetalleAtributos igual que Investigacion (ver
    pipeline/carga_staging._codigo_dominio_de_hoja). codigo_plaza es la
    clave natural de cada fila (junto con codigo_dominio) -- el diff hacia
    gdd (INSERTAR/ACTUALIZAR/ELIMINAR) se hace por esa clave, ver
    gdd_repository.dominio_responsable_existentes. fecha_aprobada ya llega
    como date (columnas_fecha la parsea en extract/excel_reader.py, igual
    que DetalleAtributos.fecha_aprobada) pero aqui es solo un dato
    informativo -- no dispara versionado tipo REEMPLAZAR.
    """

    codigo_dominio: str
    codigo_plaza: str
    rol_texto: str
    area: str | None
    nombre_plaza: str | None
    nombre_responsable: str | None
    fecha_aprobada: datetime.date | None


def mapear_estructura(registros: list[dict]) -> list[EstructuraPendiente]:
    """`registros`: filas de staging.estructura como dicts. rol y
    codigo_plaza son obligatorios (codigo_plaza identifica la fila);
    area/nombre_plaza/nombre_responsable/fecha_aprobada pueden venir vacios.
    """
    resultado = []
    for fila in registros:
        rol = _texto_o_none(fila["rol"])
        codigo_plaza = _texto_o_none(fila["codigo_plaza"])
        if rol is None or codigo_plaza is None:
            raise ValueError(
                "staging.estructura: fila con 'rol' o 'codigo_plaza' vacio "
                f"(codigo_dominio='{fila.get('codigo_dominio')}') -- ambos son obligatorios."
            )
        resultado.append(
            EstructuraPendiente(
                codigo_dominio=str(fila["codigo_dominio"]).strip(),
                codigo_plaza=codigo_plaza,
                rol_texto=rol,
                area=_texto_o_none(fila.get("area")),
                nombre_plaza=_texto_o_none(fila.get("nombre_plaza")),
                nombre_responsable=_texto_o_none(fila.get("nombre_responsable")),
                fecha_aprobada=fila.get("fecha_aprobada"),
            )
        )
    return resultado


@dataclass(frozen=True)
class RespaldoPendiente:
    """Una fila de staging.respaldos, normalizada. Alimenta gdd.respaldo
    (evidencia de aprobacion del dominio: correos, minutas, etc., un link
    por fila). codigo_dominio no viene en la hoja -- se hereda de
    DetalleAtributos igual que Investigacion/Estructura. Clave natural:
    (codigo_dominio, tipo_respaldo, enlace_respaldo) -- el Excel real trae
    mas de una fila con el MISMO enlace_respaldo diferenciadas solo por
    tipo_respaldo, ver gdd_repository.respaldo_existentes. fecha_aprobada
    se guarda como TEXTO LIBRE (a diferencia de Estructura/DetalleAtributos):
    el Excel real la trae en un formato no estandar ("2026/15/09"), asi que
    no se intenta parsear como fecha real -- se guarda tal cual viene.
    """

    codigo_dominio: str
    tipo_respaldo_texto: str
    enlace_respaldo: str
    observaciones: str | None
    fecha_aprobada: str | None


def mapear_respaldos(registros: list[dict]) -> list[RespaldoPendiente]:
    """`registros`: filas de staging.respaldos como dicts. tipo_respaldo y
    enlace_respaldo son obligatorios (juntos identifican la fila, con
    codigo_dominio); observaciones/fecha_aprobada pueden venir vacios.
    """
    resultado = []
    for fila in registros:
        tipo_respaldo = _texto_o_none(fila["tipo_respaldo"])
        enlace_respaldo = _texto_o_none(fila["enlace_respaldo"])
        if tipo_respaldo is None or enlace_respaldo is None:
            raise ValueError(
                "staging.respaldos: fila con 'tipo_respaldo' o 'enlace_respaldo' vacio "
                f"(codigo_dominio='{fila.get('codigo_dominio')}') -- ambos son obligatorios."
            )
        resultado.append(
            RespaldoPendiente(
                codigo_dominio=str(fila["codigo_dominio"]).strip(),
                tipo_respaldo_texto=tipo_respaldo,
                enlace_respaldo=enlace_respaldo,
                observaciones=_texto_o_none(fila.get("observaciones")),
                fecha_aprobada=_texto_o_none(fila.get("fecha_aprobada")),
            )
        )
    return resultado


@dataclass(frozen=True)
class PlanRemediacionPendiente:
    """Una fila de staging.plan_remediacion, normalizada. Alimenta
    gdd.plan_remediacion (plan de remediacion de un hallazgo, ligado a un
    ATRIBUTO especifico -- a diferencia de Investigacion/Estructura/
    Respaldos, que son del dominio completo). codigo_dominio_atributo SI
    viene en la hoja (ej. "ADS-4"), no se hereda de otra hoja -- se separa
    en (codigo_dominio, codigo_atributo) con `_dividir_codigo_dominio_atributo`
    para poder resolver el FK hacia gdd.atributo (ver
    gdd_repository.resolver_atributo_por_codigo). Clave natural:
    (codigo_dominio_atributo, id_problema) -- confirmado con el usuario.

    categoria y fuente_oficial_sistema son texto libre (sin catalogo ni
    tabla tecnica, decision explicita del usuario). dimension/tipo_plan/
    categoria_plan/subcategoria_plan/estado_actual SI son catalogos
    controlados (cat_tipo_dimension/cat_tipo_plan/cat_categoria_plan/
    cat_sub_categoria_plan/cat_estado_plan) -- quedan como *_texto, se
    resuelven en gdd_repository/carga_gdd igual que nivel_criticidad_texto
    en AtributoPendiente. siro se guarda como texto libre (varchar),
    decision explicita del usuario aunque el dato de origen sea numerico.

    fecha_identificacion y fecha_ultima_modificacion ya llegan como date
    (columnas_fecha las parsea en extract/excel_reader.py, celdas de fecha
    real de Excel). fecha_finalizacion_definitiva TAMBIEN llega ya parseada
    como date pese a ser una celda de TEXTO en el Excel real ("01/30/2026",
    formato "%m/%d/%Y" en columnas_fecha) -- se decidio parsearla a DATE
    real (no dejarla como texto) para cumplir el principio del usuario de
    que las fechas se guarden siempre en orden YYYY/MM/DD.
    """

    codigo_dominio: str
    codigo_atributo: int
    codigo_dominio_atributo: str
    id_problema: str
    dimension_texto: str | None
    categoria: str | None
    fuente_oficial_sistema: str | None
    tipo_plan_texto: str | None
    categoria_plan_texto: str | None
    subcategoria_plan_texto: str | None
    priorizacion: str | None  # texto libre desde 2026-10-02 (antes int)
    estado_actual_texto: str | None
    fecha_identificacion: datetime.date | None
    fecha_finalizacion_definitiva: datetime.date | None
    descripcion_causa_raiz: str | None
    persona_responsable: str | None
    dependencias: str | None
    observacion: str | None
    acciones_corto_plazo: str | None
    acciones_definitivo: str | None
    avance: int | None
    fecha_ultima_modificacion: datetime.date | None
    siro: str | None


def mapear_plan_remediacion(registros: list[dict]) -> list[PlanRemediacionPendiente]:
    """`registros`: filas de staging.plan_remediacion como dicts.
    codigo_dominio_atributo e id_problema son obligatorios (juntos
    identifican la fila); el resto puede venir vacio.
    """
    resultado = []
    for fila in registros:
        codigo_dominio_atributo = _texto_o_none(fila["codigo_dominio_atributo"])
        id_problema = _texto_o_none(fila["id_problema"])
        if codigo_dominio_atributo is None or id_problema is None:
            raise ValueError(
                "staging.plan_remediacion: fila con 'codigo_dominio_atributo' o "
                f"'id_problema' vacio (codigo_dominio_atributo='{fila.get('codigo_dominio_atributo')}') "
                "-- ambos son obligatorios."
            )
        codigo_dominio, codigo_atributo = _dividir_codigo_dominio_atributo(codigo_dominio_atributo)
        resultado.append(
            PlanRemediacionPendiente(
                codigo_dominio=codigo_dominio,
                codigo_atributo=codigo_atributo,
                codigo_dominio_atributo=codigo_dominio_atributo,
                id_problema=id_problema,
                dimension_texto=_texto_o_none(fila.get("dimension")),
                categoria=_texto_o_none(fila.get("categoria")),
                fuente_oficial_sistema=_texto_o_none(fila.get("fuente_oficial_sistema")),
                tipo_plan_texto=_texto_o_none(fila.get("tipo_plan")),
                categoria_plan_texto=_texto_o_none(fila.get("categoria_plan")),
                subcategoria_plan_texto=_texto_o_none(fila.get("subcategoria_plan")),
                priorizacion=_texto_o_none(fila.get("priorizacion")),
                estado_actual_texto=_texto_o_none(fila.get("estado_actual")),
                fecha_identificacion=fila.get("fecha_identificacion"),
                fecha_finalizacion_definitiva=fila.get("fecha_finalizacion_definitiva"),
                descripcion_causa_raiz=_texto_o_none(fila.get("descripcion_causa_raíz")),
                persona_responsable=_texto_o_none(
                    fila.get("persona_responsable_ejecución_plan_remediación")
                ),
                dependencias=_texto_o_none(fila.get("dependencias")),
                observacion=_texto_o_none(fila.get("observacion")),
                acciones_corto_plazo=_texto_o_none(fila.get("acciones_resolucion_corto_plazo")),
                acciones_definitivo=_texto_o_none(fila.get("acciones_resolucion_definitivo")),
                avance=_entero_o_none(fila.get("avance")),
                fecha_ultima_modificacion=fila.get("fecha_ultima_modificacion"),
                siro=_texto_o_none(fila.get("siro")),
            )
        )
    return resultado

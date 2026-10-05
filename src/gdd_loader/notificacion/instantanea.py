"""
Foto legible de lo ACTIVO de un dominio en gdd.*, para la minuta de cambios.

Por que una foto antes/despues y no capturar dentro del merge:
- No toca la logica de merge (pipeline/carga_gdd.py), que ya esta probada.
- Refleja solo lo que realmente quedo confirmado en la base: si una
  transaccion del merge hace rollback, su parte simplemente no aparece como
  cambio (sirve igual para MERGE_OK y MERGE_PARCIAL).
- Los catalogos se resuelven a TEXTO (criticidad, rol, tipo de respaldo...),
  que es lo que necesita leer quien aprueba, no los id internos.

Cada entidad se indexa por su CLAVE NATURAL (la misma que usa el merge, pero
expresada en texto: servidor/base de datos en vez de id_bdd). Un REEMPLAZAR
(fila nueva por fecha de aprobacion mas reciente) conserva la clave natural,
asi que aparece como MODIFICADO con la version anterior y la nueva.

Las consultas siguen las mismas uniones ya usadas en
claude/reportes-consultas-gdd.sql (A-02, A-06, B-01, B-02, E-01..E-04).
Solo SELECT: nunca escribe en la base.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Callable

from sqlalchemy import text

# --- Modelo -------------------------------------------------------------------


@dataclass(frozen=True)
class Registro:
    etiqueta: str                       # texto que identifica la fila en la minuta
    campos: dict[str, str] = field(default_factory=dict)  # etiqueta de campo -> valor (texto); se comparan
    tabla: dict[str, str] = field(default_factory=dict)   # columna -> valor, fila de las tablas de nuevos/bajas


# entidad -> {clave natural (tupla de textos) -> Registro}
Instantanea = dict[str, dict[tuple, Registro]]


@dataclass(frozen=True)
class EntidadMinuta:
    nombre: str                          # titulo en la minuta
    sql: str                             # recibe :cod (codigo de dominio)
    clave: tuple[str, ...]               # columnas del SELECT que forman la clave natural
    etiqueta: Callable[[dict], str]      # fila (valores ya en texto) -> etiqueta legible
    campos: dict[str, str]               # columna del SELECT -> etiqueta de campo (se comparan)
    # columna del SELECT -> encabezado, para las tablas de nuevos / dados de baja
    # (subconjunto legible: identificacion + campos principales, para que la
    # tabla quepa en el ancho de un correo).
    columnas_tabla: dict[str, str] = field(default_factory=dict)


# --- Normalizacion de valores a texto -------------------------------------------


def a_texto(valor) -> str:
    """Valor de SQL Server -> texto estable para comparar y mostrar.

    None y '' quedan igual (''), para que NULL <-> vacio no cuente como cambio.
    """
    if valor is None:
        return ""
    if isinstance(valor, bool):
        return "Sí" if valor else "No"
    if isinstance(valor, datetime.datetime):
        if valor.time() == datetime.time(0, 0):
            return valor.date().isoformat()
        return valor.isoformat(sep=" ", timespec="seconds")
    if isinstance(valor, datetime.date):
        return valor.isoformat()
    if isinstance(valor, Decimal):
        texto = format(valor.normalize(), "f")
        return texto.rstrip("0").rstrip(".") if "." in texto else texto
    if isinstance(valor, float):
        return str(int(valor)) if valor.is_integer() else repr(valor)
    return str(valor).strip()


def _con(*partes: str, sep: str = " – ") -> str:
    return sep.join(p for p in partes if p)


# --- Definicion de las 7 entidades ----------------------------------------------

ATRIBUTOS = EntidadMinuta(
    nombre="Atributos",
    sql="""
        SELECT a.codigo_atributo, a.nombre_atributo, a.descripcion_atributo,
               cr.nombre_criticidad        AS criticidad,
               ta.nombre_tipo_atributo     AS tipo_atributo,
               cs.nombre_compartido_similar AS compartido_similar,
               a.es_dato_personal,
               dp.codificacion             AS clasificacion_dato_personal,
               a.reporte, a.proyecto, a.proceso, a.estructura,
               ap.nombre_atributo          AS atributo_primario,
               dpp.nombre_dominio          AS dominio_primario,
               a.fecha_aprobacion, a.version, a.[release] AS release_atributo
        FROM gdd.atributo a
        LEFT JOIN gdd.cat_criticidad cr         ON cr.id = a.id_criticidad
        LEFT JOIN gdd.cat_tipo_atributo ta      ON ta.id = a.id_tipo_atributo
        LEFT JOIN gdd.cat_compartido_similar cs ON cs.id = a.id_compartido_similar
        LEFT JOIN gdd.cat_dato_personal dp      ON dp.id = a.id_dato_personal
        LEFT JOIN gdd.atributo ap               ON ap.id = a.id_atributo_primario
        LEFT JOIN gdd.dominio_dato dpp          ON dpp.id = a.id_dominio_primario
        WHERE a.codigo_dominio = :cod AND a.activo = 1
    """,
    clave=("codigo_atributo",),
    etiqueta=lambda f: _con(f["codigo_atributo"], f["nombre_atributo"]),
    campos={
        "nombre_atributo": "Nombre",
        "descripcion_atributo": "Descripción",
        "criticidad": "Criticidad",
        "tipo_atributo": "Tipo de atributo",
        "compartido_similar": "Compartido / similar",
        "es_dato_personal": "Dato personal",
        "clasificacion_dato_personal": "Clasificación dato personal",
        "reporte": "Reporte",
        "proyecto": "Proyecto",
        "proceso": "Proceso",
        "estructura": "Estructura",
        "atributo_primario": "Atributo primario",
        "dominio_primario": "Dominio primario",
        "fecha_aprobacion": "Fecha de aprobación",
        "version": "Versión",
        "release_atributo": "Release",
    },
    columnas_tabla={
        "codigo_atributo": "Código",
        "nombre_atributo": "Nombre",
        "descripcion_atributo": "Descripción",
        "criticidad": "Criticidad",
        "tipo_atributo": "Tipo",
        "es_dato_personal": "Dato personal",
        "fecha_aprobacion": "Fecha de aprobación",
        "version": "Versión",
    },
)

# version de la fuente SI se compara (decision 2026-09-29): cuando el atributo
# padre cambia de version (REEMPLAZAR), el merge reinserta sus fuentes con
# version "1" -- comportamiento definido, y la minuta debe informarlo como
# cambio de version de la fuente.
FUENTES_OFICIALES = EntidadMinuta(
    nombre="Fuentes oficiales",
    sql="""
        SELECT a.codigo_atributo, a.nombre_atributo,
               sv.nombre_servidor AS servidor, bd.nombre_bdd AS base_datos,
               f.nombre_tabla AS tabla, f.clase, f.nombre_campo, f.es_fuente_primaria,
               f.longitud_campo, vv.valor AS tipo_campo, f.acepta_valores_nulos,
               f.formula_calculo, f.lista_valores_validos, f.fecha_aprobacion, f.version
        FROM gdd.atributo_fuente_oficial f
        JOIN gdd.atributo a           ON a.id = f.id_atributo
        JOIN gdd.base_datos_fuente bd ON bd.id = f.id_bdd
        JOIN gdd.servidor_fuente sv   ON sv.id = bd.id_servidor
        LEFT JOIN gdd.campo_fuente_valor_valido vv ON vv.id = f.id_campo_fuente_valor_valido
        WHERE a.codigo_dominio = :cod AND a.activo = 1 AND f.activo = 1
    """,
    clave=("codigo_atributo", "servidor", "base_datos", "tabla", "clase", "nombre_campo",
           "es_fuente_primaria"),
    etiqueta=lambda f: _con(
        f"Atributo {f['codigo_atributo']}",
        "/".join(p for p in (f["servidor"], f["base_datos"], f["tabla"], f["clase"], f["nombre_campo"])
                 if p)
        + (" (primaria)" if f["es_fuente_primaria"] == "Sí" else ""),
    ),
    campos={
        "longitud_campo": "Longitud",
        "tipo_campo": "Tipo de campo",
        "acepta_valores_nulos": "Acepta nulos",
        "formula_calculo": "Fórmula de cálculo",
        "lista_valores_validos": "Valores válidos",
        "fecha_aprobacion": "Fecha de aprobación",
        "version": "Versión",
    },
    columnas_tabla={
        "codigo_atributo": "Atributo",
        "nombre_atributo": "Nombre del atributo",
        "servidor": "Servidor",
        "base_datos": "Base de datos",
        "tabla": "Tabla / archivo",
        "clase": "Clase",
        "nombre_campo": "Campo",
        "es_fuente_primaria": "Primaria",
        "tipo_campo": "Tipo de campo",
        "longitud_campo": "Longitud",
        "version": "Versión",
    },
)

# Todas sus columnas son clave: solo puede haber altas y bajas.
FUENTES_CONSUMO = EntidadMinuta(
    nombre="Fuentes de consumo",
    sql="""
        SELECT a.codigo_atributo, a.nombre_atributo,
               sv.nombre_servidor AS servidor, bd.nombre_bdd AS base_datos,
               t.nombre_tabla AS tabla, c.nombre_campo
        FROM gdd.atributo_fuente_consumo c
        JOIN gdd.atributo a                ON a.id = c.id_atributo
        JOIN gdd.base_datos_fuente_tabla t ON t.id = c.id_tabla
        JOIN gdd.base_datos_fuente bd      ON bd.id = c.id_bdd
        JOIN gdd.servidor_fuente sv        ON sv.id = bd.id_servidor
        WHERE a.codigo_dominio = :cod AND a.activo = 1 AND c.activo = 1
    """,
    clave=("codigo_atributo", "servidor", "base_datos", "tabla", "nombre_campo"),
    etiqueta=lambda f: _con(
        f"Atributo {f['codigo_atributo']}",
        "/".join(p for p in (f["servidor"], f["base_datos"], f["tabla"], f["nombre_campo"]) if p),
    ),
    campos={},
    columnas_tabla={
        "codigo_atributo": "Atributo",
        "nombre_atributo": "Nombre del atributo",
        "servidor": "Servidor",
        "base_datos": "Base de datos / colección",
        "tabla": "Tabla",
        "nombre_campo": "Campo",
    },
)

RESPONSABLES = EntidadMinuta(
    nombre="Responsables",
    sql="""
        SELECT r.codigo_plaza, r.nombre_plaza, r.nombre_responsable,
               cr.nombre_rol AS rol, r.area, r.fecha_aprobada
        FROM gdd.dominio_responsable r
        JOIN gdd.dominio_dato d  ON d.id = r.id_dominio
        LEFT JOIN gdd.cat_rol cr ON cr.id = r.id_rol
        WHERE d.codigo_dominio = :cod AND r.activo = 1
    """,
    clave=("codigo_plaza",),
    etiqueta=lambda f: _con(f"Plaza {f['codigo_plaza']}", f["nombre_responsable"], f["rol"]),
    campos={
        "nombre_responsable": "Responsable",
        "rol": "Rol",
        "nombre_plaza": "Plaza",
        "area": "Área",
        "fecha_aprobada": "Fecha aprobada",
    },
    columnas_tabla={
        "codigo_plaza": "Código de plaza",
        "nombre_plaza": "Plaza",
        "nombre_responsable": "Responsable",
        "rol": "Rol",
        "area": "Área",
        "fecha_aprobada": "Fecha aprobada",
    },
)

INVESTIGACION = EntidadMinuta(
    nombre="Investigación",
    sql="""
        SELECT i.nombre, ci.nombre_clasificacion AS clasificacion,
               i.descripcion, i.referencia_normativa
        FROM gdd.investigacion i
        LEFT JOIN gdd.cat_clasificacion_investigacion ci ON ci.id = i.id_clasificacion
        WHERE i.codigo_dominio = :cod AND i.activo = 1
    """,
    clave=("nombre",),
    etiqueta=lambda f: _con(f["nombre"], f["clasificacion"]),
    campos={
        "clasificacion": "Clasificación",
        "descripcion": "Descripción",
        "referencia_normativa": "Referencia normativa",
    },
    columnas_tabla={
        "nombre": "Nombre",
        "clasificacion": "Clasificación",
        "descripcion": "Descripción",
        "referencia_normativa": "Referencia normativa",
    },
)

RESPALDOS = EntidadMinuta(
    nombre="Respaldos",
    sql="""
        SELECT tr.nombre_tipo_respaldo AS tipo_respaldo, p.enlace_respaldo,
               p.observaciones, p.fecha_aprobada
        FROM gdd.respaldo p
        LEFT JOIN gdd.cat_tipo_respaldo tr ON tr.id = p.id_tipo_respaldo
        WHERE p.codigo_dominio = :cod AND p.activo = 1
    """,
    clave=("tipo_respaldo", "enlace_respaldo"),
    etiqueta=lambda f: _con(f["tipo_respaldo"], f["enlace_respaldo"], sep=": "),
    campos={
        "observaciones": "Observaciones",
        "fecha_aprobada": "Fecha aprobada",
    },
    columnas_tabla={
        "tipo_respaldo": "Tipo",
        "enlace_respaldo": "Enlace",
        "observaciones": "Observaciones",
        "fecha_aprobada": "Fecha aprobada",
    },
)

PLANES_REMEDIACION = EntidadMinuta(
    nombre="Planes de remediación",
    sql="""
        SELECT r.codigo_dominio_atributo, r.id_problema, a.nombre_atributo,
               td.nombre_tipo_dimension AS dimension, r.categoria, r.fuente_oficial_sistema,
               tp.nombre_tipo_plan AS tipo_plan, cp.nombre_categoria_plan AS categoria_plan,
               scp.nombre_sub_categoria_plan AS sub_categoria_plan, r.priorizacion,
               ep.nombre_estado_plan AS estado_plan, r.fecha_identificacion,
               r.fecha_finalizacion_definitiva, r.descripcion_causa_raiz,
               r.persona_responsable, r.dependencias, r.observacion,
               r.acciones_corto_plazo, r.acciones_definitivo, r.avance,
               r.fecha_ultima_modificacion, r.siro
        FROM gdd.plan_remediacion r
        JOIN gdd.atributo a ON a.id = r.id_atributo
        LEFT JOIN gdd.cat_tipo_dimension td      ON td.id = r.id_dimension
        LEFT JOIN gdd.cat_tipo_plan tp            ON tp.id = r.id_tipo_plan
        LEFT JOIN gdd.cat_categoria_plan cp       ON cp.id = r.id_categoria_plan
        LEFT JOIN gdd.cat_sub_categoria_plan scp  ON scp.id = r.id_sub_categoria_plan
        LEFT JOIN gdd.cat_estado_plan ep          ON ep.id = r.id_estado_plan
        WHERE r.codigo_dominio = :cod AND r.activo = 1
    """,
    clave=("codigo_dominio_atributo", "id_problema"),
    etiqueta=lambda f: _con(
        f"{f['codigo_dominio_atributo']} problema {f['id_problema']}", f["nombre_atributo"]),
    campos={
        "dimension": "Dimensión",
        "categoria": "Categoría",
        "fuente_oficial_sistema": "Fuente oficial / sistema",
        "tipo_plan": "Tipo de plan",
        "categoria_plan": "Categoría del plan",
        "sub_categoria_plan": "Subcategoría del plan",
        "priorizacion": "Priorización",
        "estado_plan": "Estado",
        "fecha_identificacion": "Fecha de identificación",
        "fecha_finalizacion_definitiva": "Fecha de finalización definitiva",
        "descripcion_causa_raiz": "Causa raíz",
        "persona_responsable": "Persona responsable",
        "dependencias": "Dependencias",
        "observacion": "Observación",
        "acciones_corto_plazo": "Acciones de corto plazo",
        "acciones_definitivo": "Acciones definitivas",
        "avance": "Avance",
        "fecha_ultima_modificacion": "Fecha última modificación",
        "siro": "SIRO",
    },
    columnas_tabla={
        "codigo_dominio_atributo": "Atributo",
        "id_problema": "Problema",
        "nombre_atributo": "Nombre del atributo",
        "dimension": "Dimensión",
        "tipo_plan": "Tipo de plan",
        "estado_plan": "Estado",
        "priorizacion": "Priorización",
        "fecha_finalizacion_definitiva": "Fecha de finalización",
        "persona_responsable": "Persona responsable",
        "avance": "Avance",
    },
)

# Orden en que aparecen en la minuta.
ENTIDADES: tuple[EntidadMinuta, ...] = (
    ATRIBUTOS, FUENTES_OFICIALES, FUENTES_CONSUMO, RESPONSABLES,
    INVESTIGACION, RESPALDOS, PLANES_REMEDIACION,
)


# --- Construccion ---------------------------------------------------------------


def construir_registros(entidad: EntidadMinuta, filas) -> dict[tuple, Registro]:
    """Filas (mappings) de la consulta -> {clave natural: Registro}. Pura, testeable."""
    resultado: dict[tuple, Registro] = {}
    for fila in filas:
        texto = {k: a_texto(v) for k, v in dict(fila).items()}
        clave = tuple(texto[c] for c in entidad.clave)
        resultado[clave] = Registro(
            etiqueta=entidad.etiqueta(texto),
            campos={etq: texto.get(col, "") for col, etq in entidad.campos.items()},
            tabla={etq: texto.get(col, "") for col, etq in entidad.columnas_tabla.items()},
        )
    return resultado


def tomar_instantanea(conn, codigo_dominio: str) -> Instantanea:
    """Solo lectura. `conn`: Connection de SQLAlchemy."""
    return {
        e.nombre: construir_registros(
            e, conn.execute(text(e.sql), {"cod": codigo_dominio}).mappings().all())
        for e in ENTIDADES
    }


def nombre_dominio(conn, codigo_dominio: str) -> str | None:
    fila = conn.execute(
        text("SELECT nombre_dominio FROM gdd.dominio_dato WHERE codigo_dominio = :cod"),
        {"cod": codigo_dominio},
    ).first()
    return a_texto(fila[0]) or None if fila is not None else None


def responsables_a_notificar(conn, codigo_dominio: str, id_rol: int) -> list[str]:
    """Nombres de los responsables ACTIVOS del dominio con el rol dado
    (id_rol = 1 por defecto, GDD_NOTIF_ID_ROL). El flujo de Power Automate
    resuelve cada nombre a correo en el directorio (dominio_responsable no
    tiene columna de correo)."""
    filas = conn.execute(
        text(
            "SELECT DISTINCT r.nombre_responsable "
            "FROM gdd.dominio_responsable r "
            "JOIN gdd.dominio_dato d ON d.id = r.id_dominio "
            "WHERE d.codigo_dominio = :cod AND r.activo = 1 AND r.id_rol = :rol "
            "AND r.nombre_responsable IS NOT NULL"
        ),
        {"cod": codigo_dominio, "rol": id_rol},
    ).all()
    return sorted({a_texto(f[0]) for f in filas if a_texto(f[0])})

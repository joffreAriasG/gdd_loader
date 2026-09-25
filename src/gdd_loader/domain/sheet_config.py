"""
Capa de dominio: describe QUE se carga, sin saber COMO se lee (Excel)
ni COMO se guarda (SQL Server). Agregar una hoja/plantilla nueva es
agregar un SheetConfig aqui, no tocar el resto del proyecto.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SheetConfig:
    nombre_hoja: str
    tabla_staging: str  # "staging.tabla", incluye el schema
    columnas: list[str]
    # columna -> tipo pandas destino (ej. "Int64") para las columnas que
    # no son texto plano en el staging. Se aplica despues de leer.
    tipos: dict[str, str] = field(default_factory=dict)
    # Columna usada para acotar el borrado cuando GDD_ESTRATEGIA_STAGING=DOMINIO:
    # se borran de staging solo las filas cuyo valor en esta columna aparece
    # en el archivo que se esta cargando, dejando intactos otros dominios ya
    # cargados en la misma tabla. Requerida si se usa esa estrategia.
    columna_clave: str = ""
    # Bug real detectado 2026-09-17: cuando columna_clave es una clave
    # COMPUESTA "codigo_dominio-codigo_atributo" (ej. "ADS-4", como en
    # MetadataTecnica/PlanDeRemediacion), el borrado por IN-list de valores
    # presentes en el archivo (ver staging_repository.cargar) NO borra una
    # fila vieja cuyo codigo_dominio_atributo desaparecio POR COMPLETO del
    # archivo (ese valor ya no esta en ningun lado del archivo, asi que
    # nunca aparece en la lista a borrar) -- queda huerfana en staging para
    # siempre, y el merge la sigue viendo como vigente (nunca llega a
    # ELIMINAR). Confirmado por el usuario con una prueba real (borro una
    # fila de PlanDeRemediacion, la fila en gdd.plan_remediacion se quedo
    # activo=1 sin fecha_baja). columna_clave_prefijo_dominio=True hace que
    # el borrado sea por PREFIJO de dominio (LIKE 'ADS-%'), igual de
    # completo que el borrado por codigo_dominio exacto de las demas hojas
    # -- borra TODO el dominio de esa tabla antes de reinsertar, sin
    # depender de que cada valor puntual siga presente en el archivo.
    columna_clave_prefijo_dominio: bool = False
    # columna -> formato de fecha exacto (strptime, ej. "%d-%m-%Y") para
    # columnas de fecha en la plantilla. Se parsea de forma explicita (no con
    # el parser automatico de pandas) para no interpretar mal fechas
    # ambiguas como dd-mm-yyyy vs mm-dd-yyyy. El resultado queda como
    # datetime.date, listo para insertarse en una columna SQL Server DATE.
    columnas_fecha: dict[str, str] = field(default_factory=dict)
    # nombre_hoja de OTRA hoja del mismo archivo de la que se hereda
    # codigo_dominio, para una hoja que no trae esa columna propia (ej.
    # "Investigacion" hereda de "DetalleAtributos"). Vacio = la hoja trae su
    # propio codigo_dominio (o su propia columna clave), como hasta ahora.
    # Ver pipeline/carga_staging.py (_codigo_dominio_de_hoja): falla si la
    # hoja fuente trae mas de un codigo_dominio distinto en el archivo.
    hereda_codigo_dominio_de: str = ""


# Plantilla: Administracion De Seguros (dominio ADS).
# Para un nuevo dominio/plantilla, agregar sus SheetConfig a esta lista
# (o construir la lista dinamicamente si en el futuro hay muchas).
HOJAS_ADS: list[SheetConfig] = [
    SheetConfig(
        nombre_hoja="DetalleAtributos",
        tabla_staging="staging.detalle_atributos",
        columnas=[
            "codigo_dominio", "codigo_atributo", "atributo",
            "descripcion_atributo", "nivel_criticidad", "reporte",
            "proyecto", "proceso", "estructura", "es_dato_personal",
            "categoria_nivel_1", "tipo_dato", "sensibilidad",
            "tipo_atributo", "compartido_similar", "atributo_primario",
            "dominio_primario", "fecha_aprobada",
        ],
        tipos={"codigo_atributo": "Int64"},
        columna_clave="codigo_dominio",
        # fecha_aprobada: hasta 2026-09-16 la plantilla traia esta celda como
        # TEXTO libre "DD-MM-YYYY". El usuario cambio la celda a una fecha
        # real de Excel -- con dtype=str eso se lee como texto ISO con hora
        # ("2026-01-31 00:00:00", igual que Estructura.fecha_aprobada). Cada
        # carga relee la hoja completa de cero (sin acumular formatos
        # viejos), asi que no hace falta soportar el formato anterior aqui.
        columnas_fecha={"fecha_aprobada": "%Y-%m-%d %H:%M:%S"},
    ),
    SheetConfig(
        # metadata_tecnica.fecha_aprobada se mantiene como texto (varchar) a
        # proposito: esta tabla sera reemplazada mas adelante, la version
        # "principal" de fecha_aprobada como DATE vive en detalle_atributos.
        #
        # RENAME 2026-09-21: el nombre de la hoja en la plantilla Excel real
        # paso de "MetadataTécnica" (con tilde) a "MetadataTecnica" (sin
        # tilde) -- mismo motivo y mismo mecanismo que el rename de
        # PlanDeRemediación -> PlanDeRemediacion (nombre_hoja debe calzar
        # EXACTO con la pestaña del Excel).
        nombre_hoja="MetadataTecnica",
        tabla_staging="staging.metadata_tecnica",
        columnas=[
            "codigo_dominio_atributo", "clase", "formula_calculo",
            "servidor_fuente_oficial", "base_datos_fuente_oficial",
            "tabla_fuente_oficial", "nombre_campo_fuente_oficial",
            "longitud_campo_fuente_oficial", "lista_valores_validos",
            "acepta_valores_nulos", "tipo_campo", "coleccion_foc",
            "servidor_foc", "tabla_bv_foc", "nombre_campo_foc",
            "fecha_aprobada",
        ],
        columna_clave="codigo_dominio_atributo",
        # Ver nota de columna_clave_prefijo_dominio en la clase: sin esto,
        # una fila cuyo codigo_dominio_atributo desaparece por completo del
        # archivo (ej. se borraron TODAS las fuentes de un atributo) queda
        # huerfana en staging.metadata_tecnica para siempre.
        columna_clave_prefijo_dominio=True,
    ),
    SheetConfig(
        # Investigacion: material de referencia (normativas, procesos,
        # proyectos, reportes, terminos) que respalda la definicion del
        # dominio -- no esta ligada a un atributo puntual. A diferencia de
        # las otras dos hojas, no trae codigo_dominio propio (se hereda de
        # DetalleAtributos, ver hereda_codigo_dominio_de) ni fecha de
        # aprobacion por fila -- se mergea con diff INSERTAR/ACTUALIZAR/
        # ELIMINAR por clave natural (codigo_dominio, nombre), ver
        # gdd_repository.investigacion_existentes. referencia_normativa
        # (agregada 2026-09-16, alcance ampliado por el usuario): texto
        # largo, sin catalogo -- se guarda tal cual, campo mutable mas (no
        # es parte de la clave natural).
        nombre_hoja="Investigacion",
        tabla_staging="staging.investigacion",
        columnas=["clasificacion", "nombre", "descripcion", "referencia_normativa"],
        columna_clave="codigo_dominio",
        hereda_codigo_dominio_de="DetalleAtributos",
    ),
    SheetConfig(
        # Estructura: gobierno del dominio -- quien es responsable de cada
        # "plaza"/rol (Data Steward, Dueño de Dominio, etc.). Alimenta
        # gdd.dominio_responsable. Igual que Investigacion, no trae
        # codigo_dominio propio (se hereda de DetalleAtributos). codigo_plaza
        # identifica la plaza de forma unica dentro del dominio -- es la
        # clave natural del diff INSERTAR/ACTUALIZAR/ELIMINAR (ver
        # gdd_repository.dominio_responsable_existentes). fecha_aprobada se
        # guarda como dato informativo, no dispara versionado (a diferencia
        # de DetalleAtributos).
        nombre_hoja="Estructura",
        tabla_staging="staging.estructura",
        columnas=[
            "rol", "area", "codigo_plaza", "nombre_plaza",
            "nombre_responsable", "fecha_aprobada",
        ],
        columna_clave="codigo_dominio",
        columnas_fecha={"fecha_aprobada": "%Y-%m-%d %H:%M:%S"},
        hereda_codigo_dominio_de="DetalleAtributos",
    ),
    SheetConfig(
        # Respaldos: evidencia de aprobacion del dominio (correos, minutas,
        # etc. -- un link a Confluence/SharePoint por fila). Alimenta
        # gdd.respaldo. Igual que Investigacion/Estructura, no trae
        # codigo_dominio propio (se hereda de DetalleAtributos). Clave
        # natural: (codigo_dominio, tipo_respaldo, enlace_respaldo) -- el
        # Excel real trae mas de una fila con el MISMO enlace_respaldo
        # diferenciadas solo por tipo_respaldo (ej. "Correo" vs "Minuta"
        # apuntando a la misma pagina), asi que enlace_respaldo solo no
        # alcanza como clave (ver gdd_repository.respaldo_existentes).
        # fecha_aprobada se guarda como TEXTO LIBRE (sin columnas_fecha) --
        # el Excel real la trae en un formato no estandar ("2026/15/09"),
        # a diferencia de Estructura donde es un date real de Excel.
        nombre_hoja="Respaldos",
        tabla_staging="staging.respaldos",
        columnas=["tipo_respaldo", "enlace_respaldo", "observaciones", "fecha_aprobada"],
        columna_clave="codigo_dominio",
        hereda_codigo_dominio_de="DetalleAtributos",
    ),
    SheetConfig(
        # PlanDeRemediacion: plan de remediacion de un hallazgo, ligado a un
        # ATRIBUTO especifico (no al dominio completo) -- a diferencia de
        # Investigacion/Estructura/Respaldos, codigo_dominio_atributo SI
        # viene en la hoja (ej. "ADS-4"), no se hereda de otra hoja.
        # Alimenta gdd.plan_remediacion, con FK hacia gdd.atributo resuelta
        # por (codigo_dominio, codigo_atributo) -- ver
        # gdd_repository.resolver_atributo_por_codigo. Clave natural:
        # (codigo_dominio_atributo, id_problema).
        #
        # fecha_identificacion/fecha_ultima_modificacion son fechas reales
        # de Excel (mismo formato que Estructura.fecha_aprobada,
        # "%Y-%m-%d %H:%M:%S" tras leerse con dtype=str). fecha_finalizacion_definitiva
        # es una celda de TEXTO en el Excel real ("01/30/2026", confirmado
        # por inspeccion cruda -- el number_format de la celda no calza con
        # el contenido), pero se parsea igual con columnas_fecha (formato
        # "%m/%d/%Y", el dia=30 confirma que el mes va primero) para que
        # quede como DATE real en vez de texto -- decision del usuario de
        # que las fechas se guarden siempre en orden YYYY/MM/DD (una
        # columna DATE de SQL Server siempre se representa en ese orden).
        #
        # RENAME 2026-09-21: el nombre de la hoja en la plantilla Excel real
        # paso de "PlanDeRemediación" (con tilde) a "PlanDeRemediacion" (sin
        # tilde) -- nombre_hoja debe calzar EXACTO con el nombre de la
        # pestaña de Excel (pd.read_excel(sheet_name=...) en
        # extract/excel_reader.py), sensible a acentos y mayusculas/
        # minusculas.
        nombre_hoja="PlanDeRemediacion",
        tabla_staging="staging.plan_remediacion",
        columnas=[
            "codigo_dominio_atributo", "dimension", "categoria",
            "fuente_oficial_sistema", "tipo_plan", "categoria_plan",
            "subcategoria_plan", "priorizacion", "id_problema",
            "estado_actual", "fecha_identificacion",
            "fecha_finalizacion_definitiva", "descripcion_causa_raíz",
            "persona_responsable_ejecución_plan_remediación", "dependencias",
            "observacion", "acciones_resolucion_corto_plazo",
            "acciones_resolucion_definitivo", "avance",
            "fecha_ultima_modificacion", "siro",
        ],
        tipos={"priorizacion": "Int64", "avance": "Int64"},
        columna_clave="codigo_dominio_atributo",
        # Ver nota de columna_clave_prefijo_dominio en la clase -- mismo
        # bug real corregido 2026-09-17: el usuario borro una fila de esta
        # hoja y, sin esto, la fila vieja se quedaba huerfana en
        # staging.plan_remediacion (nunca se borraba porque su
        # codigo_dominio_atributo ya no aparecia en el archivo), y el merge
        # nunca la marcaba como ELIMINAR (activo=1, sin fecha_baja).
        columna_clave_prefijo_dominio=True,
        columnas_fecha={
            "fecha_identificacion": "%Y-%m-%d %H:%M:%S",
            "fecha_finalizacion_definitiva": "%m/%d/%Y",
            "fecha_ultima_modificacion": "%Y-%m-%d %H:%M:%S",
        },
    ),
]

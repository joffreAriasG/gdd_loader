"""
Capa de persistencia hacia el esquema `gdd` (destino final, normalizado).
Unico modulo que hace SELECT/INSERT/UPDATE/DELETE contra gdd.*. Recibe
siempre una `Connection` de SQLAlchemy ya abierta (no un Engine): quien
orquesta el merge (`pipeline/carga_gdd.py`) controla la transaccion
completa por dominio -- todo o nada.

Catalogos controlados (cat_criticidad, cat_tipo_atributo,
cat_compartido_similar, cat_dato_personal, cat_tipo_dominio,
cat_estado_dominio, cat_rol, cat_clasificacion_investigacion) y
`dominio_dato`: se asume que YA EXISTEN, mantenidos por otro proceso/equipo.
Si un valor del Excel no calza con el catalogo, esto es un problema de
CALIDAD DE DATO real y se reporta como error -- nunca se crea la fila
silenciosamente.

Catalogos tecnicos (servidor_fuente, base_datos_fuente,
campo_fuente_valor_valido): "get or create" -- se insertan sobre la marcha
si no existen, porque describen infraestructura tecnica descubierta desde
el Excel, no una taxonomia de gobierno.

Baja de atributo/atributo_fuente_oficial (2026-09-14): NUNCA se hace DELETE
fisico. Cuando un atributo o una fuente oficial desaparecen del Excel de
origen, se marcan inactivos (activo=0, fecha_baja=hoy) -- decision
deliberada: el Excel es la unica fuente de la comparacion (no hay un
umbral de seguridad que aborte por volumen de bajas), asi que un archivo
incompleto o con un error humano NO debe traducirse en perdida
irreversible de datos de gobierno. `atributos_existentes` y
`fuente_oficial_existentes` solo consideran filas con activo=1 como
"existentes" para el diff -- si un atributo dado de baja reaparece en una
carga posterior, se trata como alta nueva (fila e id nuevos); la fila
inactiva anterior queda como historial, nunca se reutiliza ni se reactiva.

gdd.investigacion (2026-09-14, revisado el mismo dia tras prueba real):
misma politica de baja logica que atributo/fuente_oficial, con diff por
clave natural (codigo_dominio, nombre) -- ver `investigacion_existentes`.

gdd.dominio_responsable (2026-09-15): mismo patron que investigacion --
diff por clave natural (codigo_dominio, codigo_plaza) y baja logica. A
diferencia de investigacion/atributo, esta tabla NO tiene columna
codigo_dominio propia (solo id_dominio, FK) -- el filtro por dominio se
hace siempre contra id_dominio. id_rol se resuelve contra gdd.cat_rol,
catalogo controlado igual que los demas cat_*.

gdd.respaldo (2026-09-15): evidencia de aprobacion del dominio (correos,
minutas, etc.). Clave natural (codigo_dominio, tipo_respaldo,
enlace_respaldo) -- a diferencia de investigacion/dominio_responsable, la
parte del catalogo (tipo_respaldo -> id_tipo_respaldo, contra
gdd.cat_tipo_respaldo) SI es parte de la clave, no solo un campo mutable
-- se resuelve ANTES de armar el plan de merge (mismo patron que
atributo_fuente_oficial/id_bdd, ver pipeline/carga_gdd._clave_respaldo),
para poder comparar contra lo existente por id ya resuelto en vez de por
texto. fecha_aprobada se guarda como texto libre (sin parsear), nunca
dispara versionado. Baja logica igual que el resto.

gdd.plan_remediacion (2026-09-15): plan de remediacion de un hallazgo,
ligado a un ATRIBUTO especifico (no al dominio completo como
investigacion/dominio_responsable/respaldo) -- FK id_atributo, resuelta por
(codigo_dominio, codigo_atributo) via `resolver_atributo_por_codigo`, un
catalogo controlado mas: si el atributo no existe (o esta dado de baja) en
gdd.atributo, es un problema real de orden de carga (el atributo debe
mergearse antes que su plan de remediacion) y se reporta como error, igual
que cualquier CatalogoNoEncontradoError. Clave natural: (codigo_dominio_atributo,
id_problema) -- se guarda codigo_dominio_atributo denormalizado (ademas de
codigo_dominio, para acotar la consulta de existentes por dominio igual que
respaldo) porque ya identifica al atributo sin necesidad de un JOIN.
categoria y fuente_oficial_sistema son texto libre (sin catalogo, decision
explicita del usuario). dimension/tipo_plan/categoria_plan/subcategoria_plan/
estado_actual SI son catalogos controlados (cat_tipo_dimension/cat_tipo_plan/
cat_categoria_plan/cat_sub_categoria_plan/cat_estado_plan). Mismo patron
INSERTAR/ACTUALIZAR/ELIMINAR (UPDATE in-place, sin fecha de aprobacion que
dispare REEMPLAZAR) y misma baja logica que investigacion/dominio_responsable/
respaldo. Sin bitacora (decision explicita del usuario, igual que las demas
hojas nuevas de este dia).

gdd.investigacion.referencia_normativa (2026-09-16): columna nueva agregada
por ampliacion de alcance -- texto libre largo, sin catalogo, mutable igual
que descripcion (no es parte de la clave natural (codigo_dominio, nombre)).

gdd.atributo_fuente_consumo (2026-09-17, alcance ampliado; REFACTORIZADA
2026-09-18): trazabilidad de CONSUMO de un atributo -- en que tabla/campo de
negocio se consulta, luego de la transformacion de carga a consumo (columnas
_foc de metadata_tecnica -- confirmado con el usuario que no es "otra fuente
mas" como atributo_fuente_oficial, es el extremo opuesto de la
trazabilidad). FK id_atributo igual que atributo_fuente_oficial. id_servidor
se resuelve (get-or-create, SIN CAMBIOS) contra el MISMO catalogo
gdd.servidor_fuente que usa la fuente oficial -- decision del usuario: son
los mismos servidores fisicos.

REFACTOR 2026-09-18 (la prueba con negocio del diseno de texto libre no fue
satisfactoria): coleccion_foc/tabla_bv_foc dejan de guardarse como texto libre y
se resuelven contra catalogos CONTROLADOS -- decision del usuario: "esas
tablas son controladas por listas que ingresan usuarios expertos... el
objetivo es que no deban haber duplicidad o crearse sin sentido".
- coleccion_foc -> gdd.base_datos_fuente.nombre_bdd, resuelto con
  `resolver_bdd_controlado` (CONTROLADO: error si no existe -- a diferencia
  de `resolver_o_crear_bdd`, que sigue get-or-create SIN CAMBIOS para el
  flujo de fuente oficial).
- tabla_bv_foc -> SIEMPRE se resuelve contra gdd.base_datos_fuente_tabla
  (`resolver_tabla_controlada`, tambien CONTROLADA) -- id_tabla es
  obligatorio (NOT NULL). CORRECCION 2026-09-18 (segunda vuelta, detectada
  por el usuario en la tabla resultante): la primera version de este
  refactor distinguia tipo='coleccion' (resuelve id_tabla) de
  tipo='base_datos' (texto libre en nombre_tabla_libre) -- esa distincion se
  elimino, el catalogo aplica siempre, sin excepcion de texto libre.
  gdd.base_datos_fuente.`tipo` se mantiene como dato de clasificacion pero
  ya no se usa para esta decision.
- nombre_campo_foc sigue como texto libre (confirmado: es el nombre del
  campo dentro de la tabla, no tiene sentido catalogarlo).
- id_servidor NO se guarda en atributo_fuente_consumo (CORRECCION 2026-09-18,
  segunda vuelta) -- la relacion servidor<->bdd ya existe via
  base_datos_fuente.id_servidor, exactamente el mismo patron que
  atributo_fuente_oficial (que tampoco tiene id_servidor propio, solo
  id_bdd). El servidor se sigue resolviendo en el codigo -- es el paso
  intermedio para encontrar id_bdd -- simplemente ya no queda como columna
  aparte.

Clave natural (dentro de un mismo id_atributo): (id_bdd, id_tabla,
nombre_campo) -- YA NO QUEDA NINGUN CAMPO MUTABLE (a diferencia del diseno
original de 2026-09-17, donde `clase` era mutable): todos los campos que
antes eran solo descriptivos ahora identifican el destino. Por eso no existe
`actualizar_atributo_fuente_consumo` -- si el plan de merge emite ACTUALIZAR
es porque la clave ya coincidia entre lo existente y lo entrante, y no hay
nada que escribir (ver pipeline/carga_gdd.py). Mismo patron INSERTAR/
ELIMINAR sin fecha de aprobacion y misma baja logica que investigacion/
respaldo/plan_remediacion. IMPORTANTE: `eliminar_atributo` tambien da de
baja los destinos de consumo hijos activos, igual que ya hacia con
atributo_fuente_oficial -- sin esto, un atributo dado de baja dejaria
huerfanos sus registros de consumo (mismo tipo de bug que el de staging
corregido antes en esta sesion). Esto no cambio con el refactor.
"""

from __future__ import annotations

import datetime
import logging

from sqlalchemy import text
from sqlalchemy.engine import Connection

from gdd_loader.domain.gdd_merge_logic import ExistenteVersionado

logger = logging.getLogger("gdd_loader.gdd_repository")


class CatalogoNoEncontradoError(Exception):
    """Un valor del Excel no calza con ninguna fila del catalogo controlado."""


# --- Catalogos controlados: solo lectura, nunca se crean filas nuevas ---


def resolver_catalogo_controlado(
    conn: Connection, tabla: str, columna: str, valor: str | None
) -> int | None:
    if valor is None:
        return None
    fila = conn.execute(
        text(f"SELECT id FROM gdd.{tabla} WHERE {columna} = :valor"), {"valor": valor}
    ).first()
    if fila is None:
        raise CatalogoNoEncontradoError(
            f"gdd.{tabla}: no existe ninguna fila con {columna} = '{valor}'. "
            "Es un catalogo controlado (no se crea automaticamente) -- "
            "confirmar si el valor del Excel esta mal escrito o si falta "
            "agregarlo al catalogo."
        )
    return fila[0]


def resolver_dato_personal(
    conn: Connection,
    codigo_categoria_nivel_uno: str | None,
    codigo_tipo_dato: str | None,
    codigo_sensibilidad: str | None,
) -> int | None:
    """Cruza por CODIGO (codigo_categoria_nivel_uno, codigo_tipo_dato,
    codigo_sensibilidad), no por el texto descriptivo -- el Excel trae
    "6. Crediticios" pero el catalogo separa el codigo "6" de la
    descripcion "Crediticios" en columnas distintas. La extraccion del
    codigo a partir del texto del Excel vive en
    gdd_mapping.extraer_codigo_*, no aqui.
    """
    if codigo_categoria_nivel_uno is None:
        return None
    fila = conn.execute(
        text(
            "SELECT id FROM gdd.cat_dato_personal "
            "WHERE codigo_categoria_nivel_uno = :cat AND codigo_tipo_dato = :tipo "
            "AND codigo_sensibilidad = :sens"
        ),
        {"cat": codigo_categoria_nivel_uno, "tipo": codigo_tipo_dato, "sens": codigo_sensibilidad},
    ).first()
    if fila is None:
        raise CatalogoNoEncontradoError(
            "gdd.cat_dato_personal: no existe ninguna fila con "
            f"codigo_categoria_nivel_uno='{codigo_categoria_nivel_uno}', "
            f"codigo_tipo_dato='{codigo_tipo_dato}', codigo_sensibilidad='{codigo_sensibilidad}'."
        )
    return fila[0]


def resolver_dominio_id(conn: Connection, codigo_dominio: str) -> int:
    fila = conn.execute(
        text("SELECT id FROM gdd.dominio_dato WHERE codigo_dominio = :cod"),
        {"cod": codigo_dominio},
    ).first()
    if fila is None:
        raise CatalogoNoEncontradoError(
            f"gdd.dominio_dato: no existe codigo_dominio = '{codigo_dominio}'. "
            "El dominio debe existir en gdd.dominio_dato antes de mergear sus "
            "atributos (esta capa no crea dominios nuevos)."
        )
    return fila[0]


def resolver_atributo_por_codigo(conn: Connection, codigo_dominio: str, codigo_atributo: int) -> int:
    """id de gdd.atributo por (codigo_dominio, codigo_atributo), para el FK
    de gdd.plan_remediacion. Solo considera filas activas: un atributo dado
    de baja no es un destino valido para un plan de remediacion nuevo --
    si el Excel trae un plan para un atributo inactivo, es una inconsistencia
    real (el atributo deberia mergearse antes, o el codigo esta mal) y se
    reporta como error, igual que un catalogo controlado no encontrado.
    """
    fila = conn.execute(
        text(
            "SELECT id FROM gdd.atributo WHERE codigo_dominio = :cod "
            "AND codigo_atributo = :cod_atributo AND activo = 1"
        ),
        {"cod": codigo_dominio, "cod_atributo": codigo_atributo},
    ).first()
    if fila is None:
        raise CatalogoNoEncontradoError(
            f"gdd.atributo: no existe una fila activa con codigo_dominio='{codigo_dominio}', "
            f"codigo_atributo={codigo_atributo}. El atributo debe existir (y estar activo) en "
            "gdd.atributo antes de mergear su plan de remediacion."
        )
    return fila[0]


# --- Resolucion "mejor esfuerzo" (nunca falla, solo registra advertencia) ---


def buscar_atributo_primario(
    conn: Connection, nombre_atributo: str | None, nombre_dominio: str | None
) -> tuple[int | None, int | None]:
    """Resolucion "mejor esfuerzo" de atributo_primario/dominio_primario --
    nunca aborta el merge, solo registra advertencia.

    AJUSTE 2026-09-22: antes se resolvian id_atributo_primario e
    id_dominio_primario con DOS busquedas independientes (una contra
    gdd.atributo.nombre_atributo, otra contra gdd.dominio_dato.nombre_dominio),
    sin ningun cruce entre ambas -- podian terminar apuntando a un atributo
    y a un dominio inconsistentes entre si (el atributo encontrado por
    nombre pertenece a un dominio distinto al que decia dominio_primario),
    o resolverse solo uno de los dos cuando el Excel trae ambos textos.
    Ahora exige coincidencia CONJUNTA: se busca un atributo cuyo
    nombre_atributo Y cuyo dominio (nombre_dominio, via join a
    gdd.dominio_dato) calcen AMBOS a la vez. Si falta cualquiera de los dos
    textos de origen, o no existe un atributo que calce con los dos, se
    devuelve (None, None) -- nunca se resuelve parcialmente uno de los dos
    campos. id_dominio_primario se deriva del id_dominio del atributo
    encontrado (no de una busqueda aparte), asi los dos campos quedan
    garantizados consistentes entre si por construccion.
    """
    if nombre_atributo is None or nombre_dominio is None:
        return None, None
    fila = conn.execute(
        text(
            "SELECT TOP 1 a.id, a.id_dominio "
            "FROM gdd.atributo a "
            "JOIN gdd.dominio_dato d ON d.id = a.id_dominio "
            "WHERE a.nombre_atributo = :nombre_atributo "
            "AND d.nombre_dominio = :nombre_dominio"
        ),
        {"nombre_atributo": nombre_atributo, "nombre_dominio": nombre_dominio},
    ).first()
    if fila is None:
        logger.warning(
            "atributo_primario='%s' + dominio_primario='%s' no calzan juntos con "
            "ningun atributo existente en gdd.atributo (se guardan ambos como NULL)",
            nombre_atributo,
            nombre_dominio,
        )
        return None, None
    return fila[0], fila[1]


# --- Catalogos tecnicos: get-or-create ---


def resolver_o_crear_servidor(conn: Connection, nombre_servidor: str) -> int:
    fila = conn.execute(
        text("SELECT id FROM gdd.servidor_fuente WHERE nombre_servidor = :nombre"),
        {"nombre": nombre_servidor},
    ).first()
    if fila is not None:
        return fila[0]
    return conn.execute(
        text(
            "INSERT INTO gdd.servidor_fuente (nombre_servidor) "
            "OUTPUT INSERTED.id VALUES (:nombre)"
        ),
        {"nombre": nombre_servidor},
    ).scalar_one()


def resolver_o_crear_bdd(conn: Connection, id_servidor: int, nombre_bdd: str) -> int:
    fila = conn.execute(
        text(
            "SELECT id FROM gdd.base_datos_fuente "
            "WHERE id_servidor = :id_servidor AND nombre_bdd = :nombre"
        ),
        {"id_servidor": id_servidor, "nombre": nombre_bdd},
    ).first()
    if fila is not None:
        return fila[0]
    return conn.execute(
        text(
            "INSERT INTO gdd.base_datos_fuente (id_servidor, nombre_bdd) "
            "OUTPUT INSERTED.id VALUES (:id_servidor, :nombre)"
        ),
        {"id_servidor": id_servidor, "nombre": nombre_bdd},
    ).scalar_one()


def resolver_bdd_controlado(conn: Connection, id_servidor: int, nombre_bdd: str) -> int:
    """Resuelve gdd.base_datos_fuente para el flujo de CONSUMO (coleccion_foc) --
    REFACTOR 2026-09-18. A diferencia de `resolver_o_crear_bdd` (usado por
    fuente oficial, SIN CAMBIOS -- sigue get-or-create), aqui el catalogo es
    CONTROLADO: decision del usuario, mantenido por usuarios expertos, sin
    creacion automatica.

    CORRECCION 2026-09-18 (segunda vuelta, detectada por el usuario en la
    tabla resultante): ya no devuelve `tipo` -- gdd.base_datos_fuente.`tipo`
    sigue existiendo como dato de clasificacion (coleccion/base_datos), pero
    dejo de usarse para decidir como resolver tabla_bv_foc (ver
    resolver_tabla_controlada, que ahora aplica siempre, sin excepcion).
    """
    fila = conn.execute(
        text(
            "SELECT id FROM gdd.base_datos_fuente "
            "WHERE id_servidor = :id_servidor AND nombre_bdd = :nombre"
        ),
        {"id_servidor": id_servidor, "nombre": nombre_bdd},
    ).first()
    if fila is None:
        raise CatalogoNoEncontradoError(
            f"gdd.base_datos_fuente: no existe ninguna fila con id_servidor={id_servidor} "
            f"y nombre_bdd = '{nombre_bdd}' para resolver coleccion_foc. A diferencia del flujo "
            "de fuente oficial, este catalogo es CONTROLADO para consumo -- no se crea "
            "automaticamente; confirmar si el valor del Excel esta mal escrito o si falta "
            "agregarlo al catalogo."
        )
    return fila.id


def resolver_tabla_controlada(conn: Connection, id_bdd: int, nombre_tabla: str) -> int:
    """Resuelve gdd.base_datos_fuente_tabla -- catalogo controlado NUEVO
    (2026-09-18). CORRECCION 2026-09-18 (segunda vuelta): aplica a TODAS las
    filas de base_datos_fuente (coleccion o base_datos) -- ya no hay
    excepcion de texto libre para bases de datos transaccionales (decision
    revisada por el usuario; la primera version de este refactor asumia que
    solo aplicaba a colecciones). Mantenida por usuarios expertos --
    gdd_loader nunca escribe aqui, solo resuelve/lee.
    """
    fila = conn.execute(
        text(
            "SELECT id FROM gdd.base_datos_fuente_tabla "
            "WHERE id_bdd = :id_bdd AND nombre_tabla = :nombre AND activo = 1"
        ),
        {"id_bdd": id_bdd, "nombre": nombre_tabla},
    ).first()
    if fila is None:
        raise CatalogoNoEncontradoError(
            f"gdd.base_datos_fuente_tabla: no existe ninguna fila activa con id_bdd={id_bdd} "
            f"y nombre_tabla = '{nombre_tabla}'. Catalogo controlado (coleccion) -- no se crea "
            "automaticamente; confirmar si el valor del Excel esta mal escrito o si falta "
            "agregarlo al catalogo."
        )
    return fila.id


def resolver_o_crear_valor_valido(conn: Connection, valor: str | None) -> int:
    if valor is None:
        fila = conn.execute(
            text("SELECT TOP 1 id FROM gdd.campo_fuente_valor_valido WHERE valor IS NULL")
        ).first()
        if fila is not None:
            return fila[0]
        return conn.execute(
            text(
                "INSERT INTO gdd.campo_fuente_valor_valido (valor) OUTPUT INSERTED.id VALUES (NULL)"
            )
        ).scalar_one()

    fila = conn.execute(
        text("SELECT id FROM gdd.campo_fuente_valor_valido WHERE valor = :valor"),
        {"valor": valor},
    ).first()
    if fila is not None:
        return fila[0]
    return conn.execute(
        text(
            "INSERT INTO gdd.campo_fuente_valor_valido (valor) OUTPUT INSERTED.id VALUES (:valor)"
        ),
        {"valor": valor},
    ).scalar_one()


# --- gdd.atributo ---


def atributos_existentes(conn: Connection, codigo_dominio: str) -> dict[tuple[str, int], ExistenteVersionado]:
    # activo = 1: una fila dada de baja no cuenta como "existente" para el
    # diff -- si reaparece en el Excel se trata como alta nueva (ver nota
    # de modulo sobre baja logica).
    filas = conn.execute(
        text(
            "SELECT id, codigo_dominio, codigo_atributo, fecha_aprobacion "
            "FROM gdd.atributo WHERE codigo_dominio = :cod AND activo = 1"
        ),
        {"cod": codigo_dominio},
    ).all()
    return {
        (fila.codigo_dominio, fila.codigo_atributo): ExistenteVersionado(
            id=fila.id, fecha_aprobacion=fila.fecha_aprobacion
        )
        for fila in filas
    }


def insertar_atributo(conn: Connection, campos: dict) -> int:
    # [corchetes]: "release" es palabra reservada en T-SQL: sin ellos, un
    # INSERT/UPDATE que incluya esa columna falla con error de sintaxis.
    columnas = ", ".join(f"[{c}]" for c in campos.keys())
    parametros = ", ".join(f":{c}" for c in campos.keys())
    return conn.execute(
        text(f"INSERT INTO gdd.atributo ({columnas}) OUTPUT INSERTED.id VALUES ({parametros})"),
        campos,
    ).scalar_one()


def actualizar_atributo(conn: Connection, id_atributo: int, campos: dict) -> None:
    set_clause = ", ".join(f"[{c}] = :{c}" for c in campos.keys())
    conn.execute(
        text(f"UPDATE gdd.atributo SET {set_clause} WHERE id = :id"),
        {**campos, "id": id_atributo},
    )


def version_release_atributo(conn: Connection, id_atributo: int) -> tuple[str | None, str | None]:
    """version/release actuales de una fila de gdd.atributo, para poder
    incrementar version y conservar release al REEMPLAZAR (delete+insert)
    por una fecha_aprobacion mas reciente.
    """
    fila = conn.execute(
        text("SELECT version, [release] FROM gdd.atributo WHERE id = :id"), {"id": id_atributo}
    ).first()
    return (fila.version, fila.release) if fila is not None else (None, None)


def eliminar_atributo(conn: Connection, id_atributo: int) -> None:
    """Baja logica, NUNCA delete fisico -- ver nota de modulo. Se marcan
    inactivas tambien las fuentes oficiales Y los destinos de consumo hijos
    todavia activos: un atributo dado de baja no debe dejar "huerfanos"
    registros activos en sus tablas hijas (atributo_fuente_oficial,
    atributo_fuente_consumo). Agregado 2026-09-17 al crear
    atributo_fuente_consumo -- mismo tipo de bug (fila huerfana con activo=1
    tras la baja de su padre) que el corregido antes en staging_repository
    esta misma sesion, esta vez a nivel de la baja logica en gdd.
    """
    hoy = datetime.date.today()
    conn.execute(
        text(
            "UPDATE gdd.atributo_fuente_oficial SET activo = 0, fecha_baja = :hoy "
            "WHERE id_atributo = :id AND activo = 1"
        ),
        {"id": id_atributo, "hoy": hoy},
    )
    conn.execute(
        text(
            "UPDATE gdd.atributo_fuente_consumo SET activo = 0, fecha_baja = :hoy "
            "WHERE id_atributo = :id AND activo = 1"
        ),
        {"id": id_atributo, "hoy": hoy},
    )
    conn.execute(
        text("UPDATE gdd.atributo SET activo = 0, fecha_baja = :hoy WHERE id = :id"),
        {"id": id_atributo, "hoy": hoy},
    )


# --- gdd.atributo_fuente_oficial ---


def fuente_oficial_existentes(
    conn: Connection, id_atributo: int
) -> dict[tuple[str, str, int, bool], ExistenteVersionado]:
    """Clave natural real: (clase, nombre_campo, id_bdd, es_fuente_primaria).

    (clase, es_fuente_primaria) por si solo NO alcanza: un mismo atributo
    puede tener varias filas de metadata_tecnica que comparten `clase` pero
    vienen de sistemas/tablas fuente distintos (ej. nucleo NOVA vs Coris),
    diferenciados por nombre_campo_fuente_oficial y/o
    base_datos_fuente_oficial. Confirmado con datos reales: la clave vieja
    colapsaba 210 filas de metadata_tecnica en solo 94 (perdida silenciosa
    de 116 filas); esta clave no tiene colisiones en esos mismos datos.
    """
    filas = conn.execute(
        text(
            "SELECT id, clase, nombre_campo, id_bdd, es_fuente_primaria, fecha_aprobacion "
            "FROM gdd.atributo_fuente_oficial WHERE id_atributo = :id_atributo AND activo = 1"
        ),
        {"id_atributo": id_atributo},
    ).all()
    return {
        (fila.clase or "", fila.nombre_campo, fila.id_bdd, bool(fila.es_fuente_primaria)):
        ExistenteVersionado(id=fila.id, fecha_aprobacion=fila.fecha_aprobacion)
        for fila in filas
    }


def insertar_fuente_oficial(conn: Connection, campos: dict) -> int:
    columnas = ", ".join(f"[{c}]" for c in campos.keys())
    parametros = ", ".join(f":{c}" for c in campos.keys())
    return conn.execute(
        text(
            f"INSERT INTO gdd.atributo_fuente_oficial ({columnas}) "
            f"OUTPUT INSERTED.id VALUES ({parametros})"
        ),
        campos,
    ).scalar_one()


def actualizar_fuente_oficial(conn: Connection, id_fuente: int, campos: dict) -> None:
    set_clause = ", ".join(f"[{c}] = :{c}" for c in campos.keys())
    conn.execute(
        text(f"UPDATE gdd.atributo_fuente_oficial SET {set_clause} WHERE id = :id"),
        {**campos, "id": id_fuente},
    )


def version_release_fuente_oficial(
    conn: Connection, id_fuente: int
) -> tuple[str | None, str | None]:
    fila = conn.execute(
        text("SELECT version, [release] FROM gdd.atributo_fuente_oficial WHERE id = :id"),
        {"id": id_fuente},
    ).first()
    return (fila.version, fila.release) if fila is not None else (None, None)


def eliminar_fuente_oficial(conn: Connection, id_fuente: int) -> None:
    """Baja logica, NUNCA delete fisico -- ver nota de modulo."""
    conn.execute(
        text("UPDATE gdd.atributo_fuente_oficial SET activo = 0, fecha_baja = :hoy WHERE id = :id"),
        {"id": id_fuente, "hoy": datetime.date.today()},
    )


# --- Comparacion de contenido (evitar ACTUALIZAR/bitacora sin cambio real) ---


def campos_actuales(conn: Connection, tabla: str, id_registro: int, columnas) -> dict:
    """Valores actuales de una fila (por id) para las columnas dadas.

    Usado antes de un ACTUALIZAR: si el Excel llega con la misma
    fecha_aprobacion Y los mismos valores que ya estan en gdd (caso comun
    cuando el proceso corre varias veces al dia sin cambios reales), no
    tiene sentido escribir un UPDATE ni una linea de bitacora -- este
    chequeo es lo que permite decidirlo.

    `tabla` SIEMPRE es un literal interno ('atributo' |
    'atributo_fuente_oficial'), nunca un valor externo -- se interpola
    igual que en insertar_atributo/actualizar_atributo.
    `columnas`: iterable de nombres de columna (tipicamente
    campos_resueltos.keys(), para no duplicar la lista de campos en dos
    lugares y evitar que se desincronicen).
    """
    columnas_sql = ", ".join(f"[{c}]" for c in columnas)
    fila = conn.execute(
        text(f"SELECT {columnas_sql} FROM gdd.{tabla} WHERE id = :id"), {"id": id_registro}
    ).mappings().first()
    return dict(fila) if fila is not None else {}


# --- gdd.bitacora_carga ---


def registrar_bitacora(
    conn: Connection,
    tabla: str,
    id_registro: int,
    accion: str,
    codigo_dominio: str,
    codigo_dominio_atributo: str,
    version_resultante: str | None,
) -> None:
    """Una linea por cada operacion de merge aplicada sobre gdd.atributo /
    gdd.atributo_fuente_oficial (INSERTAR/ACTUALIZAR/REEMPLAZAR/ELIMINAR/
    OMITIR_FECHA_RETROCEDE). Decision explicita (2026-09-14): solo metadata
    de la operacion, sin snapshot de los campos que cambiaron -- evita
    guardar datos personales (cat_dato_personal) en texto plano dentro de
    la bitacora. Se inserta dentro de la MISMA transaccion del dominio: si
    el dominio hace rollback, la linea de bitacora tambien se revierte --
    la bitacora solo refleja lo que realmente quedo aplicado.

    `tabla`: 'atributo' | 'atributo_fuente_oficial'. `id_registro`: id en
    esa tabla (para REEMPLAZAR, el id de la fila NUEVA que la operacion
    produjo -- la fila vieja queda registrada por su propia linea de
    ELIMINAR/REEMPLAZAR anterior en la bitacora, no se duplica aqui).
    """
    conn.execute(
        text(
            "INSERT INTO gdd.bitacora_carga "
            "(tabla, id_registro, accion, codigo_dominio, codigo_dominio_atributo, version_resultante) "
            "VALUES (:tabla, :id_registro, :accion, :codigo_dominio, :codigo_dominio_atributo, :version_resultante)"
        ),
        {
            "tabla": tabla,
            "id_registro": id_registro,
            "accion": accion,
            "codigo_dominio": codigo_dominio,
            "codigo_dominio_atributo": codigo_dominio_atributo,
            "version_resultante": version_resultante,
        },
    )


# --- gdd.investigacion ---
#
# A diferencia de atributo/atributo_fuente_oficial, esta hoja no trae fecha
# de aprobacion por fila -- pero SI tiene clave natural propia: (codigo_dominio,
# nombre) (confirmado que "nombre" no se repite dentro de un mismo dominio
# en los datos reales). Primera version de este modulo (2026-09-14) uso
# reemplazo completo por dominio en vez de un diff por clave; el usuario lo
# probo y detecto el problema real: un solo cambio de descripcion daba de
# baja + reinsertaba las 26 filas del dominio, inviable corriendo el merge
# varias veces al dia (bitacora/historial sin control). Se reemplaza por el
# mismo patron INSERTAR/ACTUALIZAR/ELIMINAR de atributo, reutilizando
# gdd_merge_logic.calcular_plan_merge con fecha_aprobacion=None siempre (esa
# funcion, sin fecha para comparar en ninguno de los dos lados, solo emite
# INSERTAR/ACTUALIZAR/ELIMINAR -- REEMPLAZAR/OMITIR_FECHA_RETROCEDE nunca se
# generan para esta tabla). ACTUALIZAR es UPDATE in-place (no baja+alta: no
# hay version que versionar aqui), y el llamador (carga_gdd.py) usa el mismo
# helper _hay_cambios que atributo para no tocar la fila si nada cambio de
# verdad. ELIMINAR sigue siendo baja logica (activo=0, fecha_baja), nunca
# DELETE fisico -- misma politica que el resto de gdd.


def investigacion_existentes(conn: Connection, codigo_dominio: str) -> dict[str, ExistenteVersionado]:
    """Filas activas de gdd.investigacion de un dominio, por `nombre` (la
    clave natural es (codigo_dominio, nombre); codigo_dominio ya acota la
    consulta). fecha_aprobacion siempre None -- ver nota de modulo.
    """
    filas = conn.execute(
        text(
            "SELECT id, nombre FROM gdd.investigacion "
            "WHERE codigo_dominio = :cod AND activo = 1"
        ),
        {"cod": codigo_dominio},
    ).all()
    return {f.nombre: ExistenteVersionado(id=f.id, fecha_aprobacion=None) for f in filas}


def insertar_investigacion(conn: Connection, campos: dict) -> int:
    return conn.execute(
        text(
            "INSERT INTO gdd.investigacion "
            "(id_dominio, codigo_dominio, id_clasificacion, nombre, descripcion, referencia_normativa) "
            "OUTPUT INSERTED.id "
            "VALUES (:id_dominio, :codigo_dominio, :id_clasificacion, :nombre, :descripcion, :referencia_normativa)"
        ),
        campos,
    ).scalar_one()


def actualizar_investigacion(conn: Connection, id_investigacion: int, campos: dict) -> None:
    """Solo se tocan los campos mutables (id_clasificacion, descripcion,
    referencia_normativa) -- codigo_dominio/nombre son la clave natural, no
    cambian en un UPDATE.
    """
    conn.execute(
        text(
            "UPDATE gdd.investigacion SET id_clasificacion = :id_clasificacion, "
            "descripcion = :descripcion, referencia_normativa = :referencia_normativa WHERE id = :id"
        ),
        {**campos, "id": id_investigacion},
    )


def eliminar_investigacion(conn: Connection, id_investigacion: int) -> None:
    """Baja logica -- igual politica que eliminar_atributo/eliminar_fuente_oficial."""
    hoy = datetime.date.today()
    conn.execute(
        text("UPDATE gdd.investigacion SET activo = 0, fecha_baja = :hoy WHERE id = :id"),
        {"hoy": hoy, "id": id_investigacion},
    )


# --- gdd.dominio_responsable ---
#
# Estructura (2026-09-15): gobierno del dominio -- quien es responsable de
# cada "plaza"/rol (Data Steward, Dueño de Dominio, etc.). Clave natural:
# (codigo_dominio, codigo_plaza), confirmado con el usuario que codigo_plaza
# identifica la plaza de forma unica dentro de un dominio. Mismo patron
# INSERTAR/ACTUALIZAR/ELIMINAR que investigacion (fecha_aprobada se guarda
# como dato informativo, no dispara versionado tipo REEMPLAZAR) y misma
# baja logica (activo=0, fecha_baja), nunca DELETE fisico.
#
# A diferencia de investigacion/atributo, esta tabla NO tiene columna
# codigo_dominio propia (denormalizada) -- solo id_dominio (FK) -- asi que
# el filtro por dominio se hace siempre contra id_dominio (ya resuelto por
# el llamador con resolver_dominio_id), nunca contra un texto codigo_dominio
# aqui.


def dominio_responsable_existentes(conn: Connection, id_dominio: int) -> dict[str, ExistenteVersionado]:
    """Filas activas de gdd.dominio_responsable de un dominio, por
    codigo_plaza (la clave natural es (codigo_dominio, codigo_plaza);
    id_dominio ya acota la consulta). fecha_aprobacion siempre None -- ver
    nota de modulo (no dispara versionado).
    """
    filas = conn.execute(
        text(
            "SELECT id, codigo_plaza FROM gdd.dominio_responsable "
            "WHERE id_dominio = :id_dominio AND activo = 1"
        ),
        {"id_dominio": id_dominio},
    ).all()
    return {f.codigo_plaza: ExistenteVersionado(id=f.id, fecha_aprobacion=None) for f in filas}


def insertar_dominio_responsable(conn: Connection, campos: dict) -> int:
    return conn.execute(
        text(
            "INSERT INTO gdd.dominio_responsable "
            "(id_dominio, id_rol, area, codigo_plaza, nombre_plaza, nombre_responsable, fecha_aprobada) "
            "OUTPUT INSERTED.id "
            "VALUES (:id_dominio, :id_rol, :area, :codigo_plaza, :nombre_plaza, :nombre_responsable, :fecha_aprobada)"
        ),
        campos,
    ).scalar_one()


def actualizar_dominio_responsable(conn: Connection, id_registro: int, campos: dict) -> None:
    """Solo se tocan los campos mutables -- codigo_plaza (y el dominio) son
    la clave natural, no cambian en un UPDATE.
    """
    conn.execute(
        text(
            "UPDATE gdd.dominio_responsable SET id_rol = :id_rol, area = :area, "
            "nombre_plaza = :nombre_plaza, nombre_responsable = :nombre_responsable, "
            "fecha_aprobada = :fecha_aprobada WHERE id = :id"
        ),
        {**campos, "id": id_registro},
    )


def eliminar_dominio_responsable(conn: Connection, id_registro: int) -> None:
    """Baja logica -- igual politica que eliminar_investigacion."""
    hoy = datetime.date.today()
    conn.execute(
        text("UPDATE gdd.dominio_responsable SET activo = 0, fecha_baja = :hoy WHERE id = :id"),
        {"hoy": hoy, "id": id_registro},
    )


# --- gdd.respaldo ---
#
# Respaldos (2026-09-15): evidencia de aprobacion del dominio (correos,
# minutas, etc. -- un link por fila). Clave natural: (codigo_dominio,
# tipo_respaldo, enlace_respaldo) -- el Excel real trae mas de una fila con
# el MISMO enlace_respaldo diferenciadas solo por tipo_respaldo, asi que
# enlace_respaldo solo no alcanza. A diferencia de dominio_responsable
# (donde id_rol es un campo mutable) o investigacion (donde id_clasificacion
# es mutable), aqui id_tipo_respaldo SI es parte de la clave -- el llamador
# (pipeline/carga_gdd.py) lo resuelve ANTES de construir el plan de merge y
# usa (id_tipo_respaldo, enlace_respaldo) como clave en ambos lados (existente
# y entrante), evitando comparar por texto. fecha_aprobada se guarda como
# texto libre (el Excel real trae formatos no estandar), nunca dispara
# versionado. Baja logica igual que el resto de gdd.


def respaldo_existentes(conn: Connection, codigo_dominio: str) -> dict[tuple[int, str], ExistenteVersionado]:
    """Filas activas de gdd.respaldo de un dominio, por (id_tipo_respaldo,
    enlace_respaldo) -- la clave natural completa es (codigo_dominio,
    tipo_respaldo, enlace_respaldo); codigo_dominio ya acota la consulta.
    fecha_aprobacion siempre None -- no dispara versionado.
    """
    filas = conn.execute(
        text(
            "SELECT id, id_tipo_respaldo, enlace_respaldo FROM gdd.respaldo "
            "WHERE codigo_dominio = :cod AND activo = 1"
        ),
        {"cod": codigo_dominio},
    ).all()
    return {
        (f.id_tipo_respaldo, f.enlace_respaldo): ExistenteVersionado(id=f.id, fecha_aprobacion=None)
        for f in filas
    }


def insertar_respaldo(conn: Connection, campos: dict) -> int:
    return conn.execute(
        text(
            "INSERT INTO gdd.respaldo "
            "(id_dominio, codigo_dominio, id_tipo_respaldo, enlace_respaldo, observaciones, fecha_aprobada) "
            "OUTPUT INSERTED.id "
            "VALUES (:id_dominio, :codigo_dominio, :id_tipo_respaldo, :enlace_respaldo, :observaciones, :fecha_aprobada)"
        ),
        campos,
    ).scalar_one()


def actualizar_respaldo(conn: Connection, id_respaldo: int, campos: dict) -> None:
    """Solo se tocan los campos mutables (observaciones, fecha_aprobada) --
    tipo_respaldo/enlace_respaldo (y el dominio) son la clave natural, no
    cambian en un UPDATE.
    """
    conn.execute(
        text(
            "UPDATE gdd.respaldo SET observaciones = :observaciones, "
            "fecha_aprobada = :fecha_aprobada WHERE id = :id"
        ),
        {**campos, "id": id_respaldo},
    )


def eliminar_respaldo(conn: Connection, id_respaldo: int) -> None:
    """Baja logica -- igual politica que eliminar_investigacion."""
    hoy = datetime.date.today()
    conn.execute(
        text("UPDATE gdd.respaldo SET activo = 0, fecha_baja = :hoy WHERE id = :id"),
        {"hoy": hoy, "id": id_respaldo},
    )


# --- gdd.plan_remediacion ---
#
# PlanDeRemediacion (2026-09-15): plan de remediacion de un hallazgo ligado
# a un atributo especifico (ver nota de modulo). Clave natural
# (codigo_dominio_atributo, id_problema) -- codigo_dominio_atributo ya
# identifica al atributo (viene directo de la hoja, no se hereda), asi que
# id_atributo se resuelve una sola vez por fila y no es parte de la clave
# de comparacion en si (aunque en la practica nunca cambia para una misma
# clave, porque codigo_dominio_atributo -> id_atributo es una funcion).


def plan_remediacion_existentes(
    conn: Connection, codigo_dominio: str
) -> dict[tuple[str, str], ExistenteVersionado]:
    """Filas activas de gdd.plan_remediacion de un dominio, por
    (codigo_dominio_atributo, id_problema) -- la clave natural completa.
    codigo_dominio ya acota la consulta (columna denormalizada, igual que
    en gdd.respaldo). fecha_aprobacion siempre None -- no dispara
    versionado (UPDATE in-place, ver nota de modulo).
    """
    filas = conn.execute(
        text(
            "SELECT id, codigo_dominio_atributo, id_problema FROM gdd.plan_remediacion "
            "WHERE codigo_dominio = :cod AND activo = 1"
        ),
        {"cod": codigo_dominio},
    ).all()
    return {
        (f.codigo_dominio_atributo, f.id_problema): ExistenteVersionado(id=f.id, fecha_aprobacion=None)
        for f in filas
    }


def insertar_plan_remediacion(conn: Connection, campos: dict) -> int:
    columnas = ", ".join(f"[{c}]" for c in campos.keys())
    parametros = ", ".join(f":{c}" for c in campos.keys())
    return conn.execute(
        text(
            f"INSERT INTO gdd.plan_remediacion ({columnas}) "
            f"OUTPUT INSERTED.id VALUES ({parametros})"
        ),
        campos,
    ).scalar_one()


def actualizar_plan_remediacion(conn: Connection, id_plan: int, campos: dict) -> None:
    """Solo se tocan los campos mutables -- id_atributo/codigo_dominio/
    codigo_dominio_atributo/id_problema son la clave (identidad de la
    fila), no cambian en un UPDATE. El llamador (carga_gdd.py) arma
    `campos` sin esas cuatro claves antes de llamar aqui.
    """
    set_clause = ", ".join(f"[{c}] = :{c}" for c in campos.keys())
    conn.execute(
        text(f"UPDATE gdd.plan_remediacion SET {set_clause} WHERE id = :id"),
        {**campos, "id": id_plan},
    )


def eliminar_plan_remediacion(conn: Connection, id_plan: int) -> None:
    """Baja logica -- igual politica que eliminar_investigacion/eliminar_respaldo."""
    hoy = datetime.date.today()
    conn.execute(
        text("UPDATE gdd.plan_remediacion SET activo = 0, fecha_baja = :hoy WHERE id = :id"),
        {"hoy": hoy, "id": id_plan},
    )


# --- gdd.atributo_fuente_consumo ---
#
# FuenteConsumo / "_foc" (2026-09-17, alcance ampliado; REFACTORIZADA
# 2026-09-18, dos vueltas -- ver nota de modulo): trazabilidad de CONSUMO de
# un atributo especifico -- FK id_atributo, igual que plan_remediacion/
# atributo_fuente_oficial. A diferencia de atributo_fuente_oficial (de donde
# se CARGA el atributo), esto registra en que tabla/campo de negocio se
# CONSULTA -- ver nota de modulo y gdd_mapping.ConsumoPendiente. Clave
# natural (dentro de un mismo id_atributo): (id_bdd, id_tabla, nombre_campo)
# -- sin id_servidor (redundante, se alcanza via base_datos_fuente.id_servidor,
# mismo patron que atributo_fuente_oficial) y sin ningun campo mutable, por
# eso no existe actualizar_atributo_fuente_consumo (ver nota de modulo).


def consumo_existentes(conn: Connection, id_atributo: int) -> dict[tuple[int, int, str], ExistenteVersionado]:
    """Filas activas de gdd.atributo_fuente_consumo de un atributo, por
    (id_bdd, id_tabla, nombre_campo) -- la clave natural completa
    (id_atributo ya acota la consulta). fecha_aprobacion siempre None -- no
    dispara versionado.
    """
    filas = conn.execute(
        text(
            "SELECT id, id_bdd, id_tabla, nombre_campo "
            "FROM gdd.atributo_fuente_consumo WHERE id_atributo = :id_atributo AND activo = 1"
        ),
        {"id_atributo": id_atributo},
    ).all()
    return {
        (f.id_bdd, f.id_tabla, f.nombre_campo): ExistenteVersionado(id=f.id, fecha_aprobacion=None)
        for f in filas
    }


def insertar_atributo_fuente_consumo(conn: Connection, campos: dict) -> int:
    return conn.execute(
        text(
            "INSERT INTO gdd.atributo_fuente_consumo "
            "(id_atributo, id_bdd, id_tabla, nombre_campo) "
            "OUTPUT INSERTED.id "
            "VALUES (:id_atributo, :id_bdd, :id_tabla, :nombre_campo)"
        ),
        campos,
    ).scalar_one()


# No hay actualizar_atributo_fuente_consumo: TODOS los campos (id_bdd/
# id_tabla/nombre_campo) son la clave natural -- si el plan de merge emite
# ACTUALIZAR es porque la clave ya coincidia entre lo existente y lo
# entrante, y no hay nada que escribir (ver pipeline/carga_gdd.py, que
# cuenta ese caso directamente como "sin cambios" sin llamar al repositorio).


def eliminar_atributo_fuente_consumo(conn: Connection, id_registro: int) -> None:
    """Baja logica -- igual politica que eliminar_investigacion/eliminar_respaldo."""
    hoy = datetime.date.today()
    conn.execute(
        text("UPDATE gdd.atributo_fuente_consumo SET activo = 0, fecha_baja = :hoy WHERE id = :id"),
        {"hoy": hoy, "id": id_registro},
    )

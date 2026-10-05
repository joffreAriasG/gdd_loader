"""
Orquestacion del merge staging -> gdd, por dominio. Lee de
staging.detalle_atributos / staging.metadata_tecnica (no del Excel
directamente -- staging ya es la fuente confiable), resuelve catalogos,
calcula el plan de INSERT/UPDATE/REEMPLAZAR/ELIMINAR (gdd_merge_logic) y lo
aplica dentro de UNA transaccion por dominio: todo o nada. Si algo falla a
mitad de camino (un catalogo no encontrado, un dato requerido vacio), se
revierte todo el dominio -- nunca se deja gdd a medio actualizar.

ELIMINAR es baja logica, no fisica (ver gdd_repository.py): el Excel de
origen se toma como la fuente completa de verdad para decidir que sigue
vigente, sin ningun umbral de seguridad que frene una baja masiva -- por
eso un atributo/fuente que desaparece nunca se borra de verdad, solo se
marca inactivo con su fecha de baja.

A diferencia de la carga a staging (que sigue con la siguiente hoja/archivo
si una falla), aqui una fila con problemas aborta el dominio completo: gdd
es el esquema de gobierno "oficial" y sus relaciones (FKs, catalogos) no
toleran una carga parcial sin quedar inconsistentes.

staging.investigacion -> gdd.investigacion se mergea aparte
(`ejecutar_merge_investigacion_dominio`), con su propia transaccion: no
comparte FKs con atributo/fuente_oficial. Usa el mismo plan INSERTAR/
ACTUALIZAR/ELIMINAR de gdd_merge_logic, con clave natural (codigo_dominio,
nombre) en vez de fecha_aprobacion (esa hoja no trae fecha por fila).

staging.estructura -> gdd.dominio_responsable se mergea igual que
investigacion (`ejecutar_merge_estructura_dominio`, transaccion propia),
con clave natural (codigo_dominio, codigo_plaza).

staging.respaldos -> gdd.respaldo se mergea igual (`ejecutar_merge_respaldos_dominio`,
transaccion propia), con clave natural (codigo_dominio, tipo_respaldo,
enlace_respaldo) -- a diferencia de las otras, la parte de catalogo
(id_tipo_respaldo) es parte de la clave, no un campo mutable, y se
resuelve antes de construir el plan (ver `_clave_respaldo`).

staging.plan_remediacion -> gdd.plan_remediacion se mergea igual
(`ejecutar_merge_plan_remediacion_dominio`, transaccion propia), pero esta
hoja esta ligada a un ATRIBUTO especifico, no al dominio completo:
codigo_dominio_atributo viene en la propia hoja (no se hereda), se lee de
staging con `leer_por_prefijo` (igual que metadata_tecnica) y el FK
id_atributo se resuelve por (codigo_dominio, codigo_atributo) via
`repo.resolver_atributo_por_codigo`. Clave natural: (codigo_dominio_atributo,
id_problema).

gdd.atributo_fuente_consumo (2026-09-17, alcance ampliado; REFACTORIZADA
2026-09-18): trazabilidad de CONSUMO de un atributo -- en que tabla/campo de
negocio se consulta, luego de la transformacion de carga a consumo (columnas
_foc de metadata_tecnica). A diferencia de plan_remediacion (transaccion
propia), esto se mergea DENTRO de `ejecutar_merge_dominio` (misma
transaccion que atributo/fuente_oficial): tiene FK hacia atributo igual que
fuente_oficial, y su baja logica debe quedar sincronizada con la del
atributo padre (ver gdd_repository.eliminar_atributo). Se lee de la MISMA
`filas_metadata` que fuente_oficial (mismas columnas _foc de
metadata_tecnica), pero `mapear_atributo_fuente_consumo` agrupa por atributo
y deduplica -- la cardinalidad es directa con el atributo, confirmada con el
usuario, no con cada fila de fuente oficial.

REFACTOR 2026-09-18 (ver nota de modulo de gdd_repository.py -- la prueba
con negocio del diseno de texto libre no fue satisfactoria; dos vueltas):
coleccion_foc/tabla_bv_foc se resuelven ahora contra catalogos CONTROLADOS en
vez de guardarse como texto libre. `_resolver_destino_consumo` hace: (1)
servidor -- get-or-create, SIN CAMBIOS, pero solo como paso intermedio (NO
se guarda en atributo_fuente_consumo, ver mas abajo); (2) coleccion_foc -> id_bdd
via `repo.resolver_bdd_controlado` (CONTROLADO); (3) tabla_bv_foc -> id_tabla
via `repo.resolver_tabla_controlada` (CONTROLADO), SIEMPRE -- correccion de
la segunda vuelta: la primera version distinguia tipo='coleccion' (catalogo)
de tipo='base_datos' (texto libre); esa distincion se elimino, el catalogo
aplica siempre.

id_servidor NO es parte de gdd.atributo_fuente_consumo (correccion de la
segunda vuelta, detectada por el usuario en la tabla resultante): la
relacion servidor<->bdd ya existe via base_datos_fuente.id_servidor, mismo
patron que atributo_fuente_oficial (que tampoco guarda id_servidor propio).

Clave natural (dentro de un mismo id_atributo): (id_bdd, id_tabla,
nombre_campo) -- todos resueltos/conocidos ANTES de construir el plan de
merge (mismo patron que id_bdd en fuente oficial, ver `_clave_consumo`). Ya
no queda ningun campo mutable (a diferencia del diseno original de
2026-09-17, donde `clase` era mutable) -- por eso el caso ACTUALIZAR de mas
abajo no llama al repositorio, solo cuenta "sin_cambios" (si el plan emite
ACTUALIZAR es porque la clave ya coincidia). Sin fecha de aprobacion (UPDATE
in-place, aunque aqui nunca hay nada que actualizar de verdad) y sin
bitacora (mismo criterio que plan_remediacion).

RENAME 2026-09-21: la columna Excel/staging `clase_foc` paso a llamarse
`coleccion_foc`, y con ella el atributo `ConsumoPendiente.clase_texto` paso
a `coleccion_texto` -- alcance SOLO de este flujo (confirmado con el
usuario).

CORRECCION 2026-10-01: las columnas "_foc" van SOLO a atributo_fuente_consumo;
ya no generan una fuente oficial secundaria (es_fuente_primaria=0) -- ver
gdd_mapping.mapear_metadata_tecnica. Como el merge compara contra el Excel,
cualquier secundaria activa que quede en gdd se da de baja en la siguiente
carga del dominio (o antes, con 20261001b_baja_fuentes_secundarias_foc.sql).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy.engine import Engine

from gdd_loader.domain.gdd_mapping import (
    AtributoPendiente,
    ConsumoPendiente,
    EstructuraPendiente,
    FuenteOficialPendiente,
    InvestigacionPendiente,
    PlanRemediacionPendiente,
    RespaldoPendiente,
    extraer_codigo_categoria_nivel_uno,
    extraer_codigo_sensibilidad,
    extraer_codigo_tipo_dato,
    mapear_atributo_fuente_consumo,
    mapear_detalle_atributos,
    mapear_estructura,
    mapear_investigacion,
    mapear_metadata_tecnica,
    mapear_plan_remediacion,
    mapear_respaldos,
)
from gdd_loader.domain.gdd_merge_logic import Accion, calcular_plan_merge
from gdd_loader.load import gdd_repository as repo
from gdd_loader.load.staging_repository import StagingRepository

logger = logging.getLogger("gdd_loader.pipeline.gdd")


@dataclass
class ResultadoMergeDominio:
    codigo_dominio: str
    atributos_insertados: int = 0
    atributos_actualizados: int = 0
    atributos_reemplazados: int = 0
    atributos_eliminados: int = 0
    atributos_omitidos_fecha_retrocede: int = 0
    atributos_sin_cambios: int = 0
    fuentes_insertadas: int = 0
    fuentes_actualizadas: int = 0
    fuentes_reemplazadas: int = 0
    fuentes_eliminadas: int = 0
    fuentes_omitidas_fecha_retrocede: int = 0
    fuentes_sin_cambios: int = 0
    consumos_insertados: int = 0
    consumos_actualizados: int = 0
    consumos_eliminados: int = 0
    consumos_sin_cambios: int = 0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def _hay_cambios(nuevos: dict, actuales: dict) -> bool:
    """Compara los campos que se escribirian contra los valores actuales en
    BD, antes de decidir si un candidato a ACTUALIZAR tiene algo real que
    actualizar. Pensado para el caso de correr el merge varias veces al
    dia con el mismo Excel: misma fecha_aprobacion Y mismos valores no
    deberia generar ni UPDATE ni linea de bitacora.

    SQL Server devuelve columnas bit como 0/1, no como bool de Python --
    se normaliza antes de comparar para no marcar como "cambio" algo que
    en realidad es igual.
    """
    for clave, valor_nuevo in nuevos.items():
        valor_actual = actuales.get(clave)
        if isinstance(valor_nuevo, bool):
            valor_actual = None if valor_actual is None else bool(valor_actual)
        if valor_nuevo != valor_actual:
            return True
    return False


def _resolver_campos_atributo(conn, id_dominio: int, pendiente: AtributoPendiente) -> dict:
    # AJUSTE 2026-09-22: id_atributo_primario/id_dominio_primario ahora se
    # resuelven JUNTOS (ver gdd_repository.buscar_atributo_primario) -- exige
    # coincidencia conjunta de nombre_atributo Y nombre_dominio; si falta
    # cualquiera de los dos textos o no calzan ambos a la vez, los dos
    # campos quedan en None (nunca se resuelve solo uno de los dos).
    id_atributo_primario, id_dominio_primario = repo.buscar_atributo_primario(
        conn, pendiente.atributo_primario_texto, pendiente.dominio_primario_texto
    )
    return {
        "id_dominio": id_dominio,
        "id_criticidad": repo.resolver_catalogo_controlado(
            conn, "cat_criticidad", "nombre_criticidad", pendiente.nivel_criticidad_texto
        ),
        # cat_dato_personal se cruza por CODIGO, no por el texto descriptivo
        # completo -- el Excel trae "6. Crediticios", "6.1. Datos
        # Identificativos...", "2. Medio"; se extrae "6", "6.1", "2" y se
        # busca contra codigo_categoria_nivel_uno/codigo_tipo_dato/
        # codigo_sensibilidad.
        "id_dato_personal": repo.resolver_dato_personal(
            conn,
            extraer_codigo_categoria_nivel_uno(pendiente.categoria_nivel_1_texto),
            extraer_codigo_tipo_dato(pendiente.tipo_dato_texto),
            extraer_codigo_sensibilidad(pendiente.sensibilidad_texto),
        ),
        "id_tipo_atributo": repo.resolver_catalogo_controlado(
            conn, "cat_tipo_atributo", "nombre_tipo_atributo", pendiente.tipo_atributo_texto
        ),
        # id_clasificacion: eliminado del modelo (2026-09-14). Confirmado que
        # no tenia FK ni catalogo de respaldo en gdd, ninguna columna del
        # Excel (las 7 hojas de la plantilla real fueron revisadas) lo
        # alimentaba, y este loader siempre lo escribia como NULL. Ver
        # gdd_repository.py / alter_id_clasificacion.sql para el DROP COLUMN.
        "id_compartido_similar": repo.resolver_catalogo_controlado(
            conn,
            "cat_compartido_similar",
            "nombre_compartido_similar",
            pendiente.compartido_similar_texto,
        ),
        # Mejor esfuerzo: atributo_primario/dominio_primario son texto libre
        # en el Excel (nombres descriptivos, no codigos) y no tienen FK
        # definida en la BD. Si no calzan (juntos) con nada existente,
        # quedan NULL (se registra advertencia, no se aborta la carga por
        # esto) -- ver buscar_atributo_primario.
        "id_atributo_primario": id_atributo_primario,
        "id_dominio_primario": id_dominio_primario,
        "es_dato_personal": pendiente.es_dato_personal,
        "reporte": pendiente.reporte,
        "proyecto": pendiente.proyecto,
        "proceso": pendiente.proceso,
        "estructura": pendiente.estructura,
        "nombre_atributo": pendiente.nombre_atributo,
        "descripcion_atributo": pendiente.descripcion_atributo,
        "fecha_aprobacion": pendiente.fecha_aprobacion,
        "codigo_dominio": pendiente.codigo_dominio,
        "codigo_atributo": pendiente.codigo_atributo,
    }


def _resolver_id_bdd(conn, pendiente: FuenteOficialPendiente) -> int:
    """Resuelve (get-or-create) servidor + base de datos fuente.

    Se separa de `_resolver_campos_fuente` porque id_bdd ahora es parte de
    la CLAVE NATURAL de la fuente oficial (ver `_clave_fuente`): hay que
    conocerlo ANTES de calcular el plan de merge, no solo al insertar.
    """
    id_servidor = (
        repo.resolver_o_crear_servidor(conn, pendiente.servidor_texto)
        if pendiente.servidor_texto is not None
        else None
    )
    id_bdd = (
        repo.resolver_o_crear_bdd(conn, id_servidor, pendiente.base_datos_texto)
        if id_servidor is not None and pendiente.base_datos_texto is not None
        else None
    )
    if id_bdd is None:
        raise ValueError(
            f"{pendiente.codigo_dominio_atributo} (clase='{pendiente.clase_texto}', "
            f"primaria={pendiente.es_fuente_primaria}): falta servidor y/o base de datos "
            "fuente -- gdd.atributo_fuente_oficial.id_bdd es obligatorio."
        )
    return id_bdd


def _clave_fuente(pendiente: FuenteOficialPendiente, id_bdd: int) -> tuple[str, str, int, bool, str]:
    """Clave natural real de una fuente oficial dentro de un mismo atributo:
    (clase, nombre_campo, id_bdd, es_fuente_primaria, nombre_tabla).

    nombre_tabla agregado 2026-10-01: con la clave anterior, filas del mismo
    servidor/base/clase/campo que solo difieren en la tabla o archivo de
    origen (ej. category_id en 3 tablas distintas de PROD1) colapsaban en
    una sola -- confirmado con staging.metadata_tecnica real (consulta de
    diagnostico del 2026-10-01).

    (clase, es_fuente_primaria) por si solo NO alcanza -- confirmado con los
    210 registros reales de metadata_tecnica del dominio ADS: varias filas
    comparten `clase` pero vienen de sistemas/tablas fuente distintos (ej.
    nucleo NOVA vs Coris), diferenciados por nombre_campo_fuente_oficial y/o
    base_datos_fuente_oficial. Con la clave vieja, 210 filas colapsaban en
    94 (perdida silenciosa de 116); esta clave no tiene colisiones en esos
    mismos datos.
    """
    return (pendiente.clase_texto or "", pendiente.nombre_campo, id_bdd, pendiente.es_fuente_primaria,
            pendiente.nombre_tabla or "")


def _resolver_campos_fuente(
    conn, id_atributo: int, id_bdd: int, pendiente: FuenteOficialPendiente
) -> dict:
    if not pendiente.nombre_campo:
        raise ValueError(
            f"{pendiente.codigo_dominio_atributo} (clase='{pendiente.clase_texto}', "
            f"primaria={pendiente.es_fuente_primaria}): nombre_campo vacio -- es obligatorio."
        )

    return {
        "id_atributo": id_atributo,
        "id_bdd": id_bdd,
        "id_aplicacion": None,
        "puerto": None,
        "ip": None,
        "es_fuente_primaria": pendiente.es_fuente_primaria,
        "observacion": None,
        "fecha_aprobacion": pendiente.fecha_aprobacion,
        # id_campo_fuente_valor_valido se resuelve contra tipo_campo
        # (DATE/DECIMAL/VARCHAR -- un catalogo tecnico get-or-create), NO
        # contra lista_valores_validos: esa es texto libre potencialmente
        # largo (se vio hasta 265 caracteres reales) y no tiene sentido
        # como catalogo de un solo valor por fila.
        "id_campo_fuente_valor_valido": repo.resolver_o_crear_valor_valido(
            conn, pendiente.tipo_campo_texto
        ),
        "nombre_campo": pendiente.nombre_campo,
        "longitud_campo": pendiente.longitud_campo,
        "clase": pendiente.clase_texto,
        # Texto libre (2026-10-01), sin catalogo -- parte de la clave natural.
        "nombre_tabla": pendiente.nombre_tabla,
        "acepta_valores_nulos": pendiente.acepta_valores_nulos,
        "formula_calculo": pendiente.formula_calculo,
        # Texto libre, sin catalogo/FK -- se guarda tal cual viene del Excel.
        "lista_valores_validos": pendiente.lista_valores_validos_texto,
    }


def _resolver_destino_consumo(conn, pendiente: ConsumoPendiente) -> tuple[int, int]:
    """Resuelve el destino completo de un consumo -- REFACTOR 2026-09-18,
    segunda vuelta (ver nota de modulo). Devuelve (id_bdd, id_tabla):

    - servidor: get-or-create contra gdd.servidor_fuente, SIN CAMBIOS
      (decision del usuario 2026-09-17: son los mismos servidores fisicos
      que fuente oficial) -- se resuelve solo como paso intermedio para
      encontrar id_bdd (igual que en `_resolver_id_bdd` de fuente oficial);
      NO se devuelve ni se guarda en atributo_fuente_consumo (correccion de
      la segunda vuelta: esa relacion ya existe via
      base_datos_fuente.id_servidor).
    - id_bdd: coleccion_foc resuelto contra gdd.base_datos_fuente, CONTROLADO
      (repo.resolver_bdd_controlado) -- a diferencia de _resolver_id_bdd
      (fuente oficial, sigue get-or-create).
    - id_tabla: tabla_bv_foc resuelto SIEMPRE contra gdd.base_datos_fuente_tabla,
      CONTROLADO (repo.resolver_tabla_controlada) -- correccion de la segunda
      vuelta: ya no hay excepcion de texto libre para tipo='base_datos'.

    Se separa de `_resolver_campos_consumo` porque el destino completo es
    parte de la CLAVE NATURAL (ver `_clave_consumo`), igual que id_bdd en
    `_resolver_id_bdd` para fuente oficial.
    """
    id_servidor = repo.resolver_o_crear_servidor(conn, pendiente.servidor_texto)
    id_bdd = repo.resolver_bdd_controlado(conn, id_servidor, pendiente.coleccion_texto)
    id_tabla = repo.resolver_tabla_controlada(conn, id_bdd, pendiente.nombre_tabla)
    return id_bdd, id_tabla


def _clave_consumo(pendiente: ConsumoPendiente, id_bdd: int, id_tabla: int) -> tuple[int, int, str]:
    """Clave natural real de un destino de consumo dentro de un mismo
    atributo: (id_bdd, id_tabla, nombre_campo)."""
    return (id_bdd, id_tabla, pendiente.nombre_campo)


def _resolver_campos_consumo(
    id_atributo: int, id_bdd: int, id_tabla: int, pendiente: ConsumoPendiente
) -> dict:
    return {
        "id_atributo": id_atributo,
        "id_bdd": id_bdd,
        "id_tabla": id_tabla,
        "nombre_campo": pendiente.nombre_campo,
    }


def _siguiente_version(version_actual: str | None) -> str:
    """version es automatica, ligada a fecha_aprobacion: arranca en "1" en
    la primera carga y sube en +1 cada vez que el merge REEMPLAZA la fila
    por una fecha_aprobacion mas reciente. Si la version existente no es
    un entero simple (dato historico/manual), se reinicia en "1" y se
    registra advertencia en vez de fallar toda la carga por esto.
    """
    if version_actual is None:
        return "1"
    try:
        return str(int(version_actual) + 1)
    except ValueError:
        logger.warning(
            "version existente '%s' no es un entero simple -- se reinicia en '1'",
            version_actual,
        )
        return "1"


def ejecutar_merge_dominio(
    engine: Engine,
    staging_repo: StagingRepository,
    codigo_dominio: str,
    id_carga: int | None = None,
) -> ResultadoMergeDominio:
    resultado = ResultadoMergeDominio(codigo_dominio=codigo_dominio)

    try:
        filas_detalle = staging_repo.leer(
            "staging.detalle_atributos", "codigo_dominio", codigo_dominio
        )
        filas_metadata = staging_repo.leer_por_prefijo(
            "staging.metadata_tecnica", "codigo_dominio_atributo", codigo_dominio
        )

        atributos_pendientes = mapear_detalle_atributos(filas_detalle)
        fuentes_pendientes = mapear_metadata_tecnica(filas_metadata)
        consumos_pendientes = mapear_atributo_fuente_consumo(filas_metadata)

        fuentes_por_codigo: dict[str, list[FuenteOficialPendiente]] = {}
        for f in fuentes_pendientes:
            fuentes_por_codigo.setdefault(f.codigo_dominio_atributo, []).append(f)

        consumos_por_codigo: dict[str, list[ConsumoPendiente]] = {}
        for c in consumos_pendientes:
            consumos_por_codigo.setdefault(c.codigo_dominio_atributo, []).append(c)

        with engine.begin() as conn:
            id_dominio = repo.resolver_dominio_id(conn, codigo_dominio)

            existentes_atributo = repo.atributos_existentes(conn, codigo_dominio)
            entrantes_atributo = {
                a.clave_natural: (a, a.fecha_aprobacion) for a in atributos_pendientes
            }
            plan_atributo = calcular_plan_merge(existentes_atributo, entrantes_atributo)

            id_atributo_por_clave: dict[tuple, int] = {
                clave: existente.id for clave, existente in existentes_atributo.items()
            }

            for op in plan_atributo:
                codigo_dominio_atributo_op = f"{codigo_dominio}-{op.clave[1]}"
                if op.accion == Accion.INSERTAR:
                    campos = _resolver_campos_atributo(conn, id_dominio, op.entrante)
                    campos["version"] = "1"
                    nuevo_id = repo.insertar_atributo(conn, campos)
                    id_atributo_por_clave[op.clave] = nuevo_id
                    resultado.atributos_insertados += 1
                    repo.registrar_bitacora(
                        conn, "atributo", nuevo_id, Accion.INSERTAR.value,
                        codigo_dominio, codigo_dominio_atributo_op, campos["version"],
                        id_carga=id_carga,
                    )
                elif op.accion == Accion.ACTUALIZAR:
                    # Misma fecha_aprobacion: es una correccion, no una
                    # nueva version aprobada -- version/release NO se tocan.
                    campos = _resolver_campos_atributo(conn, id_dominio, op.entrante)
                    id_atributo_por_clave[op.clave] = op.id_existente
                    actuales = repo.campos_actuales(conn, "atributo", op.id_existente, campos.keys())
                    if _hay_cambios(campos, actuales):
                        repo.actualizar_atributo(conn, op.id_existente, campos)
                        resultado.atributos_actualizados += 1
                        repo.registrar_bitacora(
                            conn, "atributo", op.id_existente, Accion.ACTUALIZAR.value,
                            codigo_dominio, codigo_dominio_atributo_op, None,
                            id_carga=id_carga,
                        )
                    else:
                        # Misma fecha_aprobacion Y mismos valores -- corrida
                        # repetida sin cambios reales (ej. 3 veces al dia
                        # con el mismo Excel). No se escribe UPDATE ni
                        # linea de bitacora.
                        resultado.atributos_sin_cambios += 1
                elif op.accion == Accion.REEMPLAZAR:
                    version_anterior, release_anterior = repo.version_release_atributo(
                        conn, op.id_existente
                    )
                    # repo.eliminar_atributo hace BAJA LOGICA (activo=0), no
                    # DELETE fisico -- la fila vieja queda como historial de
                    # la version anterior, no desaparece.
                    repo.eliminar_atributo(conn, op.id_existente)
                    campos = _resolver_campos_atributo(conn, id_dominio, op.entrante)
                    campos["version"] = _siguiente_version(version_anterior)
                    campos["release"] = release_anterior
                    nuevo_id = repo.insertar_atributo(conn, campos)
                    id_atributo_por_clave[op.clave] = nuevo_id
                    resultado.atributos_reemplazados += 1
                    # Una sola linea de bitacora por REEMPLAZAR, con el id
                    # NUEVO (la fila resultante) -- la fila vieja no genera
                    # una segunda linea de ELIMINAR aparte.
                    repo.registrar_bitacora(
                        conn, "atributo", nuevo_id, Accion.REEMPLAZAR.value,
                        codigo_dominio, codigo_dominio_atributo_op, campos["version"],
                        id_carga=id_carga,
                    )
                elif op.accion == Accion.ELIMINAR:
                    repo.eliminar_atributo(conn, op.id_existente)
                    id_atributo_por_clave.pop(op.clave, None)
                    resultado.atributos_eliminados += 1
                    repo.registrar_bitacora(
                        conn, "atributo", op.id_existente, Accion.ELIMINAR.value,
                        codigo_dominio, codigo_dominio_atributo_op, None,
                        id_carga=id_carga,
                    )
                elif op.accion == Accion.OMITIR_FECHA_RETROCEDE:
                    logger.warning(
                        "%s-%s: fecha_aprobacion entrante (%s) es anterior a la ya "
                        "registrada -- se omite, no se retrocede version",
                        *op.clave,
                        op.entrante.fecha_aprobacion,
                    )
                    resultado.atributos_omitidos_fecha_retrocede += 1
                    repo.registrar_bitacora(
                        conn, "atributo", op.id_existente, Accion.OMITIR_FECHA_RETROCEDE.value,
                        codigo_dominio, codigo_dominio_atributo_op, None,
                        id_carga=id_carga,
                    )

            for clave, id_atributo in list(id_atributo_por_clave.items()):
                codigo_dominio_atributo = f"{clave[0]}-{clave[1]}"
                fuentes = fuentes_por_codigo.get(codigo_dominio_atributo, [])

                existentes_fuente = repo.fuente_oficial_existentes(conn, id_atributo)

                # id_bdd es parte de la clave natural (ver _clave_fuente) y
                # solo se conoce tras resolver servidor+bdd contra la BD --
                # se resuelve aqui, ANTES de calcular el plan de merge, y se
                # guarda por clave para no tener que re-resolverlo al
                # insertar/actualizar/reemplazar.
                entrantes_fuente: dict = {}
                id_bdd_por_clave: dict = {}
                for f in fuentes:
                    id_bdd_f = _resolver_id_bdd(conn, f)
                    clave_f = _clave_fuente(f, id_bdd_f)
                    entrantes_fuente[clave_f] = (f, f.fecha_aprobacion)
                    id_bdd_por_clave[clave_f] = id_bdd_f

                plan_fuente = calcular_plan_merge(existentes_fuente, entrantes_fuente)

                for op in plan_fuente:
                    if op.accion == Accion.INSERTAR:
                        campos = _resolver_campos_fuente(
                            conn, id_atributo, id_bdd_por_clave[op.clave], op.entrante
                        )
                        campos["version"] = "1"
                        nuevo_id_fuente = repo.insertar_fuente_oficial(conn, campos)
                        resultado.fuentes_insertadas += 1
                        repo.registrar_bitacora(
                            conn, "atributo_fuente_oficial", nuevo_id_fuente, Accion.INSERTAR.value,
                            codigo_dominio, codigo_dominio_atributo, campos["version"],
                            id_carga=id_carga,
                        )
                    elif op.accion == Accion.ACTUALIZAR:
                        campos = _resolver_campos_fuente(
                            conn, id_atributo, id_bdd_por_clave[op.clave], op.entrante
                        )
                        actuales = repo.campos_actuales(
                            conn, "atributo_fuente_oficial", op.id_existente, campos.keys()
                        )
                        if _hay_cambios(campos, actuales):
                            repo.actualizar_fuente_oficial(conn, op.id_existente, campos)
                            resultado.fuentes_actualizadas += 1
                            repo.registrar_bitacora(
                                conn, "atributo_fuente_oficial", op.id_existente, Accion.ACTUALIZAR.value,
                                codigo_dominio, codigo_dominio_atributo, None,
                                id_carga=id_carga,
                            )
                        else:
                            resultado.fuentes_sin_cambios += 1
                    elif op.accion == Accion.REEMPLAZAR:
                        version_anterior, release_anterior = repo.version_release_fuente_oficial(
                            conn, op.id_existente
                        )
                        # Baja logica, no fisica -- ver eliminar_atributo.
                        repo.eliminar_fuente_oficial(conn, op.id_existente)
                        campos = _resolver_campos_fuente(
                            conn, id_atributo, id_bdd_por_clave[op.clave], op.entrante
                        )
                        campos["version"] = _siguiente_version(version_anterior)
                        campos["release"] = release_anterior
                        nuevo_id_fuente = repo.insertar_fuente_oficial(conn, campos)
                        resultado.fuentes_reemplazadas += 1
                        repo.registrar_bitacora(
                            conn, "atributo_fuente_oficial", nuevo_id_fuente, Accion.REEMPLAZAR.value,
                            codigo_dominio, codigo_dominio_atributo, campos["version"],
                            id_carga=id_carga,
                        )
                    elif op.accion == Accion.ELIMINAR:
                        repo.eliminar_fuente_oficial(conn, op.id_existente)
                        resultado.fuentes_eliminadas += 1
                        repo.registrar_bitacora(
                            conn, "atributo_fuente_oficial", op.id_existente, Accion.ELIMINAR.value,
                            codigo_dominio, codigo_dominio_atributo, None,
                            id_carga=id_carga,
                        )
                    elif op.accion == Accion.OMITIR_FECHA_RETROCEDE:
                        logger.warning(
                            "%s (clase=%s, campo=%s): fecha_aprobacion entrante es anterior "
                            "a la ya registrada -- se omite",
                            codigo_dominio_atributo,
                            op.clave[0],
                            op.clave[1],
                        )
                        resultado.fuentes_omitidas_fecha_retrocede += 1
                        repo.registrar_bitacora(
                            conn, "atributo_fuente_oficial", op.id_existente,
                            Accion.OMITIR_FECHA_RETROCEDE.value,
                            codigo_dominio, codigo_dominio_atributo, None,
                            id_carga=id_carga,
                        )

                # atributo_fuente_consumo (2026-09-17, alcance ampliado;
                # REFACTORIZADA 2026-09-18): ver nota de modulo. Mismo patron
                # INSERTAR/ACTUALIZAR/ELIMINAR sin fecha (UPDATE in-place)
                # que respaldo/plan_remediacion, sin bitacora. El destino
                # completo (servidor/bdd/tabla) se resuelve ANTES del plan
                # (parte de la clave natural, ver `_clave_consumo`).
                consumos = consumos_por_codigo.get(codigo_dominio_atributo, [])
                existentes_consumo = repo.consumo_existentes(conn, id_atributo)

                entrantes_consumo: dict = {}
                destino_por_clave: dict = {}
                for c in consumos:
                    id_bdd_c, id_tabla_c = _resolver_destino_consumo(conn, c)
                    clave_c = _clave_consumo(c, id_bdd_c, id_tabla_c)
                    entrantes_consumo[clave_c] = (c, None)
                    destino_por_clave[clave_c] = (id_bdd_c, id_tabla_c)

                plan_consumo = calcular_plan_merge(existentes_consumo, entrantes_consumo)

                for op in plan_consumo:
                    if op.accion == Accion.INSERTAR:
                        id_bdd_c, id_tabla_c = destino_por_clave[op.clave]
                        campos = _resolver_campos_consumo(id_atributo, id_bdd_c, id_tabla_c, op.entrante)
                        repo.insertar_atributo_fuente_consumo(conn, campos)
                        resultado.consumos_insertados += 1
                    elif op.accion == Accion.ACTUALIZAR:
                        # Ya no quedan campos mutables -- la clave natural
                        # completa (id_servidor, id_bdd, id_tabla,
                        # nombre_tabla_libre, nombre_campo) ya identifica el
                        # destino; si el plan emite ACTUALIZAR es porque esa
                        # clave coincidio entre lo existente y lo entrante,
                        # nunca hay nada que escribir.
                        resultado.consumos_sin_cambios += 1
                    elif op.accion == Accion.ELIMINAR:
                        repo.eliminar_atributo_fuente_consumo(conn, op.id_existente)
                        resultado.consumos_eliminados += 1
                    else:
                        raise AssertionError(
                            f"Accion inesperada para atributo_fuente_consumo: {op.accion!r} -- no "
                            "deberia poder ocurrir sin fecha_aprobacion en ninguno de los dos lados."
                        )

    except Exception as exc:  # noqa: BLE001 - se reporta y se aborta SOLO este dominio
        logger.error("Merge de dominio '%s' abortado: %s", codigo_dominio, exc)
        resultado.error = str(exc)

    return resultado


@dataclass
class ResultadoMergeInvestigacion:
    codigo_dominio: str
    insertadas: int = 0
    actualizadas: int = 0
    eliminadas: int = 0
    sin_cambios: int = 0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def _resolver_campos_investigacion(conn, id_dominio: int, pendiente: InvestigacionPendiente) -> dict:
    return {
        "id_dominio": id_dominio,
        "codigo_dominio": pendiente.codigo_dominio,
        "id_clasificacion": repo.resolver_catalogo_controlado(
            conn, "cat_clasificacion_investigacion", "nombre_clasificacion", pendiente.clasificacion_texto
        ),
        "nombre": pendiente.nombre,
        "descripcion": pendiente.descripcion,
        "referencia_normativa": pendiente.referencia_normativa,
    }


def ejecutar_merge_investigacion_dominio(
    engine: Engine, staging_repo: StagingRepository, codigo_dominio: str
) -> ResultadoMergeInvestigacion:
    """Merge de staging.investigacion -> gdd.investigacion, por dominio.

    Transaccion PROPIA, separada de `ejecutar_merge_dominio`: gdd.investigacion
    no tiene FK hacia atributo/atributo_fuente_oficial (es material de
    referencia del dominio completo, no de un atributo puntual), asi que un
    problema aqui no deja atributo/fuente a medio actualizar y viceversa --
    cada tabla se mergea de forma independiente y todo-o-nada por si misma.

    Clave natural: (codigo_dominio, nombre). A diferencia de atributo/
    fuente_oficial, la hoja Investigacion no trae fecha de aprobacion por
    fila -- se reutiliza gdd_merge_logic.calcular_plan_merge pasando
    fecha_aprobacion=None siempre en ambos lados (existente y entrante): esa
    funcion, sin fecha para comparar, solo puede emitir INSERTAR/ACTUALIZAR/
    ELIMINAR -- REEMPLAZAR/OMITIR_FECHA_RETROCEDE nunca se generan aqui (si
    aparecieran seria un bug real, por eso el `else` de abajo revienta en
    vez de ignorarlos en silencio). ACTUALIZAR es UPDATE in-place (no hay
    version que versionar) y usa el mismo `_hay_cambios` que atributo para
    no tocar la fila si nada cambio de verdad -- evita que una corrida sin
    cambios reales en el Excel genere ruido corriendo el merge varias veces
    al dia (bug real detectado por el usuario en la primera version de este
    merge, que hacia reemplazo completo por dominio).
    """
    resultado = ResultadoMergeInvestigacion(codigo_dominio=codigo_dominio)

    try:
        filas_staging = staging_repo.leer(
            "staging.investigacion", "codigo_dominio", codigo_dominio
        )
        pendientes = mapear_investigacion(filas_staging)

        with engine.begin() as conn:
            id_dominio = repo.resolver_dominio_id(conn, codigo_dominio)

            existentes = repo.investigacion_existentes(conn, codigo_dominio)
            entrantes = {p.nombre: (p, None) for p in pendientes}
            plan = calcular_plan_merge(existentes, entrantes)

            for op in plan:
                if op.accion == Accion.INSERTAR:
                    campos = _resolver_campos_investigacion(conn, id_dominio, op.entrante)
                    repo.insertar_investigacion(conn, campos)
                    resultado.insertadas += 1
                elif op.accion == Accion.ACTUALIZAR:
                    campos = _resolver_campos_investigacion(conn, id_dominio, op.entrante)
                    campos_mutables = {
                        "id_clasificacion": campos["id_clasificacion"],
                        "descripcion": campos["descripcion"],
                        "referencia_normativa": campos["referencia_normativa"],
                    }
                    actuales = repo.campos_actuales(
                        conn, "investigacion", op.id_existente, campos_mutables.keys()
                    )
                    if _hay_cambios(campos_mutables, actuales):
                        repo.actualizar_investigacion(conn, op.id_existente, campos)
                        resultado.actualizadas += 1
                    else:
                        resultado.sin_cambios += 1
                elif op.accion == Accion.ELIMINAR:
                    repo.eliminar_investigacion(conn, op.id_existente)
                    resultado.eliminadas += 1
                else:
                    raise AssertionError(
                        f"Accion inesperada para investigacion: {op.accion!r} -- no deberia "
                        "poder ocurrir sin fecha_aprobacion en ninguno de los dos lados."
                    )

    except Exception as exc:  # noqa: BLE001 - se reporta y se aborta SOLO este dominio
        logger.error("Merge de investigacion para dominio '%s' abortado: %s", codigo_dominio, exc)
        resultado.error = str(exc)

    return resultado


@dataclass
class ResultadoMergeEstructura:
    codigo_dominio: str
    insertadas: int = 0
    actualizadas: int = 0
    eliminadas: int = 0
    sin_cambios: int = 0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def _resolver_campos_estructura(conn, id_dominio: int, pendiente: EstructuraPendiente) -> dict:
    return {
        "id_dominio": id_dominio,
        "id_rol": repo.resolver_catalogo_controlado(
            conn, "cat_rol", "nombre_rol", pendiente.rol_texto
        ),
        "area": pendiente.area,
        "codigo_plaza": pendiente.codigo_plaza,
        "nombre_plaza": pendiente.nombre_plaza,
        "nombre_responsable": pendiente.nombre_responsable,
        "fecha_aprobada": pendiente.fecha_aprobada,
    }


def ejecutar_merge_estructura_dominio(
    engine: Engine, staging_repo: StagingRepository, codigo_dominio: str
) -> ResultadoMergeEstructura:
    """Merge de staging.estructura -> gdd.dominio_responsable, por dominio.

    Transaccion PROPIA, separada de `ejecutar_merge_dominio` e independiente
    de `ejecutar_merge_investigacion_dominio`: gdd.dominio_responsable no
    tiene FK hacia atributo/atributo_fuente_oficial/investigacion.

    Clave natural: (codigo_dominio, codigo_plaza) -- confirmado con el
    usuario que codigo_plaza identifica la plaza de forma unica dentro de
    un dominio. Igual que investigacion, la hoja no trae fecha de aprobacion
    utilizable para versionar (fecha_aprobada se guarda como dato
    informativo, no dispara REEMPLAZAR): se reutiliza
    gdd_merge_logic.calcular_plan_merge pasando fecha_aprobacion=None
    siempre en ambos lados -- solo puede emitir INSERTAR/ACTUALIZAR/
    ELIMINAR (REEMPLAZAR/OMITIR_FECHA_RETROCEDE nunca se generan aqui; si
    aparecieran seria un bug real, por eso el `else` de abajo revienta en
    vez de ignorarlos en silencio). ACTUALIZAR es UPDATE in-place y usa el
    mismo `_hay_cambios` que investigacion para no tocar la fila si nada
    cambio de verdad entre corridas del mismo dia.
    """
    resultado = ResultadoMergeEstructura(codigo_dominio=codigo_dominio)

    try:
        filas_staging = staging_repo.leer(
            "staging.estructura", "codigo_dominio", codigo_dominio
        )
        pendientes = mapear_estructura(filas_staging)

        with engine.begin() as conn:
            id_dominio = repo.resolver_dominio_id(conn, codigo_dominio)

            existentes = repo.dominio_responsable_existentes(conn, id_dominio)
            entrantes = {p.codigo_plaza: (p, None) for p in pendientes}
            plan = calcular_plan_merge(existentes, entrantes)

            for op in plan:
                if op.accion == Accion.INSERTAR:
                    campos = _resolver_campos_estructura(conn, id_dominio, op.entrante)
                    repo.insertar_dominio_responsable(conn, campos)
                    resultado.insertadas += 1
                elif op.accion == Accion.ACTUALIZAR:
                    campos = _resolver_campos_estructura(conn, id_dominio, op.entrante)
                    campos_mutables = {
                        k: v for k, v in campos.items() if k not in ("id_dominio", "codigo_plaza")
                    }
                    actuales = repo.campos_actuales(
                        conn, "dominio_responsable", op.id_existente, campos_mutables.keys()
                    )
                    if _hay_cambios(campos_mutables, actuales):
                        repo.actualizar_dominio_responsable(conn, op.id_existente, campos)
                        resultado.actualizadas += 1
                    else:
                        resultado.sin_cambios += 1
                elif op.accion == Accion.ELIMINAR:
                    repo.eliminar_dominio_responsable(conn, op.id_existente)
                    resultado.eliminadas += 1
                else:
                    raise AssertionError(
                        f"Accion inesperada para dominio_responsable: {op.accion!r} -- no "
                        "deberia poder ocurrir sin fecha_aprobacion en ninguno de los dos lados."
                    )

    except Exception as exc:  # noqa: BLE001 - se reporta y se aborta SOLO este dominio
        logger.error("Merge de estructura para dominio '%s' abortado: %s", codigo_dominio, exc)
        resultado.error = str(exc)

    return resultado


@dataclass
class ResultadoMergeRespaldo:
    codigo_dominio: str
    insertadas: int = 0
    actualizadas: int = 0
    eliminadas: int = 0
    sin_cambios: int = 0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def _clave_respaldo(conn, pendiente: RespaldoPendiente) -> tuple[int, str]:
    """(id_tipo_respaldo, enlace_respaldo) -- a diferencia de investigacion/
    estructura, aqui la parte resuelta contra catalogo (tipo_respaldo) SI es
    parte de la clave natural, no solo un campo mutable, asi que se resuelve
    ANTES de construir el plan de merge (mismo motivo que id_bdd en
    _clave_fuente para atributo_fuente_oficial: comparar por id ya resuelto,
    no por texto).
    """
    id_tipo_respaldo = repo.resolver_catalogo_controlado(
        conn, "cat_tipo_respaldo", "nombre_tipo_respaldo", pendiente.tipo_respaldo_texto
    )
    return (id_tipo_respaldo, pendiente.enlace_respaldo)


def _resolver_campos_respaldo(conn, id_dominio: int, id_tipo_respaldo: int, pendiente: RespaldoPendiente) -> dict:
    return {
        "id_dominio": id_dominio,
        "codigo_dominio": pendiente.codigo_dominio,
        "id_tipo_respaldo": id_tipo_respaldo,
        "enlace_respaldo": pendiente.enlace_respaldo,
        "observaciones": pendiente.observaciones,
        "fecha_aprobada": pendiente.fecha_aprobada,
    }


def ejecutar_merge_respaldos_dominio(
    engine: Engine, staging_repo: StagingRepository, codigo_dominio: str
) -> ResultadoMergeRespaldo:
    """Merge de staging.respaldos -> gdd.respaldo, por dominio.

    Transaccion PROPIA, independiente de las demas (gdd.respaldo no tiene FK
    hacia atributo/fuente/investigacion/dominio_responsable).

    Clave natural: (codigo_dominio, tipo_respaldo, enlace_respaldo) -- el
    Excel real trae mas de una fila con el MISMO enlace_respaldo
    diferenciadas solo por tipo_respaldo (ej. "Correo" y "Minuta" apuntando
    a la misma pagina de Confluence), asi que enlace_respaldo solo no
    alcanza como clave. id_tipo_respaldo se resuelve ANTES de construir el
    plan (ver `_clave_respaldo`), para que la clave use el id ya resuelto en
    ambos lados (existente y entrante) en vez de comparar texto. Igual que
    investigacion/estructura, no hay fecha de aprobacion utilizable para
    versionar (fecha_aprobada es texto libre): se reutiliza
    gdd_merge_logic.calcular_plan_merge pasando fecha_aprobacion=None
    siempre -- solo puede emitir INSERTAR/ACTUALIZAR/ELIMINAR. ACTUALIZAR es
    UPDATE in-place (solo observaciones/fecha_aprobada son mutables -- el
    tipo y el enlace son la clave) y usa el mismo `_hay_cambios` para no
    tocar la fila si nada cambio de verdad.
    """
    resultado = ResultadoMergeRespaldo(codigo_dominio=codigo_dominio)

    try:
        filas_staging = staging_repo.leer("staging.respaldos", "codigo_dominio", codigo_dominio)
        pendientes = mapear_respaldos(filas_staging)

        with engine.begin() as conn:
            id_dominio = repo.resolver_dominio_id(conn, codigo_dominio)

            existentes = repo.respaldo_existentes(conn, codigo_dominio)
            entrantes = {_clave_respaldo(conn, p): (p, None) for p in pendientes}
            plan = calcular_plan_merge(existentes, entrantes)

            for op in plan:
                if op.accion == Accion.INSERTAR:
                    id_tipo_respaldo, _ = op.clave
                    campos = _resolver_campos_respaldo(conn, id_dominio, id_tipo_respaldo, op.entrante)
                    repo.insertar_respaldo(conn, campos)
                    resultado.insertadas += 1
                elif op.accion == Accion.ACTUALIZAR:
                    id_tipo_respaldo, _ = op.clave
                    campos = _resolver_campos_respaldo(conn, id_dominio, id_tipo_respaldo, op.entrante)
                    campos_mutables = {
                        "observaciones": campos["observaciones"],
                        "fecha_aprobada": campos["fecha_aprobada"],
                    }
                    actuales = repo.campos_actuales(
                        conn, "respaldo", op.id_existente, campos_mutables.keys()
                    )
                    if _hay_cambios(campos_mutables, actuales):
                        repo.actualizar_respaldo(conn, op.id_existente, campos)
                        resultado.actualizadas += 1
                    else:
                        resultado.sin_cambios += 1
                elif op.accion == Accion.ELIMINAR:
                    repo.eliminar_respaldo(conn, op.id_existente)
                    resultado.eliminadas += 1
                else:
                    raise AssertionError(
                        f"Accion inesperada para respaldo: {op.accion!r} -- no deberia poder "
                        "ocurrir sin fecha_aprobacion en ninguno de los dos lados."
                    )

    except Exception as exc:  # noqa: BLE001 - se reporta y se aborta SOLO este dominio
        logger.error("Merge de respaldos para dominio '%s' abortado: %s", codigo_dominio, exc)
        resultado.error = str(exc)

    return resultado


@dataclass
class ResultadoMergePlanRemediacion:
    codigo_dominio: str
    insertadas: int = 0
    actualizadas: int = 0
    eliminadas: int = 0
    sin_cambios: int = 0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def _resolver_campos_plan_remediacion(conn, pendiente: PlanRemediacionPendiente) -> dict:
    id_atributo = repo.resolver_atributo_por_codigo(
        conn, pendiente.codigo_dominio, pendiente.codigo_atributo
    )
    return {
        "id_atributo": id_atributo,
        "codigo_dominio": pendiente.codigo_dominio,
        "codigo_dominio_atributo": pendiente.codigo_dominio_atributo,
        "id_problema": pendiente.id_problema,
        "id_dimension": repo.resolver_catalogo_controlado(
            conn, "cat_tipo_dimension", "nombre_tipo_dimension", pendiente.dimension_texto
        ),
        # categoria y fuente_oficial_sistema: texto libre, sin catalogo
        # (decision explicita del usuario) -- se guardan tal cual.
        "categoria": pendiente.categoria,
        "fuente_oficial_sistema": pendiente.fuente_oficial_sistema,
        "id_tipo_plan": repo.resolver_catalogo_controlado(
            conn, "cat_tipo_plan", "nombre_tipo_plan", pendiente.tipo_plan_texto
        ),
        "id_categoria_plan": repo.resolver_catalogo_controlado(
            conn, "cat_categoria_plan", "nombre_categoria_plan", pendiente.categoria_plan_texto
        ),
        "id_sub_categoria_plan": repo.resolver_catalogo_controlado(
            conn, "cat_sub_categoria_plan", "nombre_sub_categoria_plan", pendiente.subcategoria_plan_texto
        ),
        "priorizacion": pendiente.priorizacion,
        "id_estado_plan": repo.resolver_catalogo_controlado(
            conn, "cat_estado_plan", "nombre_estado_plan", pendiente.estado_actual_texto
        ),
        "fecha_identificacion": pendiente.fecha_identificacion,
        "fecha_finalizacion_definitiva": pendiente.fecha_finalizacion_definitiva,
        "descripcion_causa_raiz": pendiente.descripcion_causa_raiz,
        "persona_responsable": pendiente.persona_responsable,
        "dependencias": pendiente.dependencias,
        "observacion": pendiente.observacion,
        "acciones_corto_plazo": pendiente.acciones_corto_plazo,
        "acciones_definitivo": pendiente.acciones_definitivo,
        "avance": pendiente.avance,
        "fecha_ultima_modificacion": pendiente.fecha_ultima_modificacion,
        # siro: texto libre (varchar) -- decision explicita del usuario,
        # aunque el dato de origen sea numerico.
        "siro": pendiente.siro,
    }


def ejecutar_merge_plan_remediacion_dominio(
    engine: Engine, staging_repo: StagingRepository, codigo_dominio: str
) -> ResultadoMergePlanRemediacion:
    """Merge de staging.plan_remediacion -> gdd.plan_remediacion, por dominio.

    Transaccion PROPIA, independiente de las demas (gdd.plan_remediacion no
    tiene FK hacia investigacion/dominio_responsable/respaldo -- solo hacia
    atributo, de solo lectura para esta transaccion).

    A diferencia de investigacion/estructura/respaldos (hojas del dominio
    completo que heredan codigo_dominio de DetalleAtributos), esta hoja SI
    trae codigo_dominio_atributo propio -- se lee de staging con
    `leer_por_prefijo` (mismo mecanismo que metadata_tecnica), filtrando por
    'codigo_dominio-%' en vez de por igualdad exacta.

    Clave natural: (codigo_dominio_atributo, id_problema) -- a diferencia de
    respaldo, ningun campo resuelto contra catalogo es parte de la clave
    (dimension/tipo_plan/categoria_plan/subcategoria_plan/estado_actual son
    solo campos mutables), asi que no hace falta resolverlos antes de
    construir el plan -- se resuelven dentro de
    `_resolver_campos_plan_remediacion`, igual que nivel_criticidad en
    atributo. Igual que investigacion/estructura/respaldos, no hay fecha de
    aprobacion utilizable para versionar: se reutiliza
    gdd_merge_logic.calcular_plan_merge pasando fecha_aprobacion=None
    siempre -- solo puede emitir INSERTAR/ACTUALIZAR/ELIMINAR. ACTUALIZAR es
    UPDATE in-place (decision explicita del usuario) y usa el mismo
    `_hay_cambios` para no tocar la fila si nada cambio de verdad. Sin
    bitacora (decision explicita del usuario).
    """
    resultado = ResultadoMergePlanRemediacion(codigo_dominio=codigo_dominio)

    try:
        filas_staging = staging_repo.leer_por_prefijo(
            "staging.plan_remediacion", "codigo_dominio_atributo", codigo_dominio
        )
        pendientes = mapear_plan_remediacion(filas_staging)

        with engine.begin() as conn:
            existentes = repo.plan_remediacion_existentes(conn, codigo_dominio)
            entrantes = {
                (p.codigo_dominio_atributo, p.id_problema): (p, None) for p in pendientes
            }
            plan = calcular_plan_merge(existentes, entrantes)

            for op in plan:
                if op.accion == Accion.INSERTAR:
                    campos = _resolver_campos_plan_remediacion(conn, op.entrante)
                    repo.insertar_plan_remediacion(conn, campos)
                    resultado.insertadas += 1
                elif op.accion == Accion.ACTUALIZAR:
                    campos = _resolver_campos_plan_remediacion(conn, op.entrante)
                    campos_mutables = {
                        k: v
                        for k, v in campos.items()
                        if k not in ("id_atributo", "codigo_dominio", "codigo_dominio_atributo", "id_problema")
                    }
                    actuales = repo.campos_actuales(
                        conn, "plan_remediacion", op.id_existente, campos_mutables.keys()
                    )
                    if _hay_cambios(campos_mutables, actuales):
                        repo.actualizar_plan_remediacion(conn, op.id_existente, campos_mutables)
                        resultado.actualizadas += 1
                    else:
                        resultado.sin_cambios += 1
                elif op.accion == Accion.ELIMINAR:
                    repo.eliminar_plan_remediacion(conn, op.id_existente)
                    resultado.eliminadas += 1
                else:
                    raise AssertionError(
                        f"Accion inesperada para plan_remediacion: {op.accion!r} -- no deberia "
                        "poder ocurrir sin fecha_aprobacion en ninguno de los dos lados."
                    )

    except Exception as exc:  # noqa: BLE001 - se reporta y se aborta SOLO este dominio
        logger.error(
            "Merge de plan_remediacion para dominio '%s' abortado: %s", codigo_dominio, exc
        )
        resultado.error = str(exc)

    return resultado

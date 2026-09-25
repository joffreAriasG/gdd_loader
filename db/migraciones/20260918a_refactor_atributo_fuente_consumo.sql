-- Refactor de trazabilidad de CONSUMO (columnas _foc de MetadataTecnica):
-- reemplaza el diseno de texto libre (2026-09-17) por catalogos controlados
-- -- gdd.base_datos_fuente (+ tipo) y una tabla nueva de tablas conocidas,
-- mantenidas por usuarios expertos -- decision de negocio 2026-09-18: la
-- prueba con negocio del diseno anterior no fue satisfactoria.
--
-- Alcance:
--  1) gdd.base_datos_fuente: ALTER agregando `tipo` ('coleccion' | 'base_datos'),
--     para distinguir bucket S3/big data de motor transaccional (confirmado
--     con el usuario). Sigue siendo "get or create" para el flujo existente
--     de _oficial (fuente de CARGA, sin cambios ahi); el control (error si
--     no existe) se aplica solo al resolver clase_foc para consumo, y vive
--     en el codigo Python (gdd_repository.resolver_bdd_controlado), no aqui.
--  2) gdd.base_datos_fuente_tabla (NUEVA): catalogo controlado de tablas
--     conocidas dentro de una coleccion, con metadata_path/source como
--     informacion adicional. Mantenida por usuarios expertos -- gdd_loader
--     NUNCA escribe aqui, solo resuelve/lee (igual que cat_criticidad,
--     cat_tipo_atributo, etc.).
--  3) gdd.atributo_fuente_consumo: se DROPEA y se recrea -- las columnas
--     viejas (clase/nombre_tabla/nombre_campo, todas texto libre) son
--     incompatibles con el nuevo modelo (id_bdd/id_tabla resueltos contra
--     catalogo). CONFIRMADO CON EL USUARIO 2026-09-18: las filas existentes
--     son solo de una prueba manual, descartables -- no requieren respaldo.
--
-- SUPUESTO (marcado explicitamente para el usuario, no confirmado punto por
-- punto en el chat): cuando base_datos_fuente.tipo = 'base_datos' (motor
-- transaccional), tabla_bv_foc NO tiene catalogo controlado (esa relacion
-- --confirmado-- solo aplica a colecciones) -- se guarda como texto libre en
-- nombre_tabla_libre. Exactamente una de (id_tabla, nombre_tabla_libre) debe
-- tener valor por fila (CHECK ck_afc_tabla_xor). Si esto no es lo que
-- esperabas, es facil de ajustar antes de correr el script.
--
-- Riesgo y rollback:
--  - Pasos 1 y 2 son ADITIVOS, sin riesgo de perdida de datos.
--  - Paso 3 SI PIERDE las filas actuales de atributo_fuente_consumo
--    (confirmado descartable). Si de todas formas se quisiera conservar un
--    respaldo antes de correr este script, descomentar la linea de SELECT
--    INTO en el Paso 3.
--
-- Orden de ejecucion: correr este script COMPLETO, en orden, ANTES de
-- desplegar el codigo Python actualizado (gdd_mapping.py, gdd_repository.py,
-- carga_gdd.py) -- el merge fallaria si el codigo nuevo intenta leer/escribir
-- columnas que todavia no existen.

-- =========================================================================
-- PASO 1: gdd.base_datos_fuente -- agregar columna tipo
-- =========================================================================
ALTER TABLE DGODELIVERY.gdd.base_datos_fuente
    ADD tipo varchar(20) COLLATE Latin1_General_CI_AI NULL
        CONSTRAINT ck_bdd_fuente_tipo CHECK (tipo IN ('coleccion', 'base_datos'));

-- Nota: queda NULL para las filas existentes (no se conoce su tipo hoy).
-- Clasificar manualmente las filas existentes, ej.:
-- UPDATE DGODELIVERY.gdd.base_datos_fuente SET tipo = 'base_datos' WHERE nombre_bdd = '...';
-- UPDATE DGODELIVERY.gdd.base_datos_fuente SET tipo = 'coleccion'  WHERE nombre_bdd = '...';
--
-- IMPORTANTE: para que clase_foc resuelva en el merge, la fila de
-- base_datos_fuente correspondiente debe existir Y tener `tipo` seteado
-- (el codigo Python decide id_tabla vs. texto libre segun este valor).

-- =========================================================================
-- PASO 2: gdd.base_datos_fuente_tabla -- catalogo controlado NUEVO
-- (solo aplica a filas de base_datos_fuente con tipo = 'coleccion')
-- =========================================================================
CREATE TABLE DGODELIVERY.gdd.base_datos_fuente_tabla (
    id             bigint IDENTITY(1,1) NOT NULL,
    id_bdd         bigint NOT NULL,
    nombre_tabla   varchar(255) COLLATE Latin1_General_CI_AI NOT NULL,
    metadata_path  varchar(500) COLLATE Latin1_General_CI_AI NULL,
    source         varchar(255) COLLATE Latin1_General_CI_AI NULL,
    activo         bit NOT NULL CONSTRAINT df_bdd_fuente_tabla_activo DEFAULT (1),
    fecha_baja     date NULL,
    CONSTRAINT pk_base_datos_fuente_tabla PRIMARY KEY (id),
    CONSTRAINT fk_bdd_fuente_tabla_bdd FOREIGN KEY (id_bdd) REFERENCES DGODELIVERY.gdd.base_datos_fuente(id),
    CONSTRAINT uq_bdd_fuente_tabla UNIQUE (id_bdd, nombre_tabla)
);

-- Mantenimiento: esta tabla la carga el equipo experto directamente (INSERT
-- manual o script aparte) -- gdd_loader solo hace SELECT contra ella.

-- =========================================================================
-- PASO 3: gdd.atributo_fuente_consumo -- DROP + CREATE (estructura nueva)
-- =========================================================================

-- Respaldo OPCIONAL antes del DROP (descomentar solo si hiciera falta
-- conservar las filas de la prueba manual; confirmado por el usuario que no
-- hace falta):
-- SELECT * INTO DGODELIVERY.gdd.atributo_fuente_consumo_bak_20260918
-- FROM DGODELIVERY.gdd.atributo_fuente_consumo;

DROP TABLE DGODELIVERY.gdd.atributo_fuente_consumo;

CREATE TABLE DGODELIVERY.gdd.atributo_fuente_consumo (
    id                  bigint IDENTITY(1,1) NOT NULL,
    id_atributo         bigint NOT NULL,
    id_servidor         bigint NOT NULL,
    id_bdd              bigint NOT NULL,
    id_tabla            bigint NULL,
    nombre_tabla_libre  varchar(255) COLLATE Latin1_General_CI_AI NULL,
    nombre_campo        varchar(255) COLLATE Latin1_General_CI_AI NOT NULL,
    activo              bit NOT NULL CONSTRAINT df_afc_activo DEFAULT (1),
    fecha_baja          date NULL,
    CONSTRAINT pk_atributo_fuente_consumo PRIMARY KEY (id),
    CONSTRAINT fk_afc_atributo FOREIGN KEY (id_atributo) REFERENCES DGODELIVERY.gdd.atributo(id),
    CONSTRAINT fk_afc_servidor FOREIGN KEY (id_servidor) REFERENCES DGODELIVERY.gdd.servidor_fuente(id),
    CONSTRAINT fk_afc_bdd FOREIGN KEY (id_bdd) REFERENCES DGODELIVERY.gdd.base_datos_fuente(id),
    CONSTRAINT fk_afc_tabla FOREIGN KEY (id_tabla) REFERENCES DGODELIVERY.gdd.base_datos_fuente_tabla(id),
    -- Exactamente uno de (id_tabla, nombre_tabla_libre): id_tabla cuando
    -- base_datos_fuente.tipo = 'coleccion' (resuelto contra el catalogo
    -- controlado), nombre_tabla_libre cuando tipo = 'base_datos' (no hay
    -- catalogo de tablas para motores transaccionales). Ver SUPUESTO arriba.
    CONSTRAINT ck_afc_tabla_xor CHECK (
        (id_tabla IS NOT NULL AND nombre_tabla_libre IS NULL) OR
        (id_tabla IS NULL AND nombre_tabla_libre IS NOT NULL)
    )
);

-- Verificacion rapida despues de correr el script completo:
-- SELECT COUNT(*) FROM DGODELIVERY.gdd.atributo_fuente_consumo;  -- debe dar 0
-- SELECT name FROM sys.columns WHERE object_id = OBJECT_ID('DGODELIVERY.gdd.base_datos_fuente') AND name = 'tipo';  -- debe existir
-- SELECT COUNT(*) FROM DGODELIVERY.gdd.base_datos_fuente_tabla;  -- debe dar 0 (vacia hasta que el equipo experto la cargue)

-- Rollback completo (mientras nadie haya cargado datos reales todavia):
-- DROP TABLE DGODELIVERY.gdd.atributo_fuente_consumo;
-- DROP TABLE DGODELIVERY.gdd.base_datos_fuente_tabla;
-- ALTER TABLE DGODELIVERY.gdd.base_datos_fuente DROP CONSTRAINT ck_bdd_fuente_tipo;
-- ALTER TABLE DGODELIVERY.gdd.base_datos_fuente DROP COLUMN tipo;
-- (y volver a crear atributo_fuente_consumo con la estructura anterior,
-- ver crear_atributo_fuente_consumo.sql, si hiciera falta revertir del todo)

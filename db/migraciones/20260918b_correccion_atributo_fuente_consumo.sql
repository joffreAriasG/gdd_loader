-- Correccion sobre el refactor de gdd.atributo_fuente_consumo entregado
-- hoy (refactor_atributo_fuente_consumo.sql, ya corrido). Detectada por el
-- usuario 2026-09-18 revisando la tabla resultante:
--
--  1) id_servidor / fk_afc_servidor NO deben existir en
--     atributo_fuente_consumo -- la relacion servidor<->bdd YA EXISTE via
--     base_datos_fuente.id_servidor (mismo patron que
--     gdd.atributo_fuente_oficial, que tampoco tiene id_servidor propio:
--     solo id_bdd, el servidor se alcanza con un join transitivo). El
--     servidor se sigue resolviendo en el codigo (para encontrar id_bdd),
--     simplemente ya no se guarda como columna aparte.
--
--  2) nombre_tabla_libre NO debe existir -- decision revisada: el catalogo
--     controlado gdd.base_datos_fuente_tabla aplica a TODAS las filas de
--     base_datos_fuente (tanto tipo='coleccion' como tipo='base_datos'), no
--     solo a colecciones como se entendio en la primera vuelta. tabla_bv_foc
--     SIEMPRE se resuelve contra ese catalogo -- id_tabla pasa a ser
--     obligatorio (NOT NULL), sin excepcion de texto libre.
--
-- Nota: la columna `tipo` en gdd.base_datos_fuente (agregada en el script
-- anterior) SE MANTIENE -- sigue siendo informacion util de clasificacion
-- (coleccion=bucket S3 vs. base_datos=motor transaccional), simplemente
-- gdd_loader ya no la usa para decidir como resolver tabla_bv_foc. Avisame
-- si tambien la quieres quitar.
--
-- Riesgo: gdd.atributo_fuente_consumo deberia seguir vacia (0 filas) -- el
-- refactor anterior la recreo vacia y no se ha corrido todavia un merge con
-- datos _foc reales. Se vuelve a DROP+CREATE, igual que la vez anterior.
-- Verificar antes de correr:
--   SELECT COUNT(*) FROM DGODELIVERY.gdd.atributo_fuente_consumo;
-- Si NO da 0, avisame antes de correr este script (hay que respaldar).
--
-- Orden de ejecucion: correr este script ANTES de desplegar el codigo
-- Python actualizado (gdd_repository.py, carga_gdd.py) -- el merge fallaria
-- si el codigo nuevo intenta leer/escribir columnas que ya no existen o que
-- todavia no existen.

DROP TABLE DGODELIVERY.gdd.atributo_fuente_consumo;

CREATE TABLE DGODELIVERY.gdd.atributo_fuente_consumo (
    id           bigint IDENTITY(1,1) NOT NULL,
    id_atributo  bigint NOT NULL,
    id_bdd       bigint NOT NULL,
    id_tabla     bigint NOT NULL,
    nombre_campo varchar(255) COLLATE Latin1_General_CI_AI NOT NULL,
    activo       bit NOT NULL CONSTRAINT df_afc_activo DEFAULT (1),
    fecha_baja   date NULL,
    CONSTRAINT pk_atributo_fuente_consumo PRIMARY KEY (id),
    CONSTRAINT fk_afc_atributo FOREIGN KEY (id_atributo) REFERENCES DGODELIVERY.gdd.atributo(id),
    CONSTRAINT fk_afc_bdd FOREIGN KEY (id_bdd) REFERENCES DGODELIVERY.gdd.base_datos_fuente(id),
    CONSTRAINT fk_afc_tabla FOREIGN KEY (id_tabla) REFERENCES DGODELIVERY.gdd.base_datos_fuente_tabla(id)
);

-- Verificacion rapida despues de correr esto:
-- SELECT COUNT(*) FROM DGODELIVERY.gdd.atributo_fuente_consumo;  -- debe dar 0
-- SELECT name FROM sys.columns WHERE object_id = OBJECT_ID('DGODELIVERY.gdd.atributo_fuente_consumo') ORDER BY column_id;
-- (esperado: id, id_atributo, id_bdd, id_tabla, nombre_campo, activo, fecha_baja -- SIN id_servidor ni nombre_tabla_libre)
--
-- IMPORTANTE -- consecuencia practica de id_tabla NOT NULL: antes de poder
-- mergear un atributo con datos _foc reales, gdd.base_datos_fuente_tabla
-- debe tener cargada CADA combinacion (id_bdd, nombre_tabla) que vaya a
-- usarse -- para TODAS las filas de base_datos_fuente que participen,
-- sean tipo='coleccion' o 'base_datos'. Si falta una, el merge aborta ese
-- dominio con CatalogoNoEncontradoError (esperado, no bug).

-- Rollback (mientras la tabla siga vacia):
-- DROP TABLE DGODELIVERY.gdd.atributo_fuente_consumo;
-- (y volver a crear con la estructura de refactor_atributo_fuente_consumo.sql
-- si hiciera falta revertir a la version anterior de este refactor)

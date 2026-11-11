/* ============================================================================
   Estado de vigencia del dominio: Activo / Inactivo / Confidencial
   Base: DGODELIVERY   Fecha: 2026-11-11   gdd_loader: sin cambio de codigo

   Pedido del usuario (2026-11-11): gdd.dominio_dato necesita una columna que
   indique si el dominio esta activo o no, con los valores Activo, Inactivo y
   Confidencial. Es independiente de los estados de gobierno y de calidad
   (id_estado_dominio_gobierno / _calidad -> cat_estado_dominio: Si, No,
   En Progreso), que no se tocan.

   Decisiones (confirmadas por el usuario):
     - Catalogo + FK, igual que cat_tipo_dominio.
     - Obligatorio, con valor por defecto Activo: los dominios existentes
       quedan en Activo y un alta sin estado tambien queda en Activo.

   Objetos:
     - gdd.cat_estado_vigencia_dominio (id bigint PK, nombre_estado_vigencia
       varchar(50) UNIQUE). Ids FIJOS (sin IDENTITY), iguales en pruebas y en
       produccion: 1 Activo, 2 Inactivo, 3 Confidencial. Asi el valor por
       defecto (1) significa lo mismo en todas las bases.
     - gdd.dominio_dato.id_estado_vigencia bigint NOT NULL
       DEFAULT 1 (df_dominio_dato_estado_vigencia)
       FK fk_dominio_estado_vigencia -> gdd.cat_estado_vigencia_dominio(id).

   Pasos:
     0. Verificaciones (solo lectura).
     1. Respaldo: gdd.dominio_dato_bkp_20261111 (tabla completa).
     2. Cambios en UNA transaccion, con verificacion antes del COMMIT
        (si algo no cuadra, ROLLBACK y no queda nada a medias).
     3. Resultado (solo lectura): estado de cada dominio.

   Idempotente: cada paso verifica antes de actuar; correrlo de nuevo no
   cambia nada. Si la columna ya existe con otra definicion (NULL, otro
   tipo o sin el valor por defecto 1), el script se detiene sin cambios.
   Compatible con SQL Server 2008 o superior. Ejecutar con "detener en
   error" (o paso a paso).

   Orden de despliegue: correr en pruebas y en produccion. gdd_loader no
   necesita este cambio para seguir funcionando (no lee la columna todavia).
   Power Apps (administra gdd.dominio_dato): actualizar el origen de datos
   para que la app vea la columna nueva y agregar el campo al formulario
   (lista desde gdd.cat_estado_vigencia_dominio). Si el formulario no envia
   el campo, SQL Server asigna Activo.

   Rollback: script 20261111_rollback_dominio_estado_vigencia.sql (respalda
   los estados asignados y quita FK, valor por defecto, columna y catalogo).
   Antes, quitar el campo del formulario de Power Apps. El respaldo
   gdd.dominio_dato_bkp_20261111 no se borra.
   ========================================================================== */
SET NOCOUNT ON;
SET XACT_ABORT ON;
GO

/* ---------- PASO 0: verificaciones (solo lectura) ------------------------- */
SELECT @@SERVERNAME AS servidor, DB_NAME() AS base_datos, SUSER_SNAME() AS usuario;

-- 0.1 Estado actual: existe la columna? existe el catalogo? cuantos dominios hay?
SELECT CASE WHEN COL_LENGTH('gdd.dominio_dato', 'id_estado_vigencia') IS NULL THEN 'NO' ELSE 'SI' END AS columna_ya_existe,
       CASE WHEN OBJECT_ID('gdd.cat_estado_vigencia_dominio', 'U') IS NULL THEN 'NO' ELSE 'SI' END AS catalogo_ya_existe,
       (SELECT COUNT(*) FROM gdd.dominio_dato) AS dominios;
GO

IF OBJECT_ID('gdd.dominio_dato', 'U') IS NULL
    RAISERROR('No existe gdd.dominio_dato en esta base. No se aplica nada.', 16, 1);
GO

/* ---------- PASO 1: respaldo ---------------------------------------------- */
IF OBJECT_ID('gdd.dominio_dato_bkp_20261111', 'U') IS NULL
BEGIN
    SELECT * INTO gdd.dominio_dato_bkp_20261111 FROM gdd.dominio_dato;
    PRINT 'Respaldo gdd.dominio_dato_bkp_20261111: ' + CAST(@@ROWCOUNT AS varchar(20)) + ' filas';
END
ELSE
    PRINT 'Respaldo gdd.dominio_dato_bkp_20261111 ya existe (no se rehace).';
GO

/* ---------- PASO 2: cambios (una transaccion) ------------------------------
   Las sentencias que usan objetos nuevos van con EXEC: el lote se compila
   antes de que existan.                                                      */
BEGIN TRY
    BEGIN TRANSACTION;

    -- 2.1 Catalogo
    IF OBJECT_ID('gdd.cat_estado_vigencia_dominio', 'U') IS NULL
    BEGIN
        EXEC(N'CREATE TABLE gdd.cat_estado_vigencia_dominio (
                   id bigint NOT NULL CONSTRAINT pk_cat_estado_vigencia_dominio PRIMARY KEY,
                   nombre_estado_vigencia varchar(50) COLLATE Latin1_General_CI_AI NOT NULL
                       CONSTRAINT uq_cat_estado_vigencia_dominio_nombre UNIQUE
               );');
        PRINT 'Creado gdd.cat_estado_vigencia_dominio.';
    END

    -- 2.2 Valores (ids fijos). Si un id ya existe con otro nombre, se detiene.
    EXEC(N'
        DECLARE @v TABLE (id bigint, nombre varchar(50));
        INSERT INTO @v (id, nombre) VALUES (1, ''Activo'');
        INSERT INTO @v (id, nombre) VALUES (2, ''Inactivo'');
        INSERT INTO @v (id, nombre) VALUES (3, ''Confidencial'');
        IF EXISTS (SELECT 1 FROM gdd.cat_estado_vigencia_dominio c JOIN @v v ON v.id = c.id
                   WHERE c.nombre_estado_vigencia <> v.nombre)
            RAISERROR(''gdd.cat_estado_vigencia_dominio tiene ids 1 a 3 con otros nombres. Revisar antes de seguir.'', 16, 1);
        INSERT INTO gdd.cat_estado_vigencia_dominio (id, nombre_estado_vigencia)
        SELECT v.id, v.nombre FROM @v v
        WHERE NOT EXISTS (SELECT 1 FROM gdd.cat_estado_vigencia_dominio c WHERE c.id = v.id);
        IF @@ROWCOUNT > 0 PRINT ''Valores del catalogo insertados.'';');

    -- 2.3 Columna obligatoria con valor por defecto Activo (1): las filas existentes quedan en 1.
    IF COL_LENGTH('gdd.dominio_dato', 'id_estado_vigencia') IS NULL
    BEGIN
        EXEC(N'ALTER TABLE gdd.dominio_dato ADD id_estado_vigencia bigint NOT NULL
                   CONSTRAINT df_dominio_dato_estado_vigencia DEFAULT (1);');
        PRINT 'Agregada gdd.dominio_dato.id_estado_vigencia (Activo para los dominios existentes).';
    END

    -- 2.4 FK al catalogo
    IF OBJECT_ID('gdd.fk_dominio_estado_vigencia', 'F') IS NULL
    BEGIN
        EXEC(N'ALTER TABLE gdd.dominio_dato WITH CHECK ADD CONSTRAINT fk_dominio_estado_vigencia
                   FOREIGN KEY (id_estado_vigencia) REFERENCES gdd.cat_estado_vigencia_dominio (id);');
        PRINT 'Creada FK fk_dominio_estado_vigencia.';
    END

    -- 2.5 Verificacion antes del COMMIT
    DECLARE @ok_catalogo int, @ok_columna int, @ok_default int, @ok_fk int, @nulos int, @filas int, @filas_bkp int;
    EXEC sp_executesql N'
        SELECT @c = COUNT(*) FROM gdd.cat_estado_vigencia_dominio
        WHERE (id = 1 AND nombre_estado_vigencia = ''Activo'')
           OR (id = 2 AND nombre_estado_vigencia = ''Inactivo'')
           OR (id = 3 AND nombre_estado_vigencia = ''Confidencial'');
        SELECT @n = COUNT(*) FROM gdd.dominio_dato WHERE id_estado_vigencia IS NULL;',
        N'@c int OUTPUT, @n int OUTPUT', @c = @ok_catalogo OUTPUT, @n = @nulos OUTPUT;

    SELECT @ok_columna = COUNT(*)
    FROM sys.columns c JOIN sys.types t ON t.user_type_id = c.user_type_id
    WHERE c.object_id = OBJECT_ID('gdd.dominio_dato') AND c.name = 'id_estado_vigencia'
      AND t.name = 'bigint' AND c.is_nullable = 0;

    SELECT @ok_default = COUNT(*)
    FROM sys.default_constraints d
    JOIN sys.columns c ON c.object_id = d.parent_object_id AND c.column_id = d.parent_column_id
    WHERE d.parent_object_id = OBJECT_ID('gdd.dominio_dato') AND c.name = 'id_estado_vigencia'
      AND REPLACE(REPLACE(d.definition, '(', ''), ')', '') = '1';

    SELECT @ok_fk = COUNT(*)
    FROM sys.foreign_keys f
    JOIN sys.foreign_key_columns fc ON fc.constraint_object_id = f.object_id
    JOIN sys.columns c ON c.object_id = fc.parent_object_id AND c.column_id = fc.parent_column_id
    WHERE f.parent_object_id = OBJECT_ID('gdd.dominio_dato') AND c.name = 'id_estado_vigencia'
      AND f.referenced_object_id = OBJECT_ID('gdd.cat_estado_vigencia_dominio')
      AND f.is_not_trusted = 0 AND f.is_disabled = 0;

    SELECT @filas = COUNT(*) FROM gdd.dominio_dato;
    SELECT @filas_bkp = COUNT(*) FROM gdd.dominio_dato_bkp_20261111;

    IF @ok_catalogo <> 3 OR @ok_columna <> 1 OR @ok_default <> 1 OR @ok_fk <> 1 OR @nulos <> 0
       OR @filas < @filas_bkp
    BEGIN
        DECLARE @msg nvarchar(400);
        SET @msg = N'Verificacion fallida (catalogo=' + CAST(@ok_catalogo AS nvarchar(10))
                 + N'/3, columna=' + CAST(@ok_columna AS nvarchar(10))
                 + N'/1, default=' + CAST(@ok_default AS nvarchar(10))
                 + N'/1, fk=' + CAST(@ok_fk AS nvarchar(10))
                 + N'/1, nulos=' + CAST(@nulos AS nvarchar(10))
                 + N', filas=' + CAST(@filas AS nvarchar(10)) + N' vs respaldo ' + CAST(@filas_bkp AS nvarchar(10))
                 + N'). Si la columna ya existia con otra definicion, revisarla a mano.';
        RAISERROR(@msg, 16, 1);
    END

    COMMIT TRANSACTION;
    PRINT 'OK: estado de vigencia del dominio aplicado y verificado.';
END TRY
BEGIN CATCH
    IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;
    DECLARE @err nvarchar(2048);
    SET @err = N'Se revirtio el PASO 2 (no quedo nada a medias): ' + ERROR_MESSAGE();
    RAISERROR(@err, 16, 1);
END CATCH
GO

/* ---------- PASO 3: resultado (solo lectura) ------------------------------ */
IF COL_LENGTH('gdd.dominio_dato', 'id_estado_vigencia') IS NOT NULL
    EXEC(N'SELECT e.nombre_estado_vigencia AS estado_vigencia, COUNT(*) AS dominios
           FROM gdd.dominio_dato d
           JOIN gdd.cat_estado_vigencia_dominio e ON e.id = d.id_estado_vigencia
           GROUP BY e.id, e.nombre_estado_vigencia
           ORDER BY e.id;');
GO

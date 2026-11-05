/* ============================================================================
   Hoja MetadataRepositorio (plantilla GDD-DOMINIO 2.0.0)
   Base: DGODELIVERY   Fecha: 2026-10-05   gdd_loader: 0.7.0

   Cambio de plantilla (MAJOR 2.0.0, la 1.0.0 queda RETIRADA):
     - MetadataTecnica pierde `clase` y las columnas "_foc"
       (coleccion_foc, servidor_foc, tabla_bv_foc, nombre_campo_foc).
     - Hoja nueva MetadataRepositorio: codigo_dominio_atributo, clase,
       coleccion_foc, servidor_foc, tabla_bv_foc, nombre_campo_foc,
       fecha_aprobada. Una fila por destino de consumo; todo obligatorio.
     - gdd.atributo_fuente_consumo se alimenta de esa hoja y guarda clase y
       fecha_aprobacion propias (campos mutables, UPDATE en sitio).
     - `clase` sale de la fuente oficial: gdd.atributo_fuente_oficial.clase
       se CONSERVA con sus valores historicos, pero el loader ya no la lee ni
       la escribe, y deja de ser parte de la clave natural.

   Pasos:
     0. Verificaciones (solo lectura): columnas origen y vista previa de los
        pares de fuente oficial que solo se diferencian por clase.
     1. Respaldo: gdd.atributo_fuente_oficial_bkp_20261005 (filas afectadas
        por el paso 4) y gdd.atributo_fuente_consumo_bkp_20261005 (tabla).
     2. staging.metadata_tecnica: clase y _foc pasan a aceptar NULL (si no lo
        hacian). No se borran columnas ni datos.
     3. Crea staging.metadata_repositorio copiando tipo/ancho/intercalacion
        de las columnas equivalentes de staging.metadata_tecnica de ESTA base
        (fecha_aprobada como date, id_carga bigint). Agrega clase y
        fecha_aprobacion a gdd.atributo_fuente_consumo.
     4. Baja logica (activo = 0, fecha_baja = hoy + bitacora ELIMINAR) de las
        fuentes oficiales activas duplicadas por la clave nueva (sin clase):
        en cada grupo queda la de fecha_aprobacion mas reciente; si empatan,
        la de id mayor. Diagnostico 2026-10-05: atributo 349, 4 pares
        (Facturacion / Catalogo) -> 4 bajas. Nada se borra fisicamente.
     5. Llenado inicial de staging.metadata_repositorio desde las columnas
        _foc de staging.metadata_tecnica, solo para los dominios que aun no
        tienen filas ahi: una fila por (atributo, servidor, coleccion, tabla,
        campo), sin filas con "-"; si la misma combinacion trae clase o fecha
        distintas, se toma la fila de fecha mas reciente. Mismo id_carga que
        la fila origen (lo exige el generador de plantillas precargadas).
     6. Verificacion.

   Orden de despliegue: correr este script en la base ANTES de desplegar
   gdd_loader 0.7.0 y en la misma ventana en que se registra y publica la
   plantilla 2.0.0 (ver db/README.md). Correrlo en pruebas y en produccion.

   Idempotente: cada paso verifica antes de actuar; correrlo de nuevo no
   cambia nada. Pasos 4 y 5 son transaccionales (todo o nada).
   Ejecutar con "detener en error" (o paso a paso): un THROW detiene su
   bloque, pero algunos clientes siguen con los bloques siguientes.
   Revisar la vista previa del PASO 0.2 antes de seguir con el PASO 1.

   Rollback (antes de cargar con 0.7.0):
     -- paso 4: reactivar las fuentes dadas de baja
     UPDATE f SET f.activo = 1, f.fecha_baja = NULL
     FROM gdd.atributo_fuente_oficial f
     JOIN gdd.atributo_fuente_oficial_bkp_20261005 b ON b.id = f.id
     WHERE b.accion_20261005 = 'BAJA';
     DELETE bc FROM gdd.bitacora_carga bc
     JOIN gdd.atributo_fuente_oficial_bkp_20261005 b ON b.id = bc.id_registro
     WHERE b.accion_20261005 = 'BAJA' AND bc.tabla = 'atributo_fuente_oficial'
       AND bc.accion = 'ELIMINAR' AND bc.id_carga IS NULL
       AND CAST(bc.fecha_operacion AS date) = '2026-10-05';  -- ajustar al dia en que se corrio
     -- pasos 3 y 5 (solo si gdd_loader 0.6.x vuelve a produccion)
     DROP TABLE staging.metadata_repositorio;
     ALTER TABLE gdd.atributo_fuente_consumo DROP COLUMN clase, fecha_aprobacion;
     -- paso 2 no requiere rollback (aceptar NULL no afecta datos ni 0.6.x)
   ========================================================================== */
SET NOCOUNT ON;
SET XACT_ABORT ON;
GO

/* ---------- PASO 0: verificaciones (solo lectura) ------------------------- */
-- 0.1 Columnas origen en staging.metadata_tecnica (deben aparecer las 7)
SELECT c.name AS columna, t.name AS tipo, c.max_length, c.collation_name, c.is_nullable
FROM sys.columns c
JOIN sys.types t ON t.user_type_id = c.user_type_id
WHERE c.object_id = OBJECT_ID('staging.metadata_tecnica')
  AND c.name IN ('codigo_dominio_atributo', 'clase', 'coleccion_foc', 'servidor_foc',
                 'tabla_bv_foc', 'nombre_campo_foc', 'id_carga')
ORDER BY c.column_id;

-- 0.2 Vista previa del paso 4: grupos duplicados sin clase y que fila queda
;WITH g AS (
    SELECT f.id, f.id_atributo, f.clase, f.nombre_campo, f.id_bdd, f.es_fuente_primaria,
           f.nombre_tabla, f.fecha_aprobacion, f.version,
           COUNT(*) OVER (PARTITION BY f.id_atributo, f.nombre_campo, f.id_bdd,
                                       f.es_fuente_primaria, ISNULL(f.nombre_tabla, '')) AS filas,
           ROW_NUMBER() OVER (PARTITION BY f.id_atributo, f.nombre_campo, f.id_bdd,
                                           f.es_fuente_primaria, ISNULL(f.nombre_tabla, '')
                              ORDER BY f.fecha_aprobacion DESC, f.id DESC) AS rn
    FROM gdd.atributo_fuente_oficial f
    WHERE f.activo = 1
)
SELECT a.codigo_dominio, a.codigo_atributo, g.id, g.clase, g.nombre_campo, g.id_bdd,
       g.nombre_tabla, g.fecha_aprobacion, g.version,
       CASE WHEN g.rn = 1 THEN 'QUEDA' ELSE 'BAJA' END AS accion
FROM g
JOIN gdd.atributo a ON a.id = g.id_atributo
WHERE g.filas > 1
ORDER BY a.codigo_dominio, a.codigo_atributo, g.nombre_campo, g.id_bdd, g.rn;
GO

IF COL_LENGTH('staging.metadata_tecnica', 'clase') IS NULL
   OR COL_LENGTH('staging.metadata_tecnica', 'coleccion_foc') IS NULL
   OR COL_LENGTH('staging.metadata_tecnica', 'servidor_foc') IS NULL
   OR COL_LENGTH('staging.metadata_tecnica', 'tabla_bv_foc') IS NULL
   OR COL_LENGTH('staging.metadata_tecnica', 'nombre_campo_foc') IS NULL
   OR COL_LENGTH('staging.metadata_tecnica', 'codigo_dominio_atributo') IS NULL
    THROW 50001, 'staging.metadata_tecnica no tiene las columnas origen esperadas (ver PASO 0.1). No se aplica nada.', 1;
GO

/* ---------- PASO 1: respaldo ---------------------------------------------- */
IF OBJECT_ID('gdd.atributo_fuente_oficial_bkp_20261005') IS NULL
BEGIN
    ;WITH g AS (
        SELECT f.*,
               COUNT(*) OVER (PARTITION BY f.id_atributo, f.nombre_campo, f.id_bdd,
                                           f.es_fuente_primaria, ISNULL(f.nombre_tabla, '')) AS filas_grupo,
               ROW_NUMBER() OVER (PARTITION BY f.id_atributo, f.nombre_campo, f.id_bdd,
                                               f.es_fuente_primaria, ISNULL(f.nombre_tabla, '')
                                  ORDER BY f.fecha_aprobacion DESC, f.id DESC) AS rn_grupo
        FROM gdd.atributo_fuente_oficial f
        WHERE f.activo = 1
    )
    SELECT g.*, CASE WHEN g.rn_grupo = 1 THEN 'QUEDA' ELSE 'BAJA' END AS accion_20261005
    INTO gdd.atributo_fuente_oficial_bkp_20261005
    FROM g
    WHERE g.filas_grupo > 1;

    PRINT CONCAT('Respaldo fuentes oficiales duplicadas: ', @@ROWCOUNT, ' filas');
END
ELSE
    PRINT 'Respaldo gdd.atributo_fuente_oficial_bkp_20261005 ya existe (no se rehace).';

IF OBJECT_ID('gdd.atributo_fuente_consumo_bkp_20261005') IS NULL
BEGIN
    SELECT * INTO gdd.atributo_fuente_consumo_bkp_20261005 FROM gdd.atributo_fuente_consumo;
    PRINT CONCAT('Respaldo atributo_fuente_consumo: ', @@ROWCOUNT, ' filas');
END
ELSE
    PRINT 'Respaldo gdd.atributo_fuente_consumo_bkp_20261005 ya existe (no se rehace).';
GO

/* ---------- PASO 2: staging.metadata_tecnica acepta NULL en clase/_foc ----
   gdd_loader 0.7.0 ya no escribe estas columnas (la hoja 2.0.0 no las trae):
   si alguna era NOT NULL, la carga a staging fallaria. Conserva tipo, ancho
   e intercalacion.                                                          */
DECLARE @sql nvarchar(max) = N'';
SELECT @sql += N'ALTER TABLE staging.metadata_tecnica ALTER COLUMN ' + QUOTENAME(c.name) + N' '
             + t.name
             + CASE WHEN t.name IN ('varchar', 'char', 'varbinary', 'binary')
                    THEN N'(' + CASE WHEN c.max_length = -1 THEN N'max' ELSE CAST(c.max_length AS nvarchar(10)) END + N')'
                    WHEN t.name IN ('nvarchar', 'nchar')
                    THEN N'(' + CASE WHEN c.max_length = -1 THEN N'max' ELSE CAST(c.max_length / 2 AS nvarchar(10)) END + N')'
                    ELSE N'' END
             + CASE WHEN c.collation_name IS NOT NULL THEN N' COLLATE ' + c.collation_name ELSE N'' END
             + N' NULL;' + CHAR(10)
FROM sys.columns c
JOIN sys.types t ON t.user_type_id = c.user_type_id
WHERE c.object_id = OBJECT_ID('staging.metadata_tecnica')
  AND c.name IN ('clase', 'coleccion_foc', 'servidor_foc', 'tabla_bv_foc', 'nombre_campo_foc')
  AND c.is_nullable = 0;

IF @sql = N''
    PRINT 'staging.metadata_tecnica: clase y _foc ya aceptan NULL.';
ELSE
BEGIN
    PRINT @sql;
    EXEC sys.sp_executesql @sql;
END
GO

/* ---------- PASO 3: estructuras nuevas ------------------------------------ */
-- 3.1 staging.metadata_repositorio (columnas de texto copiadas de metadata_tecnica)
IF OBJECT_ID('staging.metadata_repositorio', 'U') IS NULL
BEGIN
    DECLARE @cols nvarchar(max);
    SELECT @cols = STRING_AGG(CAST(
                    N'    ' + QUOTENAME(c.name) + N' ' + t.name
                  + CASE WHEN t.name IN ('varchar', 'char')
                         THEN N'(' + CASE WHEN c.max_length = -1 THEN N'max' ELSE CAST(c.max_length AS nvarchar(10)) END + N')'
                         WHEN t.name IN ('nvarchar', 'nchar')
                         THEN N'(' + CASE WHEN c.max_length = -1 THEN N'max' ELSE CAST(c.max_length / 2 AS nvarchar(10)) END + N')'
                         ELSE N'' END
                  + CASE WHEN c.collation_name IS NOT NULL THEN N' COLLATE ' + c.collation_name ELSE N'' END
                  + N' NULL,' AS nvarchar(max)), CHAR(10)) WITHIN GROUP (ORDER BY o.orden)
    FROM (VALUES (1, 'codigo_dominio_atributo'), (2, 'clase'), (3, 'coleccion_foc'),
                 (4, 'servidor_foc'), (5, 'tabla_bv_foc'), (6, 'nombre_campo_foc')) AS o(orden, nombre)
    JOIN sys.columns c ON c.object_id = OBJECT_ID('staging.metadata_tecnica') AND c.name = o.nombre
    JOIN sys.types t ON t.user_type_id = c.user_type_id;

    IF (SELECT COUNT(*) FROM sys.columns
        WHERE object_id = OBJECT_ID('staging.metadata_tecnica')
          AND name IN ('codigo_dominio_atributo', 'clase', 'coleccion_foc', 'servidor_foc',
                       'tabla_bv_foc', 'nombre_campo_foc')) <> 6
        THROW 50003, 'staging.metadata_tecnica no tiene las 6 columnas origen: no se crea staging.metadata_repositorio.', 1;

    DECLARE @ddl nvarchar(max) =
        N'CREATE TABLE staging.metadata_repositorio (' + CHAR(10)
        + @cols + CHAR(10)
        + N'    fecha_aprobada date NULL,' + CHAR(10)
        + N'    id_carga bigint NULL' + CHAR(10)
        + N');';
    PRINT @ddl;
    EXEC sys.sp_executesql @ddl;
END
ELSE
    PRINT 'staging.metadata_repositorio ya existe.';
GO

-- 3.2 gdd.atributo_fuente_consumo: clase y fecha_aprobacion (mutables)
IF COL_LENGTH('gdd.atributo_fuente_consumo', 'clase') IS NULL
    ALTER TABLE gdd.atributo_fuente_consumo
        ADD clase varchar(255) COLLATE Latin1_General_CI_AI NULL;
IF COL_LENGTH('gdd.atributo_fuente_consumo', 'fecha_aprobacion') IS NULL
    ALTER TABLE gdd.atributo_fuente_consumo
        ADD fecha_aprobacion date NULL;
GO

/* ---------- PASO 4: baja de fuentes oficiales duplicadas sin clase --------- */
BEGIN TRY
    BEGIN TRANSACTION;

    DECLARE @hoy date = CAST(SYSDATETIME() AS date);
    DECLARE @bajas TABLE (id bigint PRIMARY KEY);

    ;WITH g AS (
        SELECT f.id,
               ROW_NUMBER() OVER (PARTITION BY f.id_atributo, f.nombre_campo, f.id_bdd,
                                               f.es_fuente_primaria, ISNULL(f.nombre_tabla, '')
                                  ORDER BY f.fecha_aprobacion DESC, f.id DESC) AS rn
        FROM gdd.atributo_fuente_oficial f
        WHERE f.activo = 1
    )
    INSERT INTO @bajas (id) SELECT id FROM g WHERE rn > 1;

    -- Solo se dan de baja filas que quedaron respaldadas en el PASO 1.
    IF EXISTS (SELECT 1 FROM @bajas b
               WHERE NOT EXISTS (SELECT 1 FROM gdd.atributo_fuente_oficial_bkp_20261005 k WHERE k.id = b.id))
        THROW 50002, 'Hay duplicados que no estan en gdd.atributo_fuente_oficial_bkp_20261005 (respaldo de otra corrida). Revisar antes de continuar.', 1;

    INSERT INTO gdd.bitacora_carga
        (tabla, id_registro, accion, codigo_dominio, codigo_dominio_atributo, version_resultante)
    SELECT 'atributo_fuente_oficial', f.id, 'ELIMINAR', a.codigo_dominio,
           a.codigo_dominio + '-' + CAST(a.codigo_atributo AS varchar(20)), NULL
    FROM gdd.atributo_fuente_oficial f
    JOIN @bajas b ON b.id = f.id
    JOIN gdd.atributo a ON a.id = f.id_atributo;

    UPDATE f SET f.activo = 0, f.fecha_baja = @hoy
    FROM gdd.atributo_fuente_oficial f
    JOIN @bajas b ON b.id = f.id;

    PRINT CONCAT('Fuentes oficiales duplicadas dadas de baja: ', @@ROWCOUNT);

    COMMIT TRANSACTION;
END TRY
BEGIN CATCH
    IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
GO

/* ---------- PASO 5: llenado inicial de staging.metadata_repositorio ------- */
BEGIN TRY
    BEGIN TRANSACTION;

    ;WITH origen AS (
        SELECT LTRIM(RTRIM(m.codigo_dominio_atributo)) AS codigo_dominio_atributo,
               NULLIF(NULLIF(LTRIM(RTRIM(m.clase)), ''), '-') AS clase,
               LTRIM(RTRIM(m.coleccion_foc))    AS coleccion_foc,
               LTRIM(RTRIM(m.servidor_foc))     AS servidor_foc,
               LTRIM(RTRIM(m.tabla_bv_foc))     AS tabla_bv_foc,
               LTRIM(RTRIM(m.nombre_campo_foc)) AS nombre_campo_foc,
               -- metadata_tecnica.fecha_aprobada es texto: los 3 formatos que
               -- acepta el loader (gdd_mapping._FORMATOS_FECHA_METADATA_TECNICA)
               CASE
                   WHEN LTRIM(RTRIM(m.fecha_aprobada)) LIKE '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]%'
                       THEN TRY_CONVERT(date, LEFT(LTRIM(m.fecha_aprobada), 10), 23)
                   WHEN LTRIM(RTRIM(m.fecha_aprobada)) LIKE '[0-9][0-9]/[0-9][0-9]/[0-9][0-9][0-9][0-9]'
                       THEN TRY_CONVERT(date, LTRIM(RTRIM(m.fecha_aprobada)), 103)
                   WHEN LTRIM(RTRIM(m.fecha_aprobada)) LIKE '[0-9][0-9]-[0-9][0-9]-[0-9][0-9][0-9][0-9]'
                       THEN TRY_CONVERT(date, LTRIM(RTRIM(m.fecha_aprobada)), 105)
               END AS fecha_aprobada,
               m.id_carga
        FROM staging.metadata_tecnica m
        WHERE NULLIF(NULLIF(LTRIM(RTRIM(m.coleccion_foc)), ''), '-')    IS NOT NULL
          AND NULLIF(NULLIF(LTRIM(RTRIM(m.servidor_foc)), ''), '-')     IS NOT NULL
          AND NULLIF(NULLIF(LTRIM(RTRIM(m.tabla_bv_foc)), ''), '-')     IS NOT NULL
          AND NULLIF(NULLIF(LTRIM(RTRIM(m.nombre_campo_foc)), ''), '-') IS NOT NULL
    ),
    con_dominio AS (
        SELECT o.*,
               LEFT(o.codigo_dominio_atributo,
                    LEN(o.codigo_dominio_atributo) - CHARINDEX('-', REVERSE(o.codigo_dominio_atributo))) AS codigo_dominio,
               ROW_NUMBER() OVER (PARTITION BY o.codigo_dominio_atributo, o.servidor_foc, o.coleccion_foc,
                                               o.tabla_bv_foc, o.nombre_campo_foc
                                  ORDER BY o.fecha_aprobada DESC, o.clase DESC) AS rn
        FROM origen o
        WHERE CHARINDEX('-', o.codigo_dominio_atributo) > 0
    )
    INSERT INTO staging.metadata_repositorio
        (codigo_dominio_atributo, clase, coleccion_foc, servidor_foc, tabla_bv_foc,
         nombre_campo_foc, fecha_aprobada, id_carga)
    SELECT d.codigo_dominio_atributo, d.clase, d.coleccion_foc, d.servidor_foc, d.tabla_bv_foc,
           d.nombre_campo_foc, d.fecha_aprobada, d.id_carga
    FROM con_dominio d
    WHERE d.rn = 1
      AND NOT EXISTS (SELECT 1 FROM staging.metadata_repositorio r
                      WHERE r.codigo_dominio_atributo LIKE d.codigo_dominio + '-%');

    PRINT CONCAT('staging.metadata_repositorio: filas del llenado inicial = ', @@ROWCOUNT);

    COMMIT TRANSACTION;
END TRY
BEGIN CATCH
    IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
GO

/* ---------- PASO 6: verificacion ------------------------------------------ */
-- 6.1 Estructuras (todas OK)
SELECT objeto, CASE WHEN existe = 1 THEN 'OK' ELSE 'FALTA' END AS estado
FROM (VALUES
    ('staging.metadata_repositorio', CASE WHEN OBJECT_ID('staging.metadata_repositorio', 'U') IS NOT NULL THEN 1 ELSE 0 END),
    ('gdd.atributo_fuente_consumo.clase', CASE WHEN COL_LENGTH('gdd.atributo_fuente_consumo', 'clase') IS NOT NULL THEN 1 ELSE 0 END),
    ('gdd.atributo_fuente_consumo.fecha_aprobacion', CASE WHEN COL_LENGTH('gdd.atributo_fuente_consumo', 'fecha_aprobacion') IS NOT NULL THEN 1 ELSE 0 END),
    ('gdd.atributo_fuente_oficial_bkp_20261005', CASE WHEN OBJECT_ID('gdd.atributo_fuente_oficial_bkp_20261005') IS NOT NULL THEN 1 ELSE 0 END),
    ('gdd.atributo_fuente_consumo_bkp_20261005', CASE WHEN OBJECT_ID('gdd.atributo_fuente_consumo_bkp_20261005') IS NOT NULL THEN 1 ELSE 0 END)
) AS v(objeto, existe);

-- 6.2 Columnas de staging.metadata_repositorio
SELECT COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH, COLLATION_NAME, IS_NULLABLE
FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_SCHEMA = 'staging' AND TABLE_NAME = 'metadata_repositorio'
ORDER BY ORDINAL_POSITION;

-- 6.3 Duplicados de fuente oficial por la clave nueva (debe dar 0 filas)
SELECT id_atributo, nombre_campo, id_bdd, es_fuente_primaria, ISNULL(nombre_tabla, '') AS nombre_tabla,
       COUNT(*) AS filas
FROM gdd.atributo_fuente_oficial
WHERE activo = 1
GROUP BY id_atributo, nombre_campo, id_bdd, es_fuente_primaria, ISNULL(nombre_tabla, '')
HAVING COUNT(*) > 1;

-- 6.4 Llenado inicial por dominio. clase/fecha vacias: la plantilla precargada
--     las saldra vacias y los Office Scripts / el loader exigiran completarlas.
SELECT LEFT(codigo_dominio_atributo,
            LEN(codigo_dominio_atributo) - CHARINDEX('-', REVERSE(codigo_dominio_atributo))) AS codigo_dominio,
       COUNT(*) AS destinos,
       SUM(CASE WHEN clase IS NULL THEN 1 ELSE 0 END) AS sin_clase,
       SUM(CASE WHEN fecha_aprobada IS NULL THEN 1 ELSE 0 END) AS sin_fecha,
       COUNT(DISTINCT id_carga) AS cargas_origen
FROM staging.metadata_repositorio
GROUP BY LEFT(codigo_dominio_atributo,
              LEN(codigo_dominio_atributo) - CHARINDEX('-', REVERSE(codigo_dominio_atributo)))
ORDER BY 1;

-- 6.5 Destinos de consumo activos en gdd sin fila equivalente en el llenado
--     inicial (por dominio). Si no da 0, la primera plantilla 2.0.0 de ese
--     dominio saldria sin esos destinos y su carga los daria de baja.
SELECT a.codigo_dominio, COUNT(*) AS consumos_gdd_sin_staging
FROM gdd.atributo_fuente_consumo c
JOIN gdd.atributo a                ON a.id = c.id_atributo
JOIN gdd.base_datos_fuente bd      ON bd.id = c.id_bdd
JOIN gdd.base_datos_fuente_tabla t ON t.id = c.id_tabla
JOIN gdd.servidor_fuente sv        ON sv.id = bd.id_servidor
WHERE c.activo = 1 AND a.activo = 1
  AND NOT EXISTS (
      SELECT 1 FROM staging.metadata_repositorio r
      WHERE r.codigo_dominio_atributo = a.codigo_dominio + '-' + CAST(a.codigo_atributo AS varchar(20))
        AND r.servidor_foc = sv.nombre_servidor
        AND r.coleccion_foc = bd.nombre_bdd
        AND r.tabla_bv_foc = t.nombre_tabla
        AND r.nombre_campo_foc = c.nombre_campo)
GROUP BY a.codigo_dominio;

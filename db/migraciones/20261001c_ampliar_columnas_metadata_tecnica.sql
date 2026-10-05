/* ============================================================================
   Ampliar columnas de texto que rechazan valores reales del Excel
   Base: DGODELIVERY   Fecha: 2026-10-01

   Problema (cargas 12 y 37, CLI.xlsx): "String data, right truncation:
   length 144 buffer 100" al insertar en staging.metadata_tecnica. pyodbc
   (fast_executemany) mide en BYTES UTF-16: buffer 100 = varchar(50) y
   length 144 = 72 caracteres -> longitud_campo_fuente_oficial (el Excel trae
   hasta 72, ej. "COUNTRY_CODE: 3, PROVINCE:6 , CANTON: 4, ADD1: 40, ...").
   Revision del archivo real (2026-10-01) contra los anchos de staging:
     longitud_campo_fuente_oficial  50 -> trae 72
     nombre_campo_fuente_oficial   255 -> trae 279 (y gdd.nombre_campo es 255)
     lista_valores_validos         500 -> trae 776

   Cambio (solo AMPLIA, nunca reduce; no toca datos):
     staging.metadata_tecnica.longitud_campo_fuente_oficial  50 -> 255  (= gdd.longitud_campo)
     staging.metadata_tecnica.nombre_campo_fuente_oficial   255 -> 1000
     staging.metadata_tecnica.lista_valores_validos         500 -> max  (= gdd.lista_valores_validos)
     staging.metadata_tecnica.clase                         100 -> 255  (preventivo)
     staging.metadata_tecnica.coleccion_foc                 100 -> 255  (preventivo, = gdd.base_datos_fuente.nombre_bdd)
     gdd.atributo_fuente_oficial.nombre_campo               255 -> 1000 (si no, falla el merge)
     gdd.atributo_fuente_oficial.clase                      100 -> 255  (preventivo)

   Conserva la intercalacion (COLLATE) y el NULL/NOT NULL actuales de cada
   columna (sin esto, ALTER COLUMN aplicaria la intercalacion por defecto de
   la base). Idempotente: si la columna ya tiene el ancho pedido o mas, no
   hace nada. Si un indice/estadistica depende de la columna, el ALTER falla
   y no cambia nada: el PASO 0 lo lista antes.

   No requiere cambios en gdd_loader. Rollback: no es necesario (ampliar no
   afecta datos ni codigo); reducir de nuevo solo si ningun valor excede.
   ========================================================================== */
SET NOCOUNT ON;
GO

/* ---------- PASO 0: dependencias (debe devolver 0 filas) ------------------ */
SELECT OBJECT_SCHEMA_NAME(ic.object_id) + '.' + OBJECT_NAME(ic.object_id) AS tabla,
       c.name AS columna, i.name AS indice
FROM sys.index_columns ic
JOIN sys.indexes i ON i.object_id = ic.object_id AND i.index_id = ic.index_id
JOIN sys.columns c ON c.object_id = ic.object_id AND c.column_id = ic.column_id
WHERE (ic.object_id = OBJECT_ID('staging.metadata_tecnica')
       AND c.name IN ('clase', 'coleccion_foc', 'lista_valores_validos',
                      'longitud_campo_fuente_oficial', 'nombre_campo_fuente_oficial'))
   OR (ic.object_id = OBJECT_ID('gdd.atributo_fuente_oficial') AND c.name IN ('clase', 'nombre_campo'));
GO

/* ---------- PASO 1: ampliar ----------------------------------------------- */
DECLARE @cambios TABLE (tabla sysname, columna sysname, largo int);  -- -1 = max
INSERT INTO @cambios VALUES
    ('staging.metadata_tecnica',     'longitud_campo_fuente_oficial', 255),
    ('staging.metadata_tecnica',     'nombre_campo_fuente_oficial',   1000),
    ('staging.metadata_tecnica',     'lista_valores_validos',         -1),
    ('staging.metadata_tecnica',     'clase',                         255),
    ('staging.metadata_tecnica',     'coleccion_foc',                 255),
    ('gdd.atributo_fuente_oficial',  'nombre_campo',                  1000),
    ('gdd.atributo_fuente_oficial',  'clase',                         255);

DECLARE @sql nvarchar(max) = N'';
SELECT @sql += N'ALTER TABLE ' + QUOTENAME(OBJECT_SCHEMA_NAME(c.object_id)) + N'.' + QUOTENAME(OBJECT_NAME(c.object_id))
             + N' ALTER COLUMN ' + QUOTENAME(c.name) + N' varchar('
             + CASE WHEN x.largo = -1 THEN N'max' ELSE CAST(x.largo AS nvarchar(10)) END + N')'
             + N' COLLATE ' + c.collation_name
             + CASE WHEN c.is_nullable = 1 THEN N' NULL' ELSE N' NOT NULL' END + N';' + CHAR(10)
FROM @cambios x
JOIN sys.columns c ON c.object_id = OBJECT_ID(x.tabla) AND c.name = x.columna
JOIN sys.types t ON t.user_type_id = c.user_type_id AND t.name = 'varchar'
WHERE c.max_length <> -1                                   -- ya es max: nada que hacer
  AND (x.largo = -1 OR c.max_length < x.largo);            -- solo amplia

IF @sql = N''
    PRINT 'Nada que ampliar (ya aplicado).';
ELSE
BEGIN
    PRINT @sql;
    EXEC sys.sp_executesql @sql;
END
GO

/* ---------- PASO 2: verificacion ------------------------------------------ */
SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, CHARACTER_MAXIMUM_LENGTH, COLLATION_NAME, IS_NULLABLE
FROM INFORMATION_SCHEMA.COLUMNS
WHERE (TABLE_SCHEMA = 'staging' AND TABLE_NAME = 'metadata_tecnica'
       AND COLUMN_NAME IN ('clase', 'coleccion_foc', 'lista_valores_validos',
                           'longitud_campo_fuente_oficial', 'nombre_campo_fuente_oficial'))
   OR (TABLE_SCHEMA = 'gdd' AND TABLE_NAME = 'atributo_fuente_oficial'
       AND COLUMN_NAME IN ('clase', 'nombre_campo'));

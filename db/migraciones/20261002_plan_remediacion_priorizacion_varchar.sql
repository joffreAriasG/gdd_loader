/* ============================================================================
   gdd.plan_remediacion.priorizacion: int -> varchar(255)
   Base: DGODELIVERY   Fecha: 2026-10-02   gdd_loader: 0.6.2

   Definicion (2026-10-02): priorizacion es TEXTO en la plantilla (staging ya
   es varchar). En gdd era int, y el merge fallaba con error de conversion
   cuando el Excel traia un valor no numerico. Desde 0.6.2 el loader la
   trata como texto libre (sin catalogo) y la envia como texto.

   Cambio: convierte la columna en el mismo lugar. Los valores enteros que ya
   existen pasan a texto ("1" -> '1'); no se pierde ningun dato. Conserva
   NULL. Respaldo previo de (id, priorizacion).

   Idempotente: si la columna ya es varchar, no hace nada.
   Correr ANTES de desplegar gdd_loader 0.6.2 (con 0.6.1 + este SQL la carga
   sigue funcionando para valores numericos: SQL Server convierte el int al
   insertar).

   Rollback (solo si TODOS los valores siguen siendo numericos):
     ALTER TABLE gdd.plan_remediacion ALTER COLUMN priorizacion int NULL;
   ========================================================================== */
SET NOCOUNT ON;
SET XACT_ABORT ON;
GO

/* ---------- PASO 0: dependencias (debe devolver 0 filas) ------------------
   Indices, CHECK o DEFAULT sobre la columna impiden el ALTER.               */
SELECT 'indice' AS tipo, i.name
FROM sys.index_columns ic
JOIN sys.indexes i ON i.object_id = ic.object_id AND i.index_id = ic.index_id
JOIN sys.columns c ON c.object_id = ic.object_id AND c.column_id = ic.column_id
WHERE ic.object_id = OBJECT_ID('gdd.plan_remediacion') AND c.name = 'priorizacion'
UNION ALL
SELECT 'check', cc.name FROM sys.check_constraints cc
WHERE cc.parent_object_id = OBJECT_ID('gdd.plan_remediacion') AND cc.definition LIKE '%priorizacion%'
UNION ALL
SELECT 'default', dc.name FROM sys.default_constraints dc
JOIN sys.columns c ON c.object_id = dc.parent_object_id AND c.column_id = dc.parent_column_id
WHERE dc.parent_object_id = OBJECT_ID('gdd.plan_remediacion') AND c.name = 'priorizacion';
GO

/* ---------- PASO 1: respaldo ---------------------------------------------- */
IF OBJECT_ID('gdd.plan_remediacion_priorizacion_bkp_20261002') IS NULL
    SELECT id, priorizacion
    INTO gdd.plan_remediacion_priorizacion_bkp_20261002
    FROM gdd.plan_remediacion;
GO

/* ---------- PASO 2: cambio de tipo ---------------------------------------- */
IF EXISTS (SELECT 1 FROM sys.columns c JOIN sys.types t ON t.user_type_id = c.user_type_id
           WHERE c.object_id = OBJECT_ID('gdd.plan_remediacion') AND c.name = 'priorizacion'
             AND t.name <> 'varchar')
    ALTER TABLE gdd.plan_remediacion
        ALTER COLUMN priorizacion varchar(255) COLLATE Latin1_General_CI_AI NULL;
GO

/* ---------- PASO 3: verificacion ------------------------------------------ */
-- 3.1 Tipo nuevo (varchar 255) y ancho en staging (debe ser <= 255)
SELECT TABLE_SCHEMA, TABLE_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH
FROM INFORMATION_SCHEMA.COLUMNS
WHERE COLUMN_NAME = 'priorizacion'
  AND ((TABLE_SCHEMA = 'gdd' AND TABLE_NAME = 'plan_remediacion')
    OR (TABLE_SCHEMA = 'staging' AND TABLE_NAME = 'plan_remediacion'));

-- 3.2 Ningun valor cambio (debe dar 0)
SELECT COUNT(*) AS diferencias
FROM gdd.plan_remediacion p
JOIN gdd.plan_remediacion_priorizacion_bkp_20261002 b ON b.id = p.id
WHERE ISNULL(CAST(b.priorizacion AS varchar(255)), '~') <> ISNULL(p.priorizacion, '~');

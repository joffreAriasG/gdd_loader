/* ============================================================================
   gdd.atributo_fuente_oficial.nombre_tabla -- tabla/archivo de origen
   Base: DGODELIVERY   Fecha: 2026-10-01   gdd_loader: 0.6.0

   Problema: la columna tabla_fuente_oficial de la hoja MetadataTecnica
   llegaba a staging.metadata_tecnica pero no tenia destino en gdd, y la
   clave natural de la fuente oficial (clase, nombre_campo, id_bdd,
   es_fuente_primaria) colapsaba en UNA sola fila las fuentes que solo
   difieren en la tabla (ej. category_id de Catalogo_compras en PROD1, 3
   tablas -> 1 fila).

   Cambio:
     PASO 1  Respaldo completo de gdd.atributo_fuente_oficial (una sola vez).
     PASO 2  Columna nueva nombre_tabla varchar(255) NULL, texto libre (sin
             catalogo). Parte de la clave natural desde gdd_loader 0.6.0.
     PASO 3  Completa nombre_tabla de las fuentes primarias ACTIVAS desde
             staging.metadata_tecnica, SOLO cuando la fila de gdd calza con
             una unica tabla en staging. Asi esas fuentes siguen "existiendo"
             para el merge (sin baja + reinsercion ni cambio de version).
             Las que hoy estan colapsadas (varias tablas para la misma
             clave anterior) quedan en NULL a proposito: en la siguiente
             carga del dominio se dan de baja y entran las N fuentes reales,
             cada una con su tabla.
     PASO 4  Verificacion.

   Idempotente (se puede correr de nuevo sin efecto). Correr ANTES de
   desplegar gdd_loader 0.6.0: el codigo nuevo escribe y lee nombre_tabla y
   falla si la columna no existe.

   Rollback (solo si se vuelve a gdd_loader 0.5.x):
     ALTER TABLE gdd.atributo_fuente_oficial DROP COLUMN nombre_tabla;
   Si una carga con 0.6.0 ya inserto fuentes nuevas, restaurar desde el
   respaldo en vez de solo borrar la columna:
     -- revisar diferencias antes de restaurar
     SELECT COUNT(*) FROM gdd.atributo_fuente_oficial;
     SELECT COUNT(*) FROM gdd.atributo_fuente_oficial_bkp_20261001;
   ========================================================================== */
SET NOCOUNT ON;
GO

/* ---------- PASO 1: respaldo ---------------------------------------------- */
IF OBJECT_ID('gdd.atributo_fuente_oficial_bkp_20261001') IS NULL
    SELECT * INTO gdd.atributo_fuente_oficial_bkp_20261001
    FROM gdd.atributo_fuente_oficial;
GO

/* ---------- PASO 2: columna nueva ----------------------------------------- */
IF COL_LENGTH('gdd.atributo_fuente_oficial', 'nombre_tabla') IS NULL
    ALTER TABLE gdd.atributo_fuente_oficial
        ADD nombre_tabla varchar(255) COLLATE Latin1_General_CI_AI NULL;
GO

/* ---------- PASO 3: completar desde staging -------------------------------
   Normaliza staging igual que gdd_loader (gdd_mapping._texto_o_none):
   texto recortado, '' y '-' = vacio. COLLATE explicito: las tablas staging.*
   pueden tener la intercalacion por defecto de la base y gdd.* usa
   Latin1_General_CI_AI; sin esto el JOIN puede fallar por conflicto de
   intercalacion.                                                            */
WITH stg AS (
    SELECT
        LTRIM(RTRIM(s.codigo_dominio_atributo))                       COLLATE Latin1_General_CI_AI AS codigo_dominio_atributo,
        LTRIM(RTRIM(s.servidor_fuente_oficial))                       COLLATE Latin1_General_CI_AI AS servidor,
        LTRIM(RTRIM(s.base_datos_fuente_oficial))                     COLLATE Latin1_General_CI_AI AS base_datos,
        CASE WHEN LTRIM(RTRIM(ISNULL(s.clase, ''))) IN ('', '-') THEN ''
             ELSE LTRIM(RTRIM(s.clase)) END                           COLLATE Latin1_General_CI_AI AS clase,
        LTRIM(RTRIM(s.nombre_campo_fuente_oficial))                   COLLATE Latin1_General_CI_AI AS nombre_campo,
        CASE WHEN LTRIM(RTRIM(ISNULL(s.tabla_fuente_oficial, ''))) IN ('', '-') THEN NULL
             ELSE LTRIM(RTRIM(s.tabla_fuente_oficial)) END            COLLATE Latin1_General_CI_AI AS tabla
    FROM staging.metadata_tecnica s
),
calce AS (
    SELECT f.id, MIN(stg.tabla) AS tabla, COUNT(DISTINCT stg.tabla) AS tablas
    FROM gdd.atributo_fuente_oficial f
    JOIN gdd.atributo a           ON a.id = f.id_atributo
    JOIN gdd.base_datos_fuente bd ON bd.id = f.id_bdd
    JOIN gdd.servidor_fuente sv   ON sv.id = bd.id_servidor
    JOIN stg ON stg.codigo_dominio_atributo = a.codigo_dominio + '-' + CAST(a.codigo_atributo AS varchar(20))
            AND stg.servidor     = sv.nombre_servidor
            AND stg.base_datos   = bd.nombre_bdd
            AND stg.clase        = ISNULL(f.clase, '')
            AND stg.nombre_campo = f.nombre_campo
    WHERE f.activo = 1 AND f.es_fuente_primaria = 1 AND a.activo = 1
      AND f.nombre_tabla IS NULL AND stg.tabla IS NOT NULL
    GROUP BY f.id
)
UPDATE f
SET f.nombre_tabla = c.tabla
FROM gdd.atributo_fuente_oficial f
JOIN calce c ON c.id = f.id
WHERE c.tablas = 1;

PRINT CONCAT('Fuentes completadas con nombre_tabla: ', @@ROWCOUNT);
GO

/* ---------- PASO 4: verificacion ------------------------------------------ */
-- 4.1 La columna existe (1 fila)
SELECT c.name, t.name AS tipo, c.max_length, c.is_nullable
FROM sys.columns c JOIN sys.types t ON t.user_type_id = c.user_type_id
WHERE c.object_id = OBJECT_ID('gdd.atributo_fuente_oficial') AND c.name = 'nombre_tabla';

-- 4.2 Fuentes primarias activas: con tabla vs sin tabla.
--     "sin_tabla" = colapsadas hoy (se corrigen en la proxima carga del
--     dominio) o sin fila en staging.
SELECT a.codigo_dominio,
       SUM(CASE WHEN f.nombre_tabla IS NOT NULL THEN 1 ELSE 0 END) AS con_tabla,
       SUM(CASE WHEN f.nombre_tabla IS NULL THEN 1 ELSE 0 END)     AS sin_tabla
FROM gdd.atributo_fuente_oficial f
JOIN gdd.atributo a ON a.id = f.id_atributo
WHERE f.activo = 1 AND f.es_fuente_primaria = 1
GROUP BY a.codigo_dominio
ORDER BY a.codigo_dominio;

-- 4.3 Respaldo (mismo total que la tabla antes del cambio)
SELECT COUNT(*) AS filas_respaldo FROM gdd.atributo_fuente_oficial_bkp_20261001;

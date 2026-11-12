/* ============================================================================
   Religar los planes de remediacion a la version vigente de su atributo
   Base: DGODELIVERY   Fecha: 2026-11-12   gdd_loader: 0.14.1 (BL-04 / BL-05)

   Problema (BL-04, confirmado en codigo y en la base local): el merge de
   planes identifica cada plan por (codigo_dominio_atributo, id_problema) y
   no cambiaba id_atributo. Cuando un atributo se REEMPLAZA (fecha_aprobada
   mas reciente -> baja logica de la version anterior + version nueva con id
   nuevo), sus planes quedaban ligados a la version dada de baja. En la base
   local: los 25 planes de ATH apuntan a la version 1 (inactiva desde el
   2026-09-22); la vigente es la 2.

   gdd_loader 0.14.1 corrige la causa (REEMPLAZAR mueve los planes y el merge
   de planes compara id_atributo). Este script corrige los planes que YA
   quedaron ligados a una version vieja.

   Que hace:
     - Religa cada plan ACTIVO ligado a una version INACTIVA de su atributo
       a la unica version ACTIVA del mismo atributo (mismo codigo_dominio y
       codigo_atributo), si el codigo_dominio_atributo del plan coincide con
       esa version.
     - NO toca (solo los informa, decision del usuario 2026-11-12):
         a) planes cuyo atributo no tiene ninguna version activa (atributo
            dado de baja; con 0.14.1 la baja ya va en cascada a los planes);
         b) planes cuyo atributo tiene mas de una version activa (ambiguo);
         c) planes cuyo codigo_dominio_atributo no coincide con el atributo.
     - Solo cambia id_atributo. Ningun otro campo, ningun INSERT ni DELETE.
       Los planes inactivos (historial) se quedan en su version.

   Pasos:
     0. Verificaciones y resumen por dominio (solo lectura).
     1. Respaldo: gdd.plan_remediacion_bkp_20261112 = copia completa de
        gdd.plan_remediacion + columna id_atributo_religado (NULL; el PASO 2
        anota ahi el id nuevo de cada plan religado: el rollback la usa).
     2. Religar en UNA transaccion, con verificacion antes del COMMIT (si
        algo no cuadra, ROLLBACK y no queda nada a medias).
     3. Resultado (solo lectura).

   Idempotente: si no hay planes por religar, el PASO 2 no cambia nada; el
   respaldo no se rehace si ya existe. Si el respaldo existe de una corrida
   anterior y no cubre algun plan por religar, el script se detiene sin
   cambios (renombrar el respaldo con sp_rename y volver a correr).
   Compatible con SQL Server 2008 o superior. Ejecutar con "detener en
   error" (sqlcmd -b) o paso a paso.

   Opciones SET: gdd.plan_remediacion tiene un indice filtrado
   (uq_plan_remediacion_clave_natural), y un UPDATE sobre esa tabla exige
   QUOTED_IDENTIFIER ON (sqlcmd lo deja en OFF por defecto: error 1934). Por
   eso el script fija esas opciones al inicio.

   Orden de despliegue: este SQL y despues el codigo 0.14.1. Son
   independientes: el codigo no necesita este script, y este script no
   necesita el codigo (pero sin el codigo 0.14.1, el siguiente REEMPLAZAR
   vuelve a dejar planes en la version vieja).

   Rollback: 20261112_rollback_religar_planes_version_vigente.sql (devuelve
   id_atributo al valor del respaldo SOLO en los planes que este script
   religo y que no cambiaron despues). El respaldo no se borra.
   ========================================================================== */
SET NOCOUNT ON;
SET XACT_ABORT ON;
-- Requeridas por el indice filtrado de gdd.plan_remediacion (ver encabezado).
SET QUOTED_IDENTIFIER ON;
SET ANSI_NULLS ON;
SET ANSI_PADDING ON;
SET ANSI_WARNINGS ON;
SET ARITHABORT ON;
SET CONCAT_NULL_YIELDS_NULL ON;
SET NUMERIC_ROUNDABORT OFF;
GO

/* ---------- PASO 0: verificaciones (solo lectura) ------------------------- */
SELECT @@SERVERNAME AS servidor, DB_NAME() AS base_datos, SUSER_SNAME() AS usuario;
GO

IF OBJECT_ID('gdd.plan_remediacion', 'U') IS NULL OR OBJECT_ID('gdd.atributo', 'U') IS NULL
    RAISERROR('No existen gdd.plan_remediacion o gdd.atributo en esta base. No se aplica nada.', 16, 1);
GO

-- 0.1 Resumen por dominio de los planes activos
SELECT p.codigo_dominio,
       COUNT(*) AS planes_activos,
       SUM(CASE WHEN a.activo = 1 THEN 1 ELSE 0 END) AS ligados_a_version_vigente,
       SUM(CASE WHEN a.activo = 0 AND x.n_activas = 1
                 AND p.codigo_dominio_atributo = a.codigo_dominio + '-' + CAST(a.codigo_atributo AS varchar(20))
                THEN 1 ELSE 0 END) AS a_religar,
       SUM(CASE WHEN a.activo = 0 AND x.n_activas = 0 THEN 1 ELSE 0 END) AS sin_version_vigente,
       SUM(CASE WHEN a.activo = 0 AND x.n_activas > 1 THEN 1 ELSE 0 END) AS varias_versiones_activas,
       SUM(CASE WHEN p.codigo_dominio_atributo <> a.codigo_dominio + '-' + CAST(a.codigo_atributo AS varchar(20))
                THEN 1 ELSE 0 END) AS codigo_no_coincide
FROM gdd.plan_remediacion p
JOIN gdd.atributo a ON a.id = p.id_atributo
CROSS APPLY (SELECT COUNT(*) AS n_activas
             FROM gdd.atributo v
             WHERE v.codigo_dominio = a.codigo_dominio
               AND v.codigo_atributo = a.codigo_atributo
               AND v.activo = 1) x
WHERE p.activo = 1
GROUP BY p.codigo_dominio
ORDER BY p.codigo_dominio;

-- 0.2 Planes que este script NO toca (solo se informan)
SELECT p.id AS id_plan, p.codigo_dominio_atributo, p.id_problema,
       a.id AS id_atributo_ligado, a.version AS version_ligada, a.activo AS atributo_activo,
       a.fecha_baja AS fecha_baja_atributo, x.n_activas AS versiones_activas,
       CASE WHEN p.codigo_dominio_atributo <> a.codigo_dominio + '-' + CAST(a.codigo_atributo AS varchar(20))
                 THEN 'codigo_dominio_atributo no coincide con el atributo'
            WHEN x.n_activas = 0 THEN 'atributo sin version vigente'
            ELSE 'atributo con varias versiones activas' END AS motivo
FROM gdd.plan_remediacion p
JOIN gdd.atributo a ON a.id = p.id_atributo
CROSS APPLY (SELECT COUNT(*) AS n_activas
             FROM gdd.atributo v
             WHERE v.codigo_dominio = a.codigo_dominio
               AND v.codigo_atributo = a.codigo_atributo
               AND v.activo = 1) x
WHERE p.activo = 1
  AND (p.codigo_dominio_atributo <> a.codigo_dominio + '-' + CAST(a.codigo_atributo AS varchar(20))
       OR (a.activo = 0 AND x.n_activas <> 1))
ORDER BY p.codigo_dominio_atributo, p.id_problema;
GO

/* ---------- PASO 1: respaldo ---------------------------------------------- */
IF OBJECT_ID('gdd.plan_remediacion_bkp_20261112', 'U') IS NULL
BEGIN
    SELECT p.*, CAST(NULL AS bigint) AS id_atributo_religado
    INTO gdd.plan_remediacion_bkp_20261112
    FROM gdd.plan_remediacion p;
    PRINT 'Respaldo gdd.plan_remediacion_bkp_20261112: ' + CAST(@@ROWCOUNT AS varchar(20)) + ' filas';
END
ELSE
    PRINT 'Respaldo gdd.plan_remediacion_bkp_20261112 ya existe (no se rehace).';
GO

/* ---------- PASO 2: religar (una transaccion) ----------------------------- */
IF OBJECT_ID('gdd.plan_remediacion_bkp_20261112', 'U') IS NULL
    RAISERROR('No existe el respaldo gdd.plan_remediacion_bkp_20261112: no se religa nada.', 16, 1);
GO

BEGIN TRY
    BEGIN TRANSACTION;

    DECLARE @religar TABLE (
        id_plan bigint NOT NULL PRIMARY KEY,
        id_atributo_anterior bigint NOT NULL,
        id_atributo_nuevo bigint NOT NULL
    );
    DECLARE @esperados int, @religados int, @pendientes int, @mal_ligados int,
            @sin_respaldo int, @marcados int, @activos_antes int, @activos_despues int;

    SELECT @activos_antes = COUNT(*) FROM gdd.plan_remediacion WITH (UPDLOCK, HOLDLOCK) WHERE activo = 1;

    -- 2.1 Candidatos: plan activo -> version inactiva -> unica version activa del mismo atributo
    INSERT INTO @religar (id_plan, id_atributo_anterior, id_atributo_nuevo)
    SELECT p.id, p.id_atributo, v.id
    FROM gdd.plan_remediacion p
    JOIN gdd.atributo a ON a.id = p.id_atributo
    JOIN gdd.atributo v ON v.codigo_dominio = a.codigo_dominio
                       AND v.codigo_atributo = a.codigo_atributo
                       AND v.activo = 1
    WHERE p.activo = 1
      AND a.activo = 0
      AND p.codigo_dominio = v.codigo_dominio
      AND p.codigo_dominio_atributo = v.codigo_dominio + '-' + CAST(v.codigo_atributo AS varchar(20))
      AND NOT EXISTS (SELECT 1 FROM gdd.atributo w
                      WHERE w.codigo_dominio = a.codigo_dominio
                        AND w.codigo_atributo = a.codigo_atributo
                        AND w.activo = 1
                        AND w.id <> v.id);
    SET @esperados = @@ROWCOUNT;

    -- 2.2 El respaldo debe cubrir cada plan a religar con su id_atributo actual
    SELECT @sin_respaldo = COUNT(*)
    FROM @religar r
    WHERE NOT EXISTS (SELECT 1 FROM gdd.plan_remediacion_bkp_20261112 b
                      WHERE b.id = r.id_plan AND b.id_atributo = r.id_atributo_anterior);
    IF @sin_respaldo > 0
    BEGIN
        DECLARE @msg_bkp nvarchar(400);
        SET @msg_bkp = N'El respaldo gdd.plan_remediacion_bkp_20261112 (de una corrida anterior) no cubre '
                     + CAST(@sin_respaldo AS nvarchar(10))
                     + N' plan(es) por religar. Renombrarlo con sp_rename y volver a correr el script.';
        RAISERROR(@msg_bkp, 16, 1);
    END

    -- 2.3 Religar
    UPDATE p
       SET id_atributo = r.id_atributo_nuevo
    FROM gdd.plan_remediacion p
    JOIN @religar r ON r.id_plan = p.id
    WHERE p.id_atributo = r.id_atributo_anterior
      AND p.activo = 1;
    SET @religados = @@ROWCOUNT;

    -- 2.4 Anotar en el respaldo a que version se religo cada plan (lo usa el rollback)
    UPDATE b
       SET id_atributo_religado = r.id_atributo_nuevo
    FROM gdd.plan_remediacion_bkp_20261112 b
    JOIN @religar r ON r.id_plan = b.id AND b.id_atributo = r.id_atributo_anterior;
    SET @marcados = @@ROWCOUNT;

    -- 2.5 Verificacion antes del COMMIT
    SELECT @pendientes = COUNT(*)
    FROM gdd.plan_remediacion p
    JOIN gdd.atributo a ON a.id = p.id_atributo
    JOIN gdd.atributo v ON v.codigo_dominio = a.codigo_dominio
                       AND v.codigo_atributo = a.codigo_atributo
                       AND v.activo = 1
    WHERE p.activo = 1
      AND a.activo = 0
      AND p.codigo_dominio = v.codigo_dominio
      AND p.codigo_dominio_atributo = v.codigo_dominio + '-' + CAST(v.codigo_atributo AS varchar(20))
      AND NOT EXISTS (SELECT 1 FROM gdd.atributo w
                      WHERE w.codigo_dominio = a.codigo_dominio
                        AND w.codigo_atributo = a.codigo_atributo
                        AND w.activo = 1
                        AND w.id <> v.id);

    SELECT @mal_ligados = COUNT(*)
    FROM @religar r
    JOIN gdd.plan_remediacion p ON p.id = r.id_plan
    JOIN gdd.atributo v ON v.id = p.id_atributo
    WHERE p.id_atributo <> r.id_atributo_nuevo
       OR v.activo = 0
       OR p.codigo_dominio_atributo <> v.codigo_dominio + '-' + CAST(v.codigo_atributo AS varchar(20));

    SELECT @activos_despues = COUNT(*) FROM gdd.plan_remediacion WHERE activo = 1;

    IF @religados <> @esperados OR @marcados <> @esperados OR @pendientes <> 0
       OR @mal_ligados <> 0 OR @activos_despues <> @activos_antes
    BEGIN
        DECLARE @msg nvarchar(400);
        SET @msg = N'Verificacion fallida (esperados=' + CAST(@esperados AS nvarchar(10))
                 + N', religados=' + CAST(@religados AS nvarchar(10))
                 + N', anotados en respaldo=' + CAST(@marcados AS nvarchar(10))
                 + N', pendientes=' + CAST(@pendientes AS nvarchar(10))
                 + N', mal ligados=' + CAST(@mal_ligados AS nvarchar(10))
                 + N', activos antes/despues=' + CAST(@activos_antes AS nvarchar(10))
                 + N'/' + CAST(@activos_despues AS nvarchar(10)) + N').';
        RAISERROR(@msg, 16, 1);
    END

    COMMIT TRANSACTION;
    PRINT 'OK: ' + CAST(@religados AS varchar(10)) + ' plan(es) religado(s) a la version vigente y verificados.';
END TRY
BEGIN CATCH
    IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;
    DECLARE @err nvarchar(2048);
    SET @err = N'Se revirtio el PASO 2 (no quedo nada a medias): ' + ERROR_MESSAGE();
    RAISERROR(@err, 16, 1);
END CATCH
GO

/* ---------- PASO 3: resultado (solo lectura) ------------------------------ */
-- 3.1 Planes religados por este script (segun el respaldo)
SELECT p.codigo_dominio, COUNT(*) AS planes_religados
FROM gdd.plan_remediacion_bkp_20261112 b
JOIN gdd.plan_remediacion p ON p.id = b.id
WHERE b.id_atributo_religado IS NOT NULL
GROUP BY p.codigo_dominio
ORDER BY p.codigo_dominio;

-- 3.2 Estado final de los planes activos por dominio
SELECT p.codigo_dominio,
       COUNT(*) AS planes_activos,
       SUM(CASE WHEN a.activo = 1 THEN 1 ELSE 0 END) AS ligados_a_version_vigente,
       SUM(CASE WHEN a.activo = 0 THEN 1 ELSE 0 END) AS ligados_a_version_inactiva
FROM gdd.plan_remediacion p
JOIN gdd.atributo a ON a.id = p.id_atributo
WHERE p.activo = 1
GROUP BY p.codigo_dominio
ORDER BY p.codigo_dominio;
GO

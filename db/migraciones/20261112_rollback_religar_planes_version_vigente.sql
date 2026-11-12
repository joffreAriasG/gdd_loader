/* ============================================================================
   ROLLBACK de 20261112_religar_planes_version_vigente.sql
   Base: DGODELIVERY   Fecha: 2026-11-12

   Devuelve gdd.plan_remediacion.id_atributo al valor del respaldo
   gdd.plan_remediacion_bkp_20261112, SOLO en los planes que la migracion
   religo (columna id_atributo_religado del respaldo) y que siguen ligados a
   esa misma version (si una carga posterior los movio, no se tocan). Despues
   limpia id_atributo_religado en el respaldo. Solo cambia id_atributo:
   ningun otro campo, ningun INSERT ni DELETE. El respaldo no se borra.

   Ojo: con gdd_loader 0.14.1, la siguiente carga del dominio vuelve a
   religar esos planes (el merge de planes compara id_atributo). Si el
   objetivo es volver al comportamiento anterior, revertir tambien el codigo
   (restaurar desde el zip de respaldo de la replicacion).

   Idempotente: correrlo de nuevo no cambia nada. SQL Server 2008 o superior.
   Ejecutar con "detener en error" (sqlcmd -b) o paso a paso.

   Opciones SET: el indice filtrado de gdd.plan_remediacion exige
   QUOTED_IDENTIFIER ON para el UPDATE (sqlcmd lo deja en OFF: error 1934).
   ========================================================================== */
SET NOCOUNT ON;
SET XACT_ABORT ON;
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

IF OBJECT_ID('gdd.plan_remediacion_bkp_20261112', 'U') IS NULL
    RAISERROR('No existe el respaldo gdd.plan_remediacion_bkp_20261112: no hay nada que revertir.', 16, 1);
GO

-- 0.1 Que se revertiria, por dominio
SELECT p.codigo_dominio,
       SUM(CASE WHEN p.id_atributo = b.id_atributo_religado THEN 1 ELSE 0 END) AS a_revertir,
       SUM(CASE WHEN p.id_atributo <> b.id_atributo_religado THEN 1 ELSE 0 END) AS movidos_despues_no_se_tocan
FROM gdd.plan_remediacion_bkp_20261112 b
JOIN gdd.plan_remediacion p ON p.id = b.id
WHERE b.id_atributo_religado IS NOT NULL
GROUP BY p.codigo_dominio
ORDER BY p.codigo_dominio;
GO

/* ---------- PASO 1: revertir (una transaccion) ---------------------------- */
BEGIN TRY
    BEGIN TRANSACTION;

    DECLARE @esperados int, @revertidos int, @limpiados int, @pendientes int;

    SELECT @esperados = COUNT(*)
    FROM gdd.plan_remediacion_bkp_20261112 b
    JOIN gdd.plan_remediacion p WITH (UPDLOCK, HOLDLOCK) ON p.id = b.id
    WHERE b.id_atributo_religado IS NOT NULL
      AND p.id_atributo = b.id_atributo_religado;

    UPDATE p
       SET id_atributo = b.id_atributo
    FROM gdd.plan_remediacion p
    JOIN gdd.plan_remediacion_bkp_20261112 b ON b.id = p.id
    WHERE b.id_atributo_religado IS NOT NULL
      AND p.id_atributo = b.id_atributo_religado;
    SET @revertidos = @@ROWCOUNT;

    -- Limpiar la anotacion solo donde el plan quedo de nuevo en su id original
    UPDATE b
       SET id_atributo_religado = NULL
    FROM gdd.plan_remediacion_bkp_20261112 b
    JOIN gdd.plan_remediacion p ON p.id = b.id
    WHERE b.id_atributo_religado IS NOT NULL
      AND p.id_atributo = b.id_atributo;
    SET @limpiados = @@ROWCOUNT;

    SELECT @pendientes = COUNT(*)
    FROM gdd.plan_remediacion_bkp_20261112 b
    JOIN gdd.plan_remediacion p ON p.id = b.id
    WHERE b.id_atributo_religado IS NOT NULL
      AND p.id_atributo = b.id_atributo_religado;

    IF @revertidos <> @esperados OR @limpiados < @revertidos OR @pendientes <> 0
    BEGIN
        DECLARE @msg nvarchar(400);
        SET @msg = N'Verificacion fallida (esperados=' + CAST(@esperados AS nvarchar(10))
                 + N', revertidos=' + CAST(@revertidos AS nvarchar(10))
                 + N', anotaciones limpiadas=' + CAST(@limpiados AS nvarchar(10))
                 + N', pendientes=' + CAST(@pendientes AS nvarchar(10)) + N').';
        RAISERROR(@msg, 16, 1);
    END

    COMMIT TRANSACTION;
    PRINT 'OK: ' + CAST(@revertidos AS varchar(10)) + ' plan(es) devuelto(s) a su id_atributo anterior.';
END TRY
BEGIN CATCH
    IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;
    DECLARE @err nvarchar(2048);
    SET @err = N'Se revirtio el rollback (no quedo nada a medias): ' + ERROR_MESSAGE();
    RAISERROR(@err, 16, 1);
END CATCH
GO

/* ---------- PASO 2: resultado (solo lectura) ------------------------------ */
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

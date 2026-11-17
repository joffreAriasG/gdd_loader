/* ============================================================================
   ROLLBACK de 20261117_baja_responsables_plaza_repetida.sql
   Base: DGODELIVERY   Fecha: 2026-11-17

   Reactiva (activo = 1, fecha_baja = NULL) las filas que estaban activas en
   el respaldo gdd.dominio_responsable_bkp_20261117 y hoy estan inactivas con
   fecha_baja = la fecha en que corrio la migracion (@fecha_migracion, ajustar
   abajo). Asi no reactiva bajas hechas despues por el merge en otra fecha.
   Ojo: vuelve a dejar plazas repetidas; con 0.14.3 la siguiente carga del
   dominio las da de baja otra vez. El respaldo no se borra.
   Idempotente. SQL Server 2008+.
   ========================================================================== */
SET NOCOUNT ON;
SET XACT_ABORT ON;
SET QUOTED_IDENTIFIER ON;
SET ANSI_NULLS ON;
GO

IF OBJECT_ID('gdd.dominio_responsable_bkp_20261117', 'U') IS NULL
    RAISERROR('No existe el respaldo gdd.dominio_responsable_bkp_20261117: no hay nada que revertir.', 16, 1);
GO

DECLARE @fecha_migracion date;
SET @fecha_migracion = CAST(GETDATE() AS date);   -- AJUSTAR si se revierte otro dia: 'AAAAMMDD'

BEGIN TRY
    BEGIN TRANSACTION;
    UPDATE r SET activo = 1, fecha_baja = NULL
    FROM gdd.dominio_responsable r
    JOIN gdd.dominio_responsable_bkp_20261117 b ON b.id = r.id
    WHERE b.activo = 1 AND r.activo = 0 AND r.fecha_baja = @fecha_migracion
      -- solo filas que en el respaldo repetian la plaza de otra activa del dominio
      AND EXISTS (SELECT 1 FROM gdd.dominio_responsable_bkp_20261117 o
                  WHERE o.activo = 1 AND o.id <> b.id AND o.id_dominio = b.id_dominio
                    AND LTRIM(RTRIM(o.codigo_plaza)) = LTRIM(RTRIM(b.codigo_plaza)));
    PRINT 'OK: ' + CAST(@@ROWCOUNT AS varchar(10)) + ' responsable(s) reactivado(s).';
    COMMIT TRANSACTION;
END TRY
BEGIN CATCH
    IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;
    DECLARE @err nvarchar(2048);
    SET @err = N'Se revirtio el rollback: ' + ERROR_MESSAGE();
    RAISERROR(@err, 16, 1);
END CATCH
GO

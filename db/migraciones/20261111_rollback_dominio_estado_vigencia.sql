/* ============================================================================
   ROLLBACK de 20261111_dominio_estado_vigencia.sql
   Base: DGODELIVERY   Fecha: 2026-11-11

   Quita el estado de vigencia del dominio: FK, valor por defecto, columna
   gdd.dominio_dato.id_estado_vigencia y catalogo
   gdd.cat_estado_vigencia_dominio.

   CAMBIO DESTRUCTIVO: se pierden los estados asignados (Inactivo,
   Confidencial) despues del despliegue. Por eso, antes de quitar la columna,
   se guardan en gdd.dominio_dato_estado_vigencia_rollback (id,
   codigo_dominio, estado). Ese respaldo y gdd.dominio_dato_bkp_20261111 NO
   se borran.

   Antes de correrlo: quitar el campo del formulario de Power Apps (si la app
   lo escribe, fallaria al no existir la columna). Si gdd_loader ya usa el
   estado (version posterior), volver primero a la version anterior.

   Idempotente; una transaccion (todo o nada). SQL Server 2008 o superior.
   ========================================================================== */
SET NOCOUNT ON;
SET XACT_ABORT ON;
GO

BEGIN TRY
    BEGIN TRANSACTION;

    -- 1. Respaldo de los estados asignados (solo si la columna existe y el respaldo no)
    IF COL_LENGTH('gdd.dominio_dato', 'id_estado_vigencia') IS NOT NULL
       AND OBJECT_ID('gdd.dominio_dato_estado_vigencia_rollback', 'U') IS NULL
    BEGIN
        EXEC(N'SELECT d.id, d.codigo_dominio, d.id_estado_vigencia, e.nombre_estado_vigencia,
                      GETDATE() AS fecha_rollback
               INTO gdd.dominio_dato_estado_vigencia_rollback
               FROM gdd.dominio_dato d
               LEFT JOIN gdd.cat_estado_vigencia_dominio e ON e.id = d.id_estado_vigencia;');
        PRINT 'Estados respaldados en gdd.dominio_dato_estado_vigencia_rollback.';
    END

    -- 2. FK, valor por defecto, columna y catalogo
    IF OBJECT_ID('gdd.fk_dominio_estado_vigencia', 'F') IS NOT NULL
        EXEC(N'ALTER TABLE gdd.dominio_dato DROP CONSTRAINT fk_dominio_estado_vigencia;');
    IF OBJECT_ID('gdd.df_dominio_dato_estado_vigencia', 'D') IS NOT NULL
        EXEC(N'ALTER TABLE gdd.dominio_dato DROP CONSTRAINT df_dominio_dato_estado_vigencia;');
    IF COL_LENGTH('gdd.dominio_dato', 'id_estado_vigencia') IS NOT NULL
        EXEC(N'ALTER TABLE gdd.dominio_dato DROP COLUMN id_estado_vigencia;');
    IF OBJECT_ID('gdd.cat_estado_vigencia_dominio', 'U') IS NOT NULL
        EXEC(N'DROP TABLE gdd.cat_estado_vigencia_dominio;');

    -- 3. Verificacion antes del COMMIT
    IF COL_LENGTH('gdd.dominio_dato', 'id_estado_vigencia') IS NOT NULL
       OR OBJECT_ID('gdd.cat_estado_vigencia_dominio', 'U') IS NOT NULL
        RAISERROR('El rollback no quito todos los objetos.', 16, 1);

    COMMIT TRANSACTION;
    PRINT 'OK: rollback del estado de vigencia aplicado.';
END TRY
BEGIN CATCH
    IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;
    DECLARE @err nvarchar(2048);
    SET @err = N'Se revirtio el rollback (no quedo nada a medias): ' + ERROR_MESSAGE();
    RAISERROR(@err, 16, 1);
END CATCH
GO

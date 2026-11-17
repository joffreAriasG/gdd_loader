/* ============================================================================
   Baja de responsables activos con la plaza repetida (GDD-01)
   Base: DGODELIVERY   Fecha: 2026-11-17   gdd_loader: 0.14.3

   Problema (confirmado en produccion el 2026-11-17): gdd.dominio_responsable
   no tiene indice unico por (dominio, codigo_plaza) en los activos. Si quedan
   dos filas activas con la misma plaza, el merge solo compara contra una y la
   otra queda activa para siempre. Caso real: CND, plaza 41376, ids 5 y 63.
   Decision del usuario: una plaza tiene un solo responsable en el dominio.

   Que hace: por cada (id_dominio, plaza recortada) con 2+ filas activas,
   conserva la de fecha_aprobada mas reciente (en empate, id mayor) -- la
   misma regla que usa el merge desde 0.14.3 -- y da de baja logica las
   demas (activo = 0, fecha_baja = hoy). Nunca DELETE. No toca filas sin
   plaza (se informan aparte).

   Pasos: 0 verificacion (solo lectura) · 1 respaldo
   gdd.dominio_responsable_bkp_20261117 (copia completa) · 2 baja en una
   transaccion con verificacion antes del COMMIT · 3 resultado.
   Idempotente. SQL Server 2008+. Ejecutar con "detener en error" (sqlcmd -b).
   No es obligatorio: con el codigo 0.14.3, la siguiente carga del dominio
   hace lo mismo. Este script limpia ya, sin esperar esa carga.

   Rollback: 20261117_rollback_baja_responsables_plaza_repetida.sql
   (reactiva las filas que este script dio de baja, segun el respaldo).
   ========================================================================== */
SET NOCOUNT ON;
SET XACT_ABORT ON;
SET QUOTED_IDENTIFIER ON;
SET ANSI_NULLS ON;
SET ANSI_WARNINGS ON;
SET ARITHABORT ON;
SET CONCAT_NULL_YIELDS_NULL ON;
GO

/* ---------- PASO 0: verificacion (solo lectura) --------------------------- */
SELECT @@SERVERNAME AS servidor, DB_NAME() AS base_datos, SUSER_SNAME() AS usuario;

-- 0.1 Filas que se darian de baja (rn > 1) y la que se conserva (rn = 1)
SELECT d.codigo_dominio, x.id, x.plaza, x.fecha_aprobada,
       CASE WHEN x.rn = 1 THEN 'SE CONSERVA' ELSE 'SE DA DE BAJA' END AS accion
FROM (SELECT r.id, r.id_dominio, LTRIM(RTRIM(r.codigo_plaza)) AS plaza, r.fecha_aprobada,
             ROW_NUMBER() OVER (PARTITION BY r.id_dominio, LTRIM(RTRIM(r.codigo_plaza))
                                ORDER BY r.fecha_aprobada DESC, r.id DESC) AS rn,
             COUNT(*) OVER (PARTITION BY r.id_dominio, LTRIM(RTRIM(r.codigo_plaza))) AS n
      FROM gdd.dominio_responsable r
      WHERE r.activo = 1 AND LTRIM(RTRIM(ISNULL(r.codigo_plaza, ''))) NOT IN ('', '-')) x
JOIN gdd.dominio_dato d ON d.id = x.id_dominio
WHERE x.n > 1
ORDER BY d.codigo_dominio, x.plaza, x.rn;

-- 0.2 Activos sin plaza (no se tocan; solo informativo)
SELECT d.codigo_dominio, r.id
FROM gdd.dominio_responsable r JOIN gdd.dominio_dato d ON d.id = r.id_dominio
WHERE r.activo = 1 AND LTRIM(RTRIM(ISNULL(r.codigo_plaza, ''))) IN ('', '-');
GO

/* ---------- PASO 1: respaldo ---------------------------------------------- */
IF OBJECT_ID('gdd.dominio_responsable_bkp_20261117', 'U') IS NULL
BEGIN
    SELECT * INTO gdd.dominio_responsable_bkp_20261117 FROM gdd.dominio_responsable;
    PRINT 'Respaldo gdd.dominio_responsable_bkp_20261117: ' + CAST(@@ROWCOUNT AS varchar(20)) + ' filas';
END
ELSE
    PRINT 'Respaldo gdd.dominio_responsable_bkp_20261117 ya existe (no se rehace).';
GO

/* ---------- PASO 2: baja (una transaccion) -------------------------------- */
BEGIN TRY
    BEGIN TRANSACTION;

    DECLARE @baja TABLE (id bigint NOT NULL PRIMARY KEY);
    DECLARE @esperados int, @bajas int, @pendientes int, @sin_respaldo int;

    INSERT INTO @baja (id)
    SELECT x.id
    FROM (SELECT r.id,
                 ROW_NUMBER() OVER (PARTITION BY r.id_dominio, LTRIM(RTRIM(r.codigo_plaza))
                                    ORDER BY r.fecha_aprobada DESC, r.id DESC) AS rn
          FROM gdd.dominio_responsable r WITH (UPDLOCK, HOLDLOCK)
          WHERE r.activo = 1 AND LTRIM(RTRIM(ISNULL(r.codigo_plaza, ''))) NOT IN ('', '-')) x
    WHERE x.rn > 1;
    SET @esperados = @@ROWCOUNT;

    SELECT @sin_respaldo = COUNT(*) FROM @baja b
    WHERE NOT EXISTS (SELECT 1 FROM gdd.dominio_responsable_bkp_20261117 k WHERE k.id = b.id AND k.activo = 1);
    IF @sin_respaldo > 0
        RAISERROR('El respaldo gdd.dominio_responsable_bkp_20261117 no cubre todas las filas a dar de baja. Renombrarlo con sp_rename y volver a correr.', 16, 1);

    UPDATE r SET activo = 0, fecha_baja = CAST(GETDATE() AS date)
    FROM gdd.dominio_responsable r JOIN @baja b ON b.id = r.id
    WHERE r.activo = 1;
    SET @bajas = @@ROWCOUNT;

    SELECT @pendientes = COUNT(*)
    FROM (SELECT id_dominio, LTRIM(RTRIM(codigo_plaza)) AS plaza
          FROM gdd.dominio_responsable
          WHERE activo = 1 AND LTRIM(RTRIM(ISNULL(codigo_plaza, ''))) NOT IN ('', '-')
          GROUP BY id_dominio, LTRIM(RTRIM(codigo_plaza))
          HAVING COUNT(*) > 1) z;

    IF @bajas <> @esperados OR @pendientes <> 0
    BEGIN
        DECLARE @msg nvarchar(300);
        SET @msg = N'Verificacion fallida (esperados=' + CAST(@esperados AS nvarchar(10))
                 + N', dados de baja=' + CAST(@bajas AS nvarchar(10))
                 + N', plazas aun repetidas=' + CAST(@pendientes AS nvarchar(10)) + N').';
        RAISERROR(@msg, 16, 1);
    END

    COMMIT TRANSACTION;
    PRINT 'OK: ' + CAST(@bajas AS varchar(10)) + ' responsable(s) con plaza repetida dado(s) de baja y verificados.';
END TRY
BEGIN CATCH
    IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;
    DECLARE @err nvarchar(2048);
    SET @err = N'Se revirtio el PASO 2 (no quedo nada a medias): ' + ERROR_MESSAGE();
    RAISERROR(@err, 16, 1);
END CATCH
GO

/* ---------- PASO 3: resultado (solo lectura). Esperado: 0 filas ------------ */
SELECT d.codigo_dominio, LTRIM(RTRIM(r.codigo_plaza)) AS plaza, COUNT(*) AS activos
FROM gdd.dominio_responsable r JOIN gdd.dominio_dato d ON d.id = r.id_dominio
WHERE r.activo = 1 AND LTRIM(RTRIM(ISNULL(r.codigo_plaza, ''))) NOT IN ('', '-')
GROUP BY d.codigo_dominio, LTRIM(RTRIM(r.codigo_plaza))
HAVING COUNT(*) > 1;
GO

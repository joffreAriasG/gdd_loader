/* ============================================================================
   Completar clase y fecha_aprobacion de gdd.atributo_fuente_consumo
   desde staging.metadata_repositorio
   Base: DGODELIVERY   Fecha: 2026-10-05   gdd_loader: 0.7.0

   Por que: 20261005_metadata_repositorio.sql agrego las columnas clase y
   fecha_aprobacion a gdd.atributo_fuente_consumo (vacias) y lleno
   staging.metadata_repositorio con las _foc de staging.metadata_tecnica. El
   merge solo escribe clase/fecha cuando un dominio se carga con la plantilla
   2.0.0, asi que los consumos de los dominios que aun no se recargan quedan
   en NULL. Este script copia esos dos valores de staging a gdd UNA vez.

   Regla (misma que usara el merge): un consumo activo de gdd se cruza con
   staging.metadata_repositorio por atributo (codigo_dominio-codigo_atributo),
   servidor, coleccion, tabla y campo. Solo se completan columnas que estan en
   NULL; nunca se sobrescribe un valor ya cargado. Sin bitacora (igual que el
   merge de consumo).

   Correr DESPUES de 20261005_metadata_repositorio.sql, en cada base.
   Idempotente: si se corre de nuevo no encuentra NULLs que completar.

   Rollback: vuelve a NULL solo lo que este script completo
     UPDATE c SET c.clase = CASE WHEN b.clase_antes IS NULL THEN NULL ELSE c.clase END,
                  c.fecha_aprobacion = CASE WHEN b.fecha_antes IS NULL THEN NULL ELSE c.fecha_aprobacion END
     FROM gdd.atributo_fuente_consumo c
     JOIN gdd.atributo_fuente_consumo_bkp_20261005b b ON b.id = c.id;
   ========================================================================== */
SET NOCOUNT ON;
SET XACT_ABORT ON;
GO

/* ---------- PASO 0: vista previa (solo lectura) ---------------------------
   Por dominio: consumos activos sin clase/fecha y cuantos se pueden completar. */
;WITH cruce AS (
    SELECT c.id, a.codigo_dominio, c.clase, c.fecha_aprobacion,
           r.clase AS clase_stg, r.fecha_aprobada AS fecha_stg
    FROM gdd.atributo_fuente_consumo c
    JOIN gdd.atributo a                ON a.id = c.id_atributo
    JOIN gdd.base_datos_fuente bd      ON bd.id = c.id_bdd
    JOIN gdd.servidor_fuente sv        ON sv.id = bd.id_servidor
    JOIN gdd.base_datos_fuente_tabla t ON t.id = c.id_tabla
    LEFT JOIN staging.metadata_repositorio r
           ON  r.codigo_dominio_atributo = a.codigo_dominio + '-' + CAST(a.codigo_atributo AS varchar(20))
           AND r.servidor_foc     = sv.nombre_servidor
           AND r.coleccion_foc    = bd.nombre_bdd
           AND r.tabla_bv_foc     = t.nombre_tabla
           AND r.nombre_campo_foc = c.nombre_campo
    WHERE c.activo = 1 AND a.activo = 1
)
SELECT codigo_dominio,
       COUNT(*)                                                                   AS consumos_activos,
       SUM(CASE WHEN clase IS NULL THEN 1 ELSE 0 END)                             AS sin_clase,
       SUM(CASE WHEN clase IS NULL AND clase_stg IS NOT NULL THEN 1 ELSE 0 END)   AS clase_a_completar,
       SUM(CASE WHEN fecha_aprobacion IS NULL THEN 1 ELSE 0 END)                  AS sin_fecha,
       SUM(CASE WHEN fecha_aprobacion IS NULL AND fecha_stg IS NOT NULL THEN 1 ELSE 0 END) AS fecha_a_completar
FROM cruce
GROUP BY codigo_dominio
ORDER BY codigo_dominio;
GO

/* ---------- PASO 1: respaldo de las filas que se van a tocar --------------- */
IF OBJECT_ID('gdd.atributo_fuente_consumo_bkp_20261005b') IS NULL
BEGIN
    SELECT c.id, c.clase AS clase_antes, c.fecha_aprobacion AS fecha_antes,
           SYSDATETIME() AS fecha_respaldo
    INTO gdd.atributo_fuente_consumo_bkp_20261005b
    FROM gdd.atributo_fuente_consumo c
    WHERE c.activo = 1 AND (c.clase IS NULL OR c.fecha_aprobacion IS NULL);

    PRINT CONCAT('Respaldo: ', @@ROWCOUNT, ' filas');
END
ELSE
    PRINT 'Respaldo gdd.atributo_fuente_consumo_bkp_20261005b ya existe (no se rehace).';
GO

/* ---------- PASO 2: completar (una transaccion) ---------------------------- */
BEGIN TRY
    BEGIN TRANSACTION;

    UPDATE c
    SET c.clase            = COALESCE(c.clase, r.clase),
        c.fecha_aprobacion = COALESCE(c.fecha_aprobacion, r.fecha_aprobada)
    FROM gdd.atributo_fuente_consumo c
    JOIN gdd.atributo a                ON a.id = c.id_atributo
    JOIN gdd.base_datos_fuente bd      ON bd.id = c.id_bdd
    JOIN gdd.servidor_fuente sv        ON sv.id = bd.id_servidor
    JOIN gdd.base_datos_fuente_tabla t ON t.id = c.id_tabla
    JOIN staging.metadata_repositorio r
         ON  r.codigo_dominio_atributo = a.codigo_dominio + '-' + CAST(a.codigo_atributo AS varchar(20))
         AND r.servidor_foc     = sv.nombre_servidor
         AND r.coleccion_foc    = bd.nombre_bdd
         AND r.tabla_bv_foc     = t.nombre_tabla
         AND r.nombre_campo_foc = c.nombre_campo
    WHERE c.activo = 1 AND a.activo = 1
      AND ((c.clase IS NULL AND r.clase IS NOT NULL)
        OR (c.fecha_aprobacion IS NULL AND r.fecha_aprobada IS NOT NULL))
      -- solo filas respaldadas en el PASO 1
      AND EXISTS (SELECT 1 FROM gdd.atributo_fuente_consumo_bkp_20261005b b WHERE b.id = c.id);

    PRINT CONCAT('Consumos completados: ', @@ROWCOUNT);

    COMMIT TRANSACTION;
END TRY
BEGIN CATCH
    IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
GO

/* ---------- PASO 3: verificacion ------------------------------------------
   3.1 Lo que queda vacio por dominio. Lo que sigue en NULL no tiene fila
       equivalente en staging (o staging no tenia el dato): se completara
       cuando ese dominio se cargue con la plantilla 2.0.0.                   */
SELECT a.codigo_dominio,
       COUNT(*)                                                  AS consumos_activos,
       SUM(CASE WHEN c.clase IS NULL THEN 1 ELSE 0 END)            AS sin_clase,
       SUM(CASE WHEN c.fecha_aprobacion IS NULL THEN 1 ELSE 0 END) AS sin_fecha
FROM gdd.atributo_fuente_consumo c
JOIN gdd.atributo a ON a.id = c.id_atributo
WHERE c.activo = 1 AND a.activo = 1
GROUP BY a.codigo_dominio
ORDER BY a.codigo_dominio;

-- 3.2 Detalle de los que siguen sin clase (para revisar a mano)
SELECT a.codigo_dominio, a.codigo_atributo, sv.nombre_servidor, bd.nombre_bdd,
       t.nombre_tabla, c.nombre_campo, c.id
FROM gdd.atributo_fuente_consumo c
JOIN gdd.atributo a                ON a.id = c.id_atributo
JOIN gdd.base_datos_fuente bd      ON bd.id = c.id_bdd
JOIN gdd.servidor_fuente sv        ON sv.id = bd.id_servidor
JOIN gdd.base_datos_fuente_tabla t ON t.id = c.id_tabla
WHERE c.activo = 1 AND a.activo = 1 AND c.clase IS NULL
ORDER BY a.codigo_dominio, a.codigo_atributo, c.nombre_campo;

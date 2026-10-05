/* ============================================================================
   Baja logica de las fuentes oficiales "secundarias" generadas desde _foc
   Base: DGODELIVERY   Fecha: 2026-10-01   gdd_loader: 0.6.0

   Definicion (2026-10-01): las columnas servidor_foc, coleccion_foc,
   tabla_bv_foc y nombre_campo_foc van SOLO a gdd.atributo_fuente_consumo
   (catalogos controlados base_datos_fuente / base_datos_fuente_tabla).
   Hasta gdd_loader 0.5.2 ademas generaban una fila en
   gdd.atributo_fuente_oficial con es_fuente_primaria = 0 (y tabla_bv_foc
   guardada como base de datos). Diagnostico del 2026-10-01: 23 activas
   (ADS 4, CAC 19).

   Desde 0.6.0 el loader ya no las genera, y la siguiente carga de cada
   dominio las daria de baja sola. Este script lo hace YA, para todos los
   dominios, igual que el merge: baja logica (activo = 0, fecha_baja = hoy)
   + una linea ELIMINAR en gdd.bitacora_carga por fila. NUNCA borra filas.

   Correr DESPUES de 20261001_fuente_oficial_nombre_tabla.sql (ese script
   respalda la tabla completa en gdd.atributo_fuente_oficial_bkp_20261001).
   Este script ademas guarda las filas afectadas en
   gdd.atributo_fuente_oficial_bkp_20261001b.

   Idempotente: si se corre de nuevo no encuentra secundarias activas.
   Todo en una transaccion: si algo falla, no queda nada aplicado.

   NO toca gdd.base_datos_fuente. Las filas creadas ahi con nombres de tabla
   (PASO 4, solo lectura) las revisa el equipo experto: es un catalogo
   controlado y puede haber filas que si deban quedar.

   Rollback (reactivar las mismas filas):
     UPDATE f SET f.activo = 1, f.fecha_baja = NULL
     FROM gdd.atributo_fuente_oficial f
     JOIN gdd.atributo_fuente_oficial_bkp_20261001b b ON b.id = f.id;
     -- y borrar sus lineas de bitacora:
     DELETE bc FROM gdd.bitacora_carga bc
     JOIN gdd.atributo_fuente_oficial_bkp_20261001b b ON b.id = bc.id_registro
     WHERE bc.tabla = 'atributo_fuente_oficial' AND bc.accion = 'ELIMINAR'
       AND bc.id_carga IS NULL
       AND CAST(bc.fecha_operacion AS date) = '2026-10-01';  -- ajustar al dia en que se corrio
   ========================================================================== */
SET NOCOUNT ON;
SET XACT_ABORT ON;
GO

/* ---------- PASO 1: respaldo de las filas afectadas ------------------------ */
IF OBJECT_ID('gdd.atributo_fuente_oficial_bkp_20261001b') IS NULL
    SELECT f.*
    INTO gdd.atributo_fuente_oficial_bkp_20261001b
    FROM gdd.atributo_fuente_oficial f
    WHERE f.es_fuente_primaria = 0 AND f.activo = 1;
GO

/* ---------- PASO 2: bitacora + baja logica (una transaccion) -------------- */
BEGIN TRY
    BEGIN TRANSACTION;

    DECLARE @hoy date = CAST(SYSDATETIME() AS date);

    INSERT INTO gdd.bitacora_carga
        (tabla, id_registro, accion, codigo_dominio, codigo_dominio_atributo, version_resultante)
    SELECT 'atributo_fuente_oficial', f.id, 'ELIMINAR', a.codigo_dominio,
           a.codigo_dominio + '-' + CAST(a.codigo_atributo AS varchar(20)), NULL
    FROM gdd.atributo_fuente_oficial f
    JOIN gdd.atributo a ON a.id = f.id_atributo
    WHERE f.es_fuente_primaria = 0 AND f.activo = 1;

    UPDATE gdd.atributo_fuente_oficial
    SET activo = 0, fecha_baja = @hoy
    WHERE es_fuente_primaria = 0 AND activo = 1;

    PRINT CONCAT('Fuentes secundarias dadas de baja: ', @@ROWCOUNT);

    COMMIT TRANSACTION;
END TRY
BEGIN CATCH
    IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
GO

/* ---------- PASO 3: verificacion ------------------------------------------ */
-- 3.1 Debe dar 0
SELECT COUNT(*) AS secundarias_activas
FROM gdd.atributo_fuente_oficial
WHERE es_fuente_primaria = 0 AND activo = 1;

-- 3.2 Lo que se dio de baja (debe coincidir con el diagnostico: ADS 4, CAC 19)
SELECT a.codigo_dominio, COUNT(*) AS dadas_de_baja
FROM gdd.atributo_fuente_oficial_bkp_20261001b b
JOIN gdd.atributo a ON a.id = b.id_atributo
GROUP BY a.codigo_dominio;

/* ---------- PASO 4: catalogo base_datos_fuente (solo lectura) -------------
   Filas usadas UNICAMENTE por fuentes secundarias (ahora inactivas), sin
   uso en fuentes primarias, consumo ni base_datos_fuente_tabla. Son las
   candidatas a nombres de tabla creados por la logica anterior. Revisar con
   el equipo experto antes de cualquier limpieza.                            */
SELECT bd.id, sv.nombre_servidor, bd.nombre_bdd,
       COUNT(f.id) AS usos_en_secundarias
FROM gdd.base_datos_fuente bd
JOIN gdd.servidor_fuente sv ON sv.id = bd.id_servidor
JOIN gdd.atributo_fuente_oficial f ON f.id_bdd = bd.id AND f.es_fuente_primaria = 0
WHERE NOT EXISTS (SELECT 1 FROM gdd.atributo_fuente_oficial p
                  WHERE p.id_bdd = bd.id AND p.es_fuente_primaria = 1)
  AND NOT EXISTS (SELECT 1 FROM gdd.atributo_fuente_consumo c WHERE c.id_bdd = bd.id)
  AND NOT EXISTS (SELECT 1 FROM gdd.base_datos_fuente_tabla t WHERE t.id_bdd = bd.id)
GROUP BY bd.id, sv.nombre_servidor, bd.nombre_bdd
ORDER BY bd.nombre_bdd;

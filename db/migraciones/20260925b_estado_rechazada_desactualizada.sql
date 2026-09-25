/* ============================================================================
   Fase 2 -- nuevo estado de carga RECHAZADA_DESACTUALIZADA
   Base: DGODELIVERY   Fecha: 2026-09-25

   Una plantilla precargada (generar_cli) declara en _Control el id_carga
   sobre el que se genero. Si al cargarla ya existe una carga MERGE_OK
   posterior del mismo dominio, se rechaza con este estado.

   Cambio: recrea el CHECK ck_cc_estado agregando el estado nuevo. No toca
   datos. Idempotente. Correr ANTES de desplegar gdd_loader 0.3.0.
   Rollback: recrear ck_cc_estado sin 'RECHAZADA_DESACTUALIZADA' (solo si no
   hay filas con ese estado).
   ========================================================================== */
SET NOCOUNT ON;
GO

IF EXISTS (SELECT 1 FROM sys.check_constraints
           WHERE name = 'ck_cc_estado' AND parent_object_id = OBJECT_ID('gdd.carga_control')
             AND definition NOT LIKE '%RECHAZADA_DESACTUALIZADA%')
    ALTER TABLE gdd.carga_control DROP CONSTRAINT ck_cc_estado;
GO

IF NOT EXISTS (SELECT 1 FROM sys.check_constraints
               WHERE name = 'ck_cc_estado' AND parent_object_id = OBJECT_ID('gdd.carga_control'))
    ALTER TABLE gdd.carga_control ADD CONSTRAINT ck_cc_estado CHECK (estado IN (
        'EN_PROCESO',
        'RECHAZADA_SIN_CONTROL', 'RECHAZADA_VERSION', 'RECHAZADA_ESTRUCTURA',
        'RECHAZADA_LOADER', 'RECHAZADA_DOMINIO', 'RECHAZADA_DESACTUALIZADA',
        'OMITIDA_SIN_CAMBIOS',
        'ERROR_STAGING', 'ERROR_MERGE', 'MERGE_PARCIAL', 'MERGE_OK',
        'ERROR'));
GO

-- Verificacion: debe devolver 1 fila con incluye_estado = 1
SELECT name, CASE WHEN definition LIKE '%RECHAZADA_DESACTUALIZADA%' THEN 1 ELSE 0 END AS incluye_estado
FROM sys.check_constraints
WHERE name = 'ck_cc_estado' AND parent_object_id = OBJECT_ID('gdd.carga_control');

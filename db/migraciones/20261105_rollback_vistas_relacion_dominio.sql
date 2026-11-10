/* =============================================================================
   20261105_rollback_vistas_relacion_dominio.sql   (gdd_loader v0.9.0)

   Revierte 20261105_vistas_relacion_dominio.sql: elimina las dos vistas del
   grafo de relaciones. NO toca tablas ni datos (las vistas no guardan datos).
   Idempotente: si una vista no existe, no hace nada.

   Despues del rollback, grafo_cli falla con un mensaje claro (falta la vista);
   el resto de gdd_loader (carga, merge, vinculos, generar) no usa estas vistas.
   ============================================================================= */

USE DGODELIVERY;
GO

-- Primero la que depende de la otra
IF OBJECT_ID(N'gdd.v_relacion_dominio', N'V') IS NOT NULL
    DROP VIEW gdd.v_relacion_dominio;
GO

IF OBJECT_ID(N'gdd.v_relacion_atributo', N'V') IS NOT NULL
    DROP VIEW gdd.v_relacion_atributo;
GO

-- Verificacion: debe devolver 0 filas
SELECT name FROM sys.views
WHERE schema_id = SCHEMA_ID(N'gdd') AND name IN (N'v_relacion_atributo', N'v_relacion_dominio');

-- Rename de staging.metadata_tecnica.clase_foc -> coleccion_foc (2026-09-21)
--
-- Motivo (confirmado con el usuario): la plantilla Excel de MetadataTécnica
-- ya renombró el header de esa columna de "clase_foc" a "coleccion_foc".
-- Alcance del cambio: SOLO gdd_loader.pipeline.carga_gdd._resolver_destino_
-- consumo (el flujo de gdd.atributo_fuente_consumo, catalogo CONTROLADO
-- contra gdd.base_datos_fuente). La fila secundaria que gdd_loader tambien
-- genera hacia gdd.atributo_fuente_oficial (columnas "_foc" como fuente
-- secundaria descriptiva, ver gdd_mapping.mapear_metadata_tecnica) sigue
-- leyendo la MISMA columna fisica -- por eso el rename de la columna en
-- staging es uno solo, no dos.
--
-- Este script SOLO renombra la columna (sp_rename) -- NO cambia tipo,
-- nulabilidad ni datos. staging.metadata_tecnica acumula filas de multiples
-- cargas en el tiempo (no se trunca entre cargas bajo estrategia DOMINIO);
-- el rename preserva todos los valores ya cargados, solo bajo el nombre
-- nuevo de columna.
--
-- Orden de despliegue (igual que refactors anteriores de esta sesion):
--   1. Correr este script.
--   2. Recien despues desplegar el codigo Python actualizado
--      (sheet_config.py, gdd_mapping.py, carga_gdd.py) -- el loader arma el
--      INSERT/SELECT con los nombres de columna tal cual estan en
--      SheetConfig.columnas; si el codigo nuevo corre ANTES de este rename,
--      la carga fallara buscando una columna "coleccion_foc" que todavia no
--      existe en staging.
--   3. La plantilla Excel real ya trae el header "coleccion_foc" (confirmado
--      por el usuario) -- no requiere accion adicional aqui.

-- Verificacion PREVIA -- confirmar que la columna existe con el nombre viejo
-- y no hay ya una columna con el nombre nuevo (evita error de sp_rename):
SELECT name AS columna, system_type_id, is_nullable
FROM sys.columns
WHERE object_id = OBJECT_ID('DGODELIVERY.staging.metadata_tecnica')
  AND name IN ('clase_foc', 'coleccion_foc');
-- Esperado ANTES de correr el rename: una sola fila, columna = 'clase_foc'.
-- Si ya aparece 'coleccion_foc' (el rename ya se corrio antes), DETENTE --
-- no vuelvas a correr sp_rename.

EXEC sp_rename
    'DGODELIVERY.staging.metadata_tecnica.clase_foc',
    'coleccion_foc',
    'COLUMN';

-- Verificacion POSTERIOR:
SELECT name AS columna, system_type_id, is_nullable
FROM sys.columns
WHERE object_id = OBJECT_ID('DGODELIVERY.staging.metadata_tecnica')
  AND name IN ('clase_foc', 'coleccion_foc');
-- Esperado AHORA: una sola fila, columna = 'coleccion_foc', mismo
-- system_type_id/is_nullable que antes del rename (sp_rename no los toca).

-- Rollback (si hiciera falta revertir, ANTES de desplegar el codigo nuevo):
-- EXEC sp_rename 'DGODELIVERY.staging.metadata_tecnica.coleccion_foc', 'clase_foc', 'COLUMN';

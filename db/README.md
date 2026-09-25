# Scripts de base de datos (DGODELIVERY)

`migraciones/` contiene los scripts DDL aplicados a DGODELIVERY, con prefijo
`AAAAMMDD[letra]_` para mantener el orden de ejecucion.

Reglas:
- Un script por cambio; nunca editar un script ya aplicado en produccion,
  crear uno nuevo.
- Correr el SQL ANTES de desplegar el codigo que lo necesita.
- Los scripts deben ser idempotentes (IF NOT EXISTS / verificaciones).

## Pendiente de incorporar
Scripts entregados en sesiones anteriores que no estaban en esta carpeta
(recuperar de donde se guardaron y agregar con su fecha real):
- Estructura BDD.sql (baseline del esquema gdd)
- alter_drop_id_clasificacion.sql (2026-09-14)
- alter_dominio_dato_versionado.sql (2026-09-14)
- crear_estructura.sql (2026-09-15)
- crear_plan_remediacion.sql / corregir_staging_plan_remediacion.sql (2026-09-15/16)
- alter_investigacion_referencia_normativa.sql (2026-09-16)
- diagnostico_valores_nan.sql (solo lectura)

# Base de datos y versiones de plantilla (DGODELIVERY)

## Scripts de migración

`migraciones/` contiene los scripts DDL aplicados a DGODELIVERY, con prefijo
`AAAAMMDD[letra]_` para mantener el orden de ejecución.

Reglas:
- Un script por cambio; nunca editar un script ya aplicado en producción,
  crear uno nuevo.
- Correr el SQL ANTES de desplegar el código que lo necesita.
- Los scripts deben ser idempotentes (IF NOT EXISTS / verificaciones).

### Pendiente de incorporar
Scripts entregados en sesiones anteriores que no estaban en esta carpeta
(recuperar de donde se guardaron y agregar con su fecha real):
- Estructura BDD.sql (baseline del esquema gdd)
- alter_drop_id_clasificacion.sql (2026-09-14)
- alter_dominio_dato_versionado.sql (2026-09-14)
- crear_estructura.sql (2026-09-15)
- crear_plan_remediacion.sql / corregir_staging_plan_remediacion.sql (2026-09-15/16)
- alter_investigacion_referencia_normativa.sql (2026-09-16)
- diagnostico_valores_nan.sql (solo lectura)

## Versiones de plantilla

Cada archivo que se carga debe corresponder a una versión registrada en
`gdd.plantilla_version`. El loader compara:

1. **Lo declarado**: la hoja oculta `_Control` del archivo.
2. **Lo real**: la huella (`hash_estructura`) calculada con los encabezados
   de las hojas del contrato. Las hojas auxiliares (p. ej.
   `Lista De Referencia`) no forman parte de la huella.

### Hoja `_Control` del molde

Hoja llamada exactamente `_Control`, visibilidad **muy oculta**
(`veryHidden`) y protegida. Columna A = clave, columna B = valor, fila 1 =
encabezado:

| clave | valor |
|---|---|
| id_plantilla | GDD-DOMINIO |
| version_plantilla | 1.0.0 |

(En Fase 2 el generador agregará `codigo_dominio`, `id_carga_base`,
`id_envio`.)

### Qué número de versión cambia

| Cambio en el molde | Versión | Huella | Loader / DDL | Versión anterior |
|---|---|---|---|---|
| Renombrar o quitar hoja/columna, cambiar tipo o clave, **cambiar una celda de texto a fecha real** | MAJOR | cambia | Sí, release coordinado | RETIRADA |
| Agregar columna u hoja | MINOR | cambia | Sí, si el loader debe leerla | DEPRECADA con gracia |
| Formatos, validaciones, listas desplegables, Excel Scripts | PATCH | igual | No | DEPRECADA o RETIRADA |

Regla práctica: si el cambio afecta cómo se lee una columna, no es PATCH.

### Proceso de publicación (el orden importa)

1. Preparar el molde nuevo con `_Control` actualizado (`version_plantilla`).
2. Simular el registro y revisar advertencias:
   `python -m gdd_loader.plantilla_cli registrar --molde <molde> --version X.Y.Z`
3. Si es MAJOR/MINOR y el loader debe cambiar: ajustar `SheetConfig`, tests,
   subir `__version__`, commit + tag, `git pull` en todos los equipos y
   aplicar el DDL. Registrar con `--version-loader-minima` = esa versión.
4. Registrar (BORRADOR): mismo comando con `--aplicar`.
5. Publicar el molde en SharePoint (biblioteca de solo lectura para quienes
   cargan, nombre con versión, p. ej. `GDD_Plantilla_vX.Y.Z.xlsx`).
6. Activar: `python -m gdd_loader.plantilla_cli publicar --version X.Y.Z --anterior DEPRECADA --dias-gracia 15`
   (o `--anterior RETIRADA` para un MAJOR).
7. Comunicar el cambio al equipo que carga.

### Transición (plantillas sin `_Control`)

Con `GDD_EXIGIR_CONTROL=NO` el loader acepta archivos sin `_Control` si su
huella coincide con una versión registrada (queda `origen_version = HUELLA`
y una advertencia en `carga_control.mensaje`). Por eso, **antes de la
primera ejecución de `procesar_cli`**, hay que registrar y publicar la
versión que corresponde a la plantilla que usan hoy. En la fecha de corte
comunicada, cambiar a `GDD_EXIGIR_CONTROL=SI`.

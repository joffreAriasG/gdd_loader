# gdd_loader

Carga de plantillas Excel de gobierno de datos (carpeta sincronizada de
SharePoint via OneDrive) hacia el esquema `staging` de `DGODELIVERY`
en SQL Server on-prem.

Nota de alcance: la validación de la plantilla se hace del lado del
usuario, antes de subirla a SharePoint — este proyecto no revalida
reglas de negocio, solo garantiza que la estructura (columnas) es la
esperada antes de insertar, y deja logs de cada corrida.

## Arquitectura de capas

```
config          -> lee .env / variables de entorno, valida configuración
domain          -> QUE se carga (SheetConfig: hoja, tabla destino, columnas, tipos)
extract         -> COMO se lee el origen (hoy: Excel local/sincronizado)
load            -> COMO se escribe el destino (engine SQLAlchemy + StagingRepository)
pipeline        -> orquesta extract + load, no sabe de Excel ni de SQL directamente
cli             -> punto de entrada, define exit code para Task Scheduler
logging_setup   -> logging a consola + archivo rotativo (logs/)
```

Cada capa solo depende de la de abajo. Si mañana el origen pasa a ser
Microsoft Graph API en vez de una carpeta sincronizada, o el destino
cambia, solo se reemplaza el módulo de esa capa (`extract/` o
`load/`) — `pipeline/` y `domain/` no se tocan.

Decisiones que quedaron abiertas y son configurables sin tocar código
(ver `.env.example`):

- **Autenticación a SQL Server**: `GDD_SQL_AUTH_MODO=WINDOWS` (por
  defecto) o `SQL` (usuario/password).
- **Estrategia de staging**: `GDD_ESTRATEGIA_STAGING=DOMINIO` (por
  defecto) borra solo las filas de staging del dominio que trae el
  archivo cargado (según `SheetConfig.columna_clave` — `codigo_dominio`
  para `detalle_atributos`, `codigo_dominio_atributo` para
  `metadata_tecnica`) y las reinserta, sin tocar otros dominios ya
  cargados en la misma tabla. `TRUNCATE` vacía toda la tabla — usar
  solo para un reset manual completo, nunca en operación normal con
  varios dominios.
- **Manejo de errores por hoja**: el pipeline sigue con la siguiente
  hoja si una falla (no es all-or-nothing entre hojas), pero el CLI
  retorna código de salida 1 si hubo algún error, para que se note en
  la ejecución programada.

## Instalación

```powershell
# desde la carpeta del proyecto, con tu entorno de Anaconda activado
pip install -e ".[dev]"
copy .env.example .env
# editar .env con el servidor real y confirmar autenticación/estrategia
```

## Ejecutar (operación normal)

```powershell
python -m gdd_loader.procesar_cli
```

Procesa cada archivo de la carpeta de entrada (`GDD_CARPETA_ORIGEN`) de
punta a punta, con un `id_carga` por archivo registrado en
`gdd.carga_control`:

1. Valida la plantilla: hoja oculta `_Control` (id y versión declarados)
   y huella de estructura contra `gdd.plantilla_version`.
2. Carga las hojas a `staging` (marcadas con `id_carga`).
3. Si staging quedó completo, ejecuta el merge hacia `gdd`.
4. Mueve el archivo a `GDD_CARPETA_ARCHIVO` (OK / sin cambios) o a
   `GDD_CARPETA_RECHAZADOS` (rechazo o error), renombrado como
   `{dominio}_{id_carga}_{hash8}.xlsx`.
5. Elimina archivos archivados con más de `GDD_DIAS_RETENCION` días (el
   registro en `carga_control` se conserva).

El nombre con el que llega el archivo no importa: el dominio se toma de la
hoja `DetalleAtributos`.

Consultar el resultado:

```sql
SELECT TOP 20 id_carga, codigo_dominio, estado, version_plantilla,
       origen_version, subido_por, fecha_inicio, mensaje
FROM gdd.carga_control ORDER BY id_carga DESC;

SELECT * FROM gdd.carga_control_detalle WHERE id_carga = <id>;
```

### Plantilla precargada para hacer cambios (Fase 2)

```powershell
python -m gdd_loader.generar_cli --dominio CAC
```

Genera `CAC_v{version}_c{id_carga}.xlsx` en `GDD_CARPETA_PLANTILLAS`: el molde
vigente (con sus Office Scripts y su etiqueta de clasificación intactos)
llenado con la última carga `MERGE_OK` del dominio, y su hoja oculta
`_Control` con `id_carga_base`. El responsable del dominio edita ese archivo y
lo sube a la carpeta de entrada.

- Si entre la generación y la carga hubo otra carga exitosa del dominio, se
  rechaza con `RECHAZADA_DESACTUALIZADA`: generar de nuevo y reaplicar.
- Cuando se publica una versión nueva de plantilla, basta con volver a
  generar: los datos pasan al molde nuevo por nombre de columna.
- El archivo de la última carga OK de cada dominio nunca se purga (es la
  fuente del generador; si faltara, se usa staging).
- La primera carga de un dominio nuevo se hace sobre el molde en blanco.

### Listas de referencia (valores permitidos en la plantilla)

```powershell
python -m gdd_loader.listas_cli comparar                                    # solo lectura
python -m gdd_loader.listas_cli comparar --script-semilla semilla.sql       # + script (no se ejecuta)
```

Compara las listas del molde vigente (`ListaDeReferencia`, `FuentesConsumo`)
con los catálogos de la BD que usa el loader para validar. En **pruebas**
muestra en qué difiere la BD de producción y genera un script de semilla para
revisarlo y correrlo a mano. En **producción**, cuando no haya diferencias,
activar `GDD_LISTAS_REFERENCIA=BD` para que las plantillas generadas tomen las
listas de los catálogos. `FuentesPrimarias` siempre sale del molde. La hoja
`Reporte_Errores` sale vacía en toda plantilla generada.

Para limpiar esa hoja en un molde nuevo (no cambia su huella):
`python -m gdd_loader.plantilla_cli limpiar --molde <molde> --salida <nuevo>`

### Herramientas manuales (diagnóstico / reprocesos)

```powershell
python -m gdd_loader.cli --archivo X.xlsx        # solo Excel -> staging
python -m gdd_loader.merge_cli --dominio ADS     # solo staging -> gdd
```

### Versiones de plantilla (equipo central)

Ver `db/README.md`. Resumen:

```powershell
python -m gdd_loader.plantilla_cli registrar --molde Plantilla_v1.0.0.xlsx --version 1.0.0            # simulación
python -m gdd_loader.plantilla_cli registrar --molde Plantilla_v1.0.0.xlsx --version 1.0.0 --aplicar  # BORRADOR
python -m gdd_loader.plantilla_cli publicar --version 1.0.0 --anterior RETIRADA                       # VIGENTE
```

## Tests

```powershell
pytest
```

Los tests no tocan SQL Server real: `test_excel_reader.py` genera un
Excel sintético (sin datos reales) en un directorio temporal, y
`test_staging_repository.py` usa un engine simulado (`Mock`) para
verificar que se llama TRUNCATE + INSERT en el orden correcto.

## Agregar una nueva plantilla / dominio

1. Agregar un `SheetConfig` nuevo en `domain/sheet_config.py` (hoja,
   tabla de staging, columnas esperadas, tipos si aplica, y
   `columna_clave` para que la estrategia `DOMINIO` sepa qué borrar
   antes de insertar).
2. Si la tabla de staging todavía no existe en SQL Server, crearla
   primero (fuera de este repo, es DDL manual por ahora).
3. Pasar esa lista de `SheetConfig` al pipeline (hoy el CLI usa
   `HOJAS_ADS`; para varios dominios conviene parametrizar cuál lista
   usar por argumento).

## Pendiente (fuera del alcance de esta primera versión)

- Hojas `Términos`, `PlanDeRemediación`, `pryprcnorm` y la hoja
  `Lista De Referencia`: sin tabla de staging destino confirmada
  todavía.
- El `MERGE` desde `staging` hacia las tablas definitivas de `gdd`
  (con lookup-or-insert de `servidor_fuente`/`base_datos_fuente` y
  resolución de FKs contra los catálogos `cat_*`). El control de
  versión por `fecha_aprobacion` (misma fecha = update, fecha más
  reciente = reemplazo, atributo ausente en la carga = eliminación)
  se decidió que vive aquí, no en `staging` — `staging` se mantiene
  como espejo simple del Excel por dominio.
- Migrar la autenticación a SQL Server de usuario personal a una
  cuenta de servicio antes de pasar a producción.

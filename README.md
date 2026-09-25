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

## Ejecutar

```powershell
python -m gdd_loader.cli --archivo AdministracionDeSeguros.xlsx
```

Lee el archivo desde `GDD_CARPETA_ORIGEN` (definida en `.env`) y carga
las hojas configuradas en `domain/sheet_config.py` (hoy: `Detalle
Atributos` y `Metadata Técnica`) a sus tablas de staging.

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

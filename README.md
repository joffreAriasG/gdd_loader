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

### Notificación de cargas (v0.5.0)

Con `GDD_CARPETA_NOTIFICACIONES` configurada, `procesar_cli` deja por cada
archivo procesado un `resultado_<dominio>_<id_carga>_<id>.json` en esa carpeta
(sincronizada con SharePoint) y, junto a él, un `.html` con el mismo nombre: la
minuta como página completa, para abrirla en el navegador o reenviarla a mano
(copiar y pegar en Outlook conserva las tablas). Un flujo de Power Automate con
conectores estándar puede tomar el JSON y enviar el correo:

- **Para**: responsables activos del dominio con `id_rol = GDD_NOTIF_ID_ROL`
  (1 por defecto) en `gdd.dominio_responsable`. El JSON trae los nombres; el
  flujo los resuelve a correo en el directorio (solo si hay exactamente una
  coincidencia) y, si no, usa el correo de respaldo configurado en el flujo.
- **Asunto**: campo `asunto` del JSON (`[PRUEBAS]` fuera de producción).
- **Cuerpo**: campo `minuta_html`, tipo acta para aprobación: datos de la
  carga, resumen por sección y, por sección, registros nuevos, modificados
  (valor anterior → nuevo) y dados de baja. Cubre atributos, fuentes
  oficiales, fuentes de consumo, responsables, investigación, respaldos y
  planes de remediación.

Cómo se calcula el detalle: antes y después del merge se toma una foto
legible (solo `SELECT`, catálogos resueltos a texto) de lo activo del dominio
y se comparan por clave natural (`notificacion/`). No cambia la lógica del
merge ni `gdd.bitacora_carga`. Si la foto o la escritura del JSON fallan, la
carga sigue igual y el problema queda en el log.

El JSON contiene valores de los registros: la carpeta debe tener acceso solo
para el equipo de gobierno de datos.

### Plantilla 2.0.0: hoja MetadataRepositorio (v0.7.0)

Desde gdd_loader 0.7.0 la plantilla GDD-DOMINIO vigente es la **2.0.0**:

- `MetadataTecnica` ya no trae `clase` ni las columnas `_foc` (11 columnas).
- Hoja nueva `MetadataRepositorio` (`codigo_dominio_atributo, clase,
  coleccion_foc, servidor_foc, tabla_bv_foc, nombre_campo_foc,
  fecha_aprobada`): una fila por destino de consumo → `staging.metadata_repositorio`
  → `gdd.atributo_fuente_consumo`. Todas las columnas son obligatorias (ni
  vacías ni `-`); la hoja puede venir sin filas (se dan de baja los consumos
  del dominio). `clase` es texto libre; `fecha_aprobada` acepta fecha real de
  Excel o `DD/MM/AAAA`.
- `clase` y `fecha_aprobacion` de consumo se actualizan en sitio si cambian
  (sin versionar); la minuta los muestra como anterior → nuevo.
- La fuente oficial ya no usa `clase` (ni en la clave natural). La columna
  `gdd.atributo_fuente_oficial.clase` conserva sus valores históricos.
- Requiere `db/migraciones/20261005_metadata_repositorio.sql` en cada base.
  La 1.0.0 se publica como RETIRADA (cambio MAJOR, sin periodo de gracia).

### Herramientas manuales (diagnóstico / reprocesos)

```powershell
python -m gdd_loader.cli --archivo X.xlsx        # solo Excel -> staging
python -m gdd_loader.merge_cli --dominio ADS     # solo staging -> gdd
```

### Vínculos pendientes (v0.8.0, paso adicional al merge)

```powershell
python -m gdd_loader.vinculos_cli                  # todos los dominios, SIMULACIÓN
python -m gdd_loader.vinculos_cli --dominio ADS    # un dominio, SIMULACIÓN
python -m gdd_loader.vinculos_cli --aplicar        # escribe en gdd
python -m gdd_loader.vinculos_cli --ignorar-fecha  # no exige igualdad de fechas (v0.8.2)
```

Completa `id_atributo_primario` / `id_dominio_primario` de `gdd.atributo` cuando
ya existe la coincidencia (por ejemplo, el dominio del atributo primario se
cargó después). No modifica el merge. Correr después de `merge_cli`.

- Sin `--aplicar` solo informa. Con `--aplicar` escribe y deja una línea
  `ACTUALIZAR` en `gdd.bitacora_carga` (sin `id_carga`) por atributo.
- Candidatos: atributos activos con alguno de los dos campos en `NULL` y cuya
  fila de `staging.detalle_atributos` trae ambos textos. Usa la misma
  coincidencia conjunta (atributo y dominio) que el merge.
- Solo se aplica si la `fecha_aprobada` de staging es igual a la
  `fecha_aprobacion` de gdd (el merge ya consolidó esa versión).
  `--ignorar-fecha` (abreviable como `--ignorar`) salta esa comprobación: si
  staging es más viejo que gdd, aplica un vínculo de una versión anterior sobre
  una más nueva. Cada vínculo aplicado con fecha distinta se lista como
  `FECHA IGNORADA` con las dos fechas; revisar primero en simulación.
- Solo completa `NULL`: nunca pisa un valor existente (si difiere, lo informa
  como conflicto). No cambia versión, release ni fecha.
- Una transacción por dominio; idempotente. Que queden vínculos sin
  coincidencia no es un error (código de salida 0).

### Versiones de plantilla (equipo central)

Ver `db/README.md`. Resumen:

```powershell
python -m gdd_loader.plantilla_cli registrar --molde GDD_Plantilla_v2.0.0.xlsx --version 2.0.0            # simulación
python -m gdd_loader.plantilla_cli registrar --molde GDD_Plantilla_v2.0.0.xlsx --version 2.0.0 --aplicar --version-loader-minima 0.7.0  # BORRADOR
python -m gdd_loader.plantilla_cli publicar --version 2.0.0 --anterior RETIRADA                              # VIGENTE
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

## Grafo de relaciones entre dominios (v0.9.0)

Grafo HTML autocontenido (sin internet ni servidor) de los atributos declarados como
Compartido / Similar en `gdd.atributo`. **Solo lectura**: no escribe en la base ni usa staging.

1. Una sola vez, en DGODELIVERY: `db/migraciones/20261105_vistas_relacion_dominio.sql`
   (crea `gdd.v_relacion_atributo` y `gdd.v_relacion_dominio`; no toca tablas).
2. `python -m gdd_loader.grafo_cli --salida "C:\ruta\carpeta"` genera
   `grafo_relaciones_AAAAMMDD_HHMM.html`.

Flecha: el dominio de origen **usa** atributos del dominio destino (dominio primario, dueño).
Solo se dibujan los vínculos `RESUELTO` y `DESTINO_INACTIVO`; el resto (`SIN_VINCULO`,
`VINCULO_ROTO`, `INCONSISTENTE`, `MISMO_DOMINIO`, `SIN_TIPO`) aparece en la pestaña
"Calidad de vínculos". `gdd.v_relacion_dominio` sirve también como fuente para Power BI.
vis-network 9.1.9 va incrustado (`exportar/recursos`, licencia MIT).

## Sitio de consulta por dominio (v0.10.0)

HTML autocontenido (sin internet ni servidor) con menú lateral y ocho secciones.
**Solo lectura**: lee `gdd.*` (registros activos); no usa staging.

1. **Dominio y estructura**: datos del dominio, responsables (hoja Estructura) y respaldos.
2. **Atributos y fuentes**: cada atributo con sus fuentes oficiales (fórmula y valores válidos en fila
   desplegable) y sus fuentes de consumo. Botón **Descargar Excel**: `.xlsx` con los atributos visibles
   según el filtro y hojas de fuentes oficiales, fuentes de consumo y relaciones (se genera en el navegador).
3. **Explorador de atributos** (v0.12.0): atributos de todos los dominios con filtros combinables (uno o más
   dominios, criticidad, compartido/similar, estado del vínculo, tipo, dato personal, con/sin fuente oficial y
   de consumo, estructura/reporte/proyecto/proceso), búsqueda de texto (incluye tablas, campos y fórmulas),
   selector de columnas, orden por columna y descarga a Excel del resultado con sus fuentes. Desde v0.13.1,
   una columna por rol (`cat_rol`) con los responsables del dominio (una fila por atributo; varias personas
   del mismo rol en la misma celda) y búsqueda también por responsable.
4. **Investigación**: registros de la hoja Investigación (sin los términos) en tarjetas, agrupados en todas las
   secciones del catálogo `cat_clasificacion_investigacion` (incluida "Actualización de datos"; las vacías dicen
   "Sin registros"), con buscador y accesos directos a cada sección (v0.13.1).
5. **Glosario de términos**: los registros con clasificación "Término" de todos los dominios en una sola
   página (o los del dominio elegido), con buscador e índice A–Z.
6. **Relaciones entre dominios**: grafo de los atributos Compartido / Similar (completo, o el dominio
   elegido con sus vecinos), con la calidad de vínculos. Usa la vista `gdd.v_relacion_atributo`
   (migración `20261105_vistas_relacion_dominio.sql`); si no existe, el sitio se genera igual y la
   sección lo informa.
7. **Indicadores** (v0.11.0): puntaje de fuente oficial por dominio (parte de 100; cada atributo sin
   fuente oficial resta 5 si es crítico y está marcado en Estructura, 3 si solo es crítico y 1 en otro
   caso; mínimo 0), atributos críticos / no críticos con y sin fuente, estructura completa (roles 1 a 4),
   fuentes de consumo, respaldos, glosario y vínculos. Cada gráfico tiene su vista de tabla.
8. **Planes de remediación** (v0.14.0): planes activos (`gdd.plan_remediacion`). Por dominio, tabla con fila
   desplegable (causa raíz, acciones, dependencias, observación, responsable, SIRO); con "Todos", indicadores
   (total, avance promedio, finalizados, vencidos) y gráficos por estado, dimensión, dominio y avance, más la
   tabla de todos los planes. Filtros de selección múltiple y texto, y descarga a Excel. Cada plan se muestra
   con la versión vigente de su atributo; en un dominio Confidencial no se publica "Fuente oficial / sistema".

`python -m gdd_loader.sitio_cli --salida "C:\ruta\carpeta"` genera la carpeta `sitio_dominios_AAAAMMDD_HHMM\`
(v0.13.0) con `index.html` (Dominio y estructura) y una página por sección (`atributos.html`, `explorador.html`,
`investigacion.html`, `terminos.html`, `relaciones.html`, `indicadores.html`, `planes.html`). El diseño es el mismo; el menú
lateral enlaza las páginas y conserva el dominio elegido. Cada página es autocontenida y lleva solo los datos que
usa, comprimidos (gzip + base64, unas 9 veces menos); el navegador los descomprime al abrirla (Edge o Chrome
recientes). `--max-mb` (por defecto 10) avisa si alguna página supera el límite de 10 MB por archivo de SharePoint.
La carpeta trae además `sitio_completo.html` (v0.14.2): todas las secciones en un solo archivo.

**En SharePoint se publica solo `sitio_completo.html`.** El visor "Páginas HTML" de SharePoint abre cada archivo
en un iframe aislado (`sandbox="allow-scripts"`, `about:srcdoc`): no permite pasar de un archivo a otro, ni usar
el historial del navegador, ni descargar archivos. El sitio lo detecta y se adapta: navega entre secciones dentro
del mismo archivo y el botón de Excel abre "Copiar para Excel" (la hoja completa al portapapeles, con los textos
marcados como texto para que Excel no los cambie). Fuera de SharePoint (carpeta local o unidad compartida) se
usa la carpeta completa desde `index.html` y el Excel se descarga como `.xlsx`.
Con `--salida sitio.html` se genera un solo archivo con todas las secciones, como antes.
Los archivos contienen valores reales, incluidos los nombres de los responsables: compartirlo solo con
el equipo de gobierno de datos.

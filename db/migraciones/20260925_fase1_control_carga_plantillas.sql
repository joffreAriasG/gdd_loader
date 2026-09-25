/* ============================================================================
   Fase 1 -- Control de cargas y registro de versiones de plantilla
   Base: DGODELIVERY   Esquemas: gdd, staging
   Fecha: 2026-09-25

   QUE HACE (solo cambios ADITIVOS, no borra ni modifica datos existentes):
     1. gdd.plantilla / gdd.plantilla_version / gdd.plantilla_version_columna
        Registro central de versiones de la plantilla Excel y su contrato
        estructural (hojas + encabezados). De aqui sale la "huella"
        (hash_estructura) que el loader compara contra cada archivo.
     2. gdd.carga_control / gdd.carga_control_detalle
        Un registro por archivo procesado (id_carga), con hash, version de
        plantilla, version del loader, estado y conteos por hoja/tabla.
     3. gdd.bitacora_carga.id_carga (nullable, FK)
        Enlaza cada linea de bitacora con la carga que la produjo.
     4. staging.<tabla>.id_carga (nullable, sin FK)
        Identifica que carga dejo cada fila en staging.

   ORDEN DE DESPLIEGUE: correr este script ANTES de usar
   `python -m gdd_loader.procesar_cli`. Los CLIs anteriores (cli / merge_cli)
   siguen funcionando con o sin este script.

   IDEMPOTENTE: se puede correr mas de una vez sin error. Cada bloque va
   protegido con IF NOT EXISTS / COL_LENGTH, separado por GO (correr en SSMS
   o sqlcmd). Si un bloque falla, corregir y volver a correr el script completo.

   PERMISOS: la cuenta con la que corre gdd_loader necesita SELECT/INSERT/
   UPDATE sobre gdd.carga_control, gdd.carga_control_detalle y SELECT sobre
   gdd.plantilla_version / gdd.plantilla_version_columna. Solo el equipo
   central de plantillas deberia tener INSERT/UPDATE sobre el registro de
   versiones.

   ROLLBACK (si hiciera falta, en este orden):
     ALTER TABLE gdd.bitacora_carga DROP CONSTRAINT fk_bitacora_carga_control;
     ALTER TABLE gdd.bitacora_carga DROP COLUMN id_carga;
     ALTER TABLE staging.<tabla> DROP COLUMN id_carga;   -- las 6 tablas
     DROP TABLE gdd.carga_control_detalle; DROP TABLE gdd.carga_control;
     DROP TABLE gdd.plantilla_version_columna; DROP TABLE gdd.plantilla_version;
     DROP TABLE gdd.plantilla;
   ========================================================================== */

SET NOCOUNT ON;
GO

/* ---------------------------------------------------------------------------
   1. Registro de versiones de plantilla
   ------------------------------------------------------------------------- */
IF OBJECT_ID('gdd.plantilla', 'U') IS NULL
BEGIN
    CREATE TABLE gdd.plantilla (
        id_plantilla  varchar(30)   NOT NULL CONSTRAINT pk_plantilla PRIMARY KEY,
        nombre        varchar(100)  NOT NULL,
        descripcion   nvarchar(500) NULL
    );
END;
GO

IF NOT EXISTS (SELECT 1 FROM gdd.plantilla WHERE id_plantilla = 'GDD-DOMINIO')
    INSERT INTO gdd.plantilla (id_plantilla, nombre, descripcion)
    VALUES ('GDD-DOMINIO', 'Plantilla de dominio de datos',
            'Plantilla Excel por dominio: DetalleAtributos, MetadataTecnica, Investigacion, Estructura, Respaldos, PlanDeRemediacion');
GO

IF OBJECT_ID('gdd.plantilla_version', 'U') IS NULL
BEGIN
    CREATE TABLE gdd.plantilla_version (
        id_plantilla          varchar(30)    NOT NULL,
        version               varchar(20)    NOT NULL,   -- semver MAJOR.MINOR.PATCH
        estado                varchar(15)    NOT NULL,
        hash_estructura       char(64)       NOT NULL,   -- SHA-256 de hojas+encabezados del contrato
        version_loader_minima varchar(20)    NOT NULL,   -- version minima de gdd_loader que la soporta
        fecha_registro        datetime2(0)   NOT NULL CONSTRAINT df_pv_fecha_registro DEFAULT SYSDATETIME(),
        fecha_publicacion     datetime2(0)   NULL,
        fecha_fin_gracia      date           NULL,       -- solo aplica a DEPRECADA
        notas_cambio          nvarchar(max)  NULL,
        registrado_por        varchar(100)   NULL,
        CONSTRAINT pk_plantilla_version PRIMARY KEY (id_plantilla, version),
        CONSTRAINT fk_pv_plantilla FOREIGN KEY (id_plantilla) REFERENCES gdd.plantilla (id_plantilla),
        CONSTRAINT ck_pv_estado CHECK (estado IN ('BORRADOR', 'VIGENTE', 'DEPRECADA', 'RETIRADA'))
    );
END;
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes
               WHERE name = 'ux_pv_una_vigente' AND object_id = OBJECT_ID('gdd.plantilla_version'))
    CREATE UNIQUE INDEX ux_pv_una_vigente
        ON gdd.plantilla_version (id_plantilla) WHERE estado = 'VIGENTE';

IF NOT EXISTS (SELECT 1 FROM sys.indexes
               WHERE name = 'ix_pv_hash' AND object_id = OBJECT_ID('gdd.plantilla_version'))
    CREATE INDEX ix_pv_hash ON gdd.plantilla_version (id_plantilla, hash_estructura);

IF OBJECT_ID('gdd.plantilla_version_columna', 'U') IS NULL
BEGIN
    CREATE TABLE gdd.plantilla_version_columna (
        id_plantilla  varchar(30)   NOT NULL,
        version       varchar(20)   NOT NULL,
        hoja          nvarchar(100) NOT NULL,
        orden         smallint      NOT NULL,   -- 1..n, orden de la columna en la hoja
        columna       nvarchar(128) NOT NULL,
        CONSTRAINT pk_pvc PRIMARY KEY (id_plantilla, version, hoja, orden),
        CONSTRAINT fk_pvc_version FOREIGN KEY (id_plantilla, version)
            REFERENCES gdd.plantilla_version (id_plantilla, version)
    );
END;

/* ---------------------------------------------------------------------------
   2. Control de cargas
   ------------------------------------------------------------------------- */
IF OBJECT_ID('gdd.carga_control', 'U') IS NULL
BEGIN
    CREATE TABLE gdd.carga_control (
        id_carga                  bigint IDENTITY(1,1) NOT NULL CONSTRAINT pk_carga_control PRIMARY KEY,
        codigo_dominio            varchar(20)    NULL,   -- NULL si se rechazo antes de identificarlo
        nombre_archivo_original   nvarchar(260)  NOT NULL,
        ruta_archivo_archivado    nvarchar(1000) NULL,
        hash_archivo              char(64)       NOT NULL,
        tamano_bytes              bigint         NULL,
        id_plantilla              varchar(30)    NULL,
        version_plantilla         varchar(20)    NULL,
        origen_version            varchar(10)    NULL,   -- CONTROL (hoja _Control) | HUELLA (transicion)
        hash_estructura_calculado char(64)       NULL,
        id_carga_base             bigint         NULL,   -- Fase 2 (plantilla precargada)
        id_envio                  varchar(36)    NULL,   -- Fase 2 (GUID del envio)
        version_loader            varchar(20)    NOT NULL,
        equipo                    varchar(100)   NOT NULL,
        usuario_ejecucion         varchar(100)   NOT NULL,
        subido_por                nvarchar(200)  NULL,   -- informativo, no es control de acceso
        origen_subido_por         varchar(20)    NULL,   -- DOCPROPS | POWER_AUTOMATE (Fase 3)
        estado                    varchar(30)    NOT NULL,
        fecha_inicio              datetime2(0)   NOT NULL CONSTRAINT df_cc_fecha_inicio DEFAULT SYSDATETIME(),
        fecha_fin                 datetime2(0)   NULL,
        mensaje                   nvarchar(max)  NULL,
        fecha_purga_archivo       datetime2(0)   NULL,
        CONSTRAINT ck_cc_estado CHECK (estado IN (
            'EN_PROCESO',
            'RECHAZADA_SIN_CONTROL', 'RECHAZADA_VERSION', 'RECHAZADA_ESTRUCTURA',
            'RECHAZADA_LOADER', 'RECHAZADA_DOMINIO',
            'OMITIDA_SIN_CAMBIOS',
            'ERROR_STAGING', 'ERROR_MERGE', 'MERGE_PARCIAL', 'MERGE_OK',
            'ERROR')),
        CONSTRAINT ck_cc_origen_version CHECK (origen_version IS NULL OR origen_version IN ('CONTROL', 'HUELLA')),
        CONSTRAINT ck_cc_origen_subido CHECK (origen_subido_por IS NULL OR origen_subido_por IN ('DOCPROPS', 'POWER_AUTOMATE'))
    );
END;
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes
               WHERE name = 'ix_cc_dominio_estado' AND object_id = OBJECT_ID('gdd.carga_control'))
    CREATE INDEX ix_cc_dominio_estado
        ON gdd.carga_control (codigo_dominio, estado, id_carga DESC) INCLUDE (hash_archivo);

IF NOT EXISTS (SELECT 1 FROM sys.indexes
               WHERE name = 'ix_cc_purga' AND object_id = OBJECT_ID('gdd.carga_control'))
    CREATE INDEX ix_cc_purga
        ON gdd.carga_control (fecha_inicio) WHERE fecha_purga_archivo IS NULL;

IF OBJECT_ID('gdd.carga_control_detalle', 'U') IS NULL
BEGIN
    CREATE TABLE gdd.carga_control_detalle (
        id_carga      bigint        NOT NULL,
        etapa         varchar(10)   NOT NULL,   -- STAGING | MERGE
        objeto        varchar(60)   NOT NULL,   -- hoja (STAGING) o tabla gdd (MERGE)
        filas         int           NOT NULL CONSTRAINT df_ccd_filas        DEFAULT 0,
        insertadas    int           NOT NULL CONSTRAINT df_ccd_insertadas   DEFAULT 0,
        actualizadas  int           NOT NULL CONSTRAINT df_ccd_actualizadas DEFAULT 0,
        reemplazadas  int           NOT NULL CONSTRAINT df_ccd_reemplazadas DEFAULT 0,
        eliminadas    int           NOT NULL CONSTRAINT df_ccd_eliminadas   DEFAULT 0,
        omitidas      int           NOT NULL CONSTRAINT df_ccd_omitidas     DEFAULT 0,
        sin_cambios   int           NOT NULL CONSTRAINT df_ccd_sin_cambios  DEFAULT 0,
        error         nvarchar(max) NULL,
        CONSTRAINT pk_carga_control_detalle PRIMARY KEY (id_carga, etapa, objeto),
        CONSTRAINT fk_ccd_carga FOREIGN KEY (id_carga) REFERENCES gdd.carga_control (id_carga),
        CONSTRAINT ck_ccd_etapa CHECK (etapa IN ('STAGING', 'MERGE'))
    );
END;

/* ---------------------------------------------------------------------------
   3. Enlace bitacora -> carga
   ------------------------------------------------------------------------- */
IF COL_LENGTH('gdd.bitacora_carga', 'id_carga') IS NULL
    ALTER TABLE gdd.bitacora_carga ADD id_carga bigint NULL;

GO

-- FK en lote aparte: SQL Server valida la columna recien agregada al compilar.
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'fk_bitacora_carga_control')
    ALTER TABLE gdd.bitacora_carga
        ADD CONSTRAINT fk_bitacora_carga_control FOREIGN KEY (id_carga)
            REFERENCES gdd.carga_control (id_carga);
GO

/* ---------------------------------------------------------------------------
   4. id_carga en staging (sin FK: staging se mantiene como espejo simple)
   ------------------------------------------------------------------------- */
IF COL_LENGTH('staging.detalle_atributos', 'id_carga') IS NULL
    ALTER TABLE staging.detalle_atributos ADD id_carga bigint NULL;
IF COL_LENGTH('staging.metadata_tecnica', 'id_carga') IS NULL
    ALTER TABLE staging.metadata_tecnica ADD id_carga bigint NULL;
IF COL_LENGTH('staging.investigacion', 'id_carga') IS NULL
    ALTER TABLE staging.investigacion ADD id_carga bigint NULL;
IF COL_LENGTH('staging.estructura', 'id_carga') IS NULL
    ALTER TABLE staging.estructura ADD id_carga bigint NULL;
IF COL_LENGTH('staging.respaldos', 'id_carga') IS NULL
    ALTER TABLE staging.respaldos ADD id_carga bigint NULL;
IF COL_LENGTH('staging.plan_remediacion', 'id_carga') IS NULL
    ALTER TABLE staging.plan_remediacion ADD id_carga bigint NULL;
GO

/* ---------------------------------------------------------------------------
   Verificacion (solo lectura) -- todas las filas deben decir OK
   ------------------------------------------------------------------------- */
SELECT objeto, CASE WHEN existe = 1 THEN 'OK' ELSE 'FALTA' END AS estado
FROM (VALUES
    ('gdd.plantilla',                     CASE WHEN OBJECT_ID('gdd.plantilla','U') IS NOT NULL THEN 1 ELSE 0 END),
    ('gdd.plantilla_version',             CASE WHEN OBJECT_ID('gdd.plantilla_version','U') IS NOT NULL THEN 1 ELSE 0 END),
    ('gdd.plantilla_version_columna',     CASE WHEN OBJECT_ID('gdd.plantilla_version_columna','U') IS NOT NULL THEN 1 ELSE 0 END),
    ('gdd.carga_control',                 CASE WHEN OBJECT_ID('gdd.carga_control','U') IS NOT NULL THEN 1 ELSE 0 END),
    ('gdd.carga_control_detalle',         CASE WHEN OBJECT_ID('gdd.carga_control_detalle','U') IS NOT NULL THEN 1 ELSE 0 END),
    ('gdd.bitacora_carga.id_carga',       CASE WHEN COL_LENGTH('gdd.bitacora_carga','id_carga') IS NOT NULL THEN 1 ELSE 0 END),
    ('staging.detalle_atributos.id_carga',CASE WHEN COL_LENGTH('staging.detalle_atributos','id_carga') IS NOT NULL THEN 1 ELSE 0 END),
    ('staging.metadata_tecnica.id_carga', CASE WHEN COL_LENGTH('staging.metadata_tecnica','id_carga') IS NOT NULL THEN 1 ELSE 0 END),
    ('staging.investigacion.id_carga',    CASE WHEN COL_LENGTH('staging.investigacion','id_carga') IS NOT NULL THEN 1 ELSE 0 END),
    ('staging.estructura.id_carga',       CASE WHEN COL_LENGTH('staging.estructura','id_carga') IS NOT NULL THEN 1 ELSE 0 END),
    ('staging.respaldos.id_carga',        CASE WHEN COL_LENGTH('staging.respaldos','id_carga') IS NOT NULL THEN 1 ELSE 0 END),
    ('staging.plan_remediacion.id_carga', CASE WHEN COL_LENGTH('staging.plan_remediacion','id_carga') IS NOT NULL THEN 1 ELSE 0 END),
    ('seed GDD-DOMINIO',                  CASE WHEN EXISTS (SELECT 1 FROM gdd.plantilla WHERE id_plantilla='GDD-DOMINIO') THEN 1 ELSE 0 END)
) v(objeto, existe);

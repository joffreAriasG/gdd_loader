/* =============================================================================
   20261105_vistas_relacion_dominio.sql   (gdd_loader v0.9.0)

   Vistas de SOLO LECTURA para graficar las relaciones entre dominios a partir
   de los atributos declarados como Compartido / Similar en gdd.atributo.

     gdd.v_relacion_atributo  -> una fila por atributo ACTIVO que declara una
                                 relacion (id_compartido_similar o
                                 id_atributo_primario informado), con su
                                 destino y un estado de calidad del vinculo.
     gdd.v_relacion_dominio   -> agregado dominio -> dominio por tipo de
                                 relacion (aristas del grafo). Pensada tambien
                                 como fuente para Power BI.

   Cambio NO destructivo: solo crea / reemplaza vistas. No toca tablas ni datos.
   Idempotente: se puede correr mas de una vez (crea la vista si no existe y
   luego la redefine con ALTER). Compatible con SQL Server 2008 o superior
   (no usa CREATE OR ALTER).
   Rollback: 20261105_rollback_vistas_relacion_dominio.sql

   Semantica de la arista: dominio_origen (el que USA el atributo) ->
   dominio_destino (dominio_primario, el DUENO del atributo).

   estado_vinculo:
     RESUELTO          vinculo completo y el atributo primario esta activo.
     DESTINO_INACTIVO  apunta a una version dada de baja del atributo primario
                       (p. ej. el primario tuvo REEMPLAZAR despues). El dominio
                       destino sigue siendo valido para el grafo.
     MISMO_DOMINIO     el primario esta en el mismo dominio (no es arista entre
                       dominios; informativo).
     SIN_VINCULO       declara Compartido/Similar pero id_atributo_primario o
                       id_dominio_primario estan en NULL (no se encontro la
                       coincidencia atributo + dominio al cargar).
     VINCULO_ROTO      el id guardado no existe en gdd.atributo o
                       gdd.dominio_dato (estas columnas no tienen FK).
     INCONSISTENTE     el atributo primario pertenece a un dominio distinto de
                       id_dominio_primario.
     SIN_TIPO          tiene vinculo pero id_compartido_similar es NULL.
   ============================================================================= */

USE DGODELIVERY;
GO

IF OBJECT_ID(N'gdd.v_relacion_atributo', N'V') IS NULL
    EXEC (N'CREATE VIEW gdd.v_relacion_atributo AS SELECT 1 AS marcador');
GO

ALTER VIEW gdd.v_relacion_atributo
AS
SELECT
    a.id                            AS id_atributo_origen,
    a.codigo_atributo               AS codigo_atributo_origen,
    a.nombre_atributo               AS nombre_atributo_origen,
    d_o.id                          AS id_dominio_origen,
    d_o.codigo_dominio              AS codigo_dominio_origen,
    d_o.nombre_dominio              AS nombre_dominio_origen,
    cs.nombre_compartido_similar    AS tipo_relacion,
    a.id_atributo_primario          AS id_atributo_destino,
    ap.nombre_atributo              AS nombre_atributo_destino,
    ap.activo                       AS atributo_destino_activo,
    a.id_dominio_primario           AS id_dominio_destino,
    d_p.codigo_dominio              AS codigo_dominio_destino,
    d_p.nombre_dominio              AS nombre_dominio_destino,
    CASE
        WHEN a.id_atributo_primario IS NULL OR a.id_dominio_primario IS NULL THEN 'SIN_VINCULO'
        WHEN ap.id IS NULL OR d_p.id IS NULL                                  THEN 'VINCULO_ROTO'
        WHEN ap.id_dominio <> a.id_dominio_primario                           THEN 'INCONSISTENTE'
        WHEN a.id_dominio_primario = a.id_dominio                             THEN 'MISMO_DOMINIO'
        WHEN a.id_compartido_similar IS NULL                                  THEN 'SIN_TIPO'
        WHEN ap.activo = 0                                                    THEN 'DESTINO_INACTIVO'
        ELSE 'RESUELTO'
    END                             AS estado_vinculo
FROM gdd.atributo a
JOIN gdd.dominio_dato d_o              ON d_o.id = a.id_dominio
LEFT JOIN gdd.cat_compartido_similar cs ON cs.id = a.id_compartido_similar
LEFT JOIN gdd.atributo ap              ON ap.id = a.id_atributo_primario
LEFT JOIN gdd.dominio_dato d_p         ON d_p.id = a.id_dominio_primario
WHERE a.activo = 1
  AND (a.id_compartido_similar IS NOT NULL OR a.id_atributo_primario IS NOT NULL);
GO

IF OBJECT_ID(N'gdd.v_relacion_dominio', N'V') IS NULL
    EXEC (N'CREATE VIEW gdd.v_relacion_dominio AS SELECT 1 AS marcador');
GO

ALTER VIEW gdd.v_relacion_dominio
AS
SELECT
    id_dominio_origen,
    codigo_dominio_origen,
    nombre_dominio_origen,
    id_dominio_destino,
    codigo_dominio_destino,
    nombre_dominio_destino,
    tipo_relacion,
    COUNT(*)                                                         AS n_atributos,
    SUM(CASE WHEN estado_vinculo = 'DESTINO_INACTIVO' THEN 1 ELSE 0 END) AS n_destino_inactivo
FROM gdd.v_relacion_atributo
WHERE estado_vinculo IN ('RESUELTO', 'DESTINO_INACTIVO')
GROUP BY id_dominio_origen, codigo_dominio_origen, nombre_dominio_origen,
         id_dominio_destino, codigo_dominio_destino, nombre_dominio_destino,
         tipo_relacion;
GO

/* ---------------------------------------------------------------------------
   Verificacion (solo lectura). Esperado en produccion: la suma de
   n_atributos por estado debe cuadrar con los ~1017 atributos relacionados.
   --------------------------------------------------------------------------- */
SELECT estado_vinculo, tipo_relacion, COUNT(*) AS n_atributos
FROM gdd.v_relacion_atributo
GROUP BY estado_vinculo, tipo_relacion
ORDER BY estado_vinculo, tipo_relacion;

SELECT COUNT(*) AS n_aristas_dominio, SUM(n_atributos) AS n_atributos_en_aristas
FROM gdd.v_relacion_dominio;

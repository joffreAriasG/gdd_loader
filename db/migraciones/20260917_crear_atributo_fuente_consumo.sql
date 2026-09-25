-- Crea gdd.atributo_fuente_consumo: trazabilidad de CONSUMO de un atributo
-- (en que tabla/campo de negocio de BI/reportes se consulta, luego de la
-- transformacion de carga a consumo) -- alcance ampliado 2026-09-17
-- (columnas _foc de MetadataTecnica: clase_foc, servidor_foc, tabla_bv_foc,
-- nombre_campo_foc), hasta ahora sin tabla destino propia.
--
-- Confirmado con el usuario: NO es "otra fuente mas" como
-- gdd.atributo_fuente_oficial (que registra de DONDE se CARGA el atributo);
-- es el extremo opuesto de la trazabilidad (zona de carga -> zona de
-- consumo). id_servidor reutiliza el MISMO catalogo gdd.servidor_fuente que
-- ya usa atributo_fuente_oficial (decision del usuario: son los mismos
-- servidores fisicos). `clase` ("clase o coleccion donde se aloja la
-- tabla") queda como texto libre por ahora -- sin datos reales disponibles
-- para saber si hace falta un catalogo controlado.
--
-- Cambio ADITIVO -- no modifica ninguna tabla existente, no hay riesgo de
-- perdida de datos. Rollback (mientras la tabla siga vacia, o su contenido
-- no haga falta conservar): DROP TABLE DGODELIVERY.gdd.atributo_fuente_consumo;
--
-- Requisito de codigo: correr esto ANTES de desplegar los .py actualizados
-- (gdd_mapping.py, gdd_repository.py, carga_gdd.py) -- el merge falla si
-- intenta escribir en una tabla que todavia no existe.

CREATE TABLE DGODELIVERY.gdd.atributo_fuente_consumo (
    id           bigint IDENTITY(1,1) NOT NULL,
    id_atributo  bigint NOT NULL,
    id_servidor  bigint NOT NULL,
    clase        varchar(255) COLLATE Latin1_General_CI_AI NULL,
    nombre_tabla varchar(255) COLLATE Latin1_General_CI_AI NOT NULL,
    nombre_campo varchar(255) COLLATE Latin1_General_CI_AI NOT NULL,
    activo       bit NOT NULL CONSTRAINT df_atributo_fuente_consumo_activo DEFAULT (1),
    fecha_baja   date NULL,
    CONSTRAINT pk_atributo_fuente_consumo PRIMARY KEY (id),
    CONSTRAINT fk_afc_atributo FOREIGN KEY (id_atributo) REFERENCES DGODELIVERY.gdd.atributo(id),
    CONSTRAINT fk_afc_servidor FOREIGN KEY (id_servidor) REFERENCES DGODELIVERY.gdd.servidor_fuente(id)
);

-- Verificacion rapida despues de correr esto:
-- SELECT COUNT(*) FROM DGODELIVERY.gdd.atributo_fuente_consumo;  -- debe dar 0

-- Rollback (solo si la tabla esta vacia o su contenido no hace falta conservar):
-- DROP TABLE DGODELIVERY.gdd.atributo_fuente_consumo;

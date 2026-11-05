"""
Diagnostico de la plantilla precargada (SOLO LECTURA: no escribe en la base ni
genera archivos).

Responde a: "generar_cli no llena la hoja MetadataRepositorio de un dominio".
Reproduce, paso a paso, la misma decision que toma exportar/generador.py:

  1. version VIGENTE de la plantilla y sus hojas;
  2. ultima carga MERGE_OK del dominio (id, version de plantilla, archivo);
  3. fuente ARCHIVO: el .xlsx archivado, sus hojas y filas de MetadataRepositorio;
  4. fuente STAGING (solo si el archivo no sirve): filas por id_carga;
  5. lo que hay en gdd.atributo_fuente_consumo para el dominio;
  6. veredicto: de donde saldrian los datos y por que la hoja saldria vacia.

Uso (desde la carpeta del proyecto, con el entorno activado):
    python scripts/diagnostico_generacion.py CND
"""

from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import text

from gdd_loader.config import cargar_settings
from gdd_loader.domain.sheet_config import HOJAS_ADS
from gdd_loader.exportar.fuente_datos import (
    FuenteNoDisponibleError,
    datos_desde_archivo,
    datos_desde_staging,
)
from gdd_loader.load.control_repository import ControlRepository
from gdd_loader.load.db import construir_engine
from gdd_loader.load.staging_repository import StagingRepository

HOJA = "MetadataRepositorio"


def resumen_filas(datos: dict[str, list[dict]]) -> str:
    return ", ".join(f"{h}={len(f)}" for h, f in datos.items())


def contar_staging(engine, tabla: str, columna_clave: str, prefijo: str) -> list[tuple]:
    try:
        with engine.connect() as conn:
            return [tuple(f) for f in conn.execute(
                text(f"SELECT id_carga, COUNT(*) FROM {tabla} WHERE {columna_clave} LIKE :p "
                     "GROUP BY id_carga ORDER BY id_carga"), {"p": f"{prefijo}-%"}).all()]
    except Exception as exc:  # noqa: BLE001 - es un diagnostico: se informa y se sigue
        return [("ERROR", str(exc)[:160])]


def main() -> int:
    if len(sys.argv) != 2:
        print("Uso: python scripts/diagnostico_generacion.py <CODIGO_DOMINIO>")
        return 1
    dominio = sys.argv[1].strip().upper()
    settings = cargar_settings(None)
    settings.validar()
    engine = construir_engine(settings)
    control = ControlRepository(engine)
    staging = StagingRepository(engine)
    veredicto: list[str] = []

    print(f"== Diagnostico de generacion para {dominio} ==")

    # 1) version vigente
    vigente = next((v for v in control.versiones(settings.id_plantilla) if v.estado == "VIGENTE"), None)
    if vigente is None:
        print(f"1) No hay version VIGENTE de {settings.id_plantilla}.")
        return 1
    hojas_contrato = list(vigente.columnas)
    print(f"1) Version vigente: {vigente.version}. Hojas del contrato: {', '.join(hojas_contrato)}")
    if HOJA not in hojas_contrato:
        veredicto.append(f"La version vigente ({vigente.version}) NO incluye la hoja {HOJA}.")

    # 2) ultima carga OK
    ultima = control.ultima_carga_ok(dominio)
    if ultima is None:
        print(f"2) {dominio} no tiene ninguna carga MERGE_OK: el generador no puede generar.")
        return 1
    with engine.connect() as conn:
        fila = conn.execute(text(
            "SELECT estado, version_plantilla, fecha_inicio FROM gdd.carga_control WHERE id_carga = :i"),
            {"i": ultima.id_carga}).first()
    version_carga = fila[1] if fila else "?"
    print(f"2) Ultima MERGE_OK: id_carga={ultima.id_carga}, version_plantilla={version_carga}, "
          f"fecha_inicio={fila[2] if fila else '?'}")
    print(f"   archivo archivado: {ultima.ruta_archivo or '(sin ruta)'}")
    if version_carga != vigente.version:
        veredicto.append(
            f"La ultima carga OK se hizo con plantilla {version_carga} y la vigente es {vigente.version}: "
            "esa carga puede no traer la hoja MetadataRepositorio.")

    # 3) fuente ARCHIVO
    print("3) Fuente ARCHIVO:")
    fuente = None
    try:
        if not ultima.ruta_archivo:
            raise FuenteNoDisponibleError("la carga no tiene archivo archivado")
        datos = datos_desde_archivo(Path(ultima.ruta_archivo), hojas_contrato)
        fuente = "ARCHIVO"
        print(f"   OK -> filas por hoja: {resumen_filas(datos)}")
    except FuenteNoDisponibleError as exc:
        print(f"   NO sirve: {exc}")
        veredicto.append(f"El archivo no sirve como fuente ({exc}); el generador cae a staging.")

    # 4) fuente STAGING
    if fuente is None:
        print("4) Fuente STAGING (la que usa el generador cuando el archivo no sirve):")
        print(f"   {HOJA}: filas por id_carga = "
              f"{contar_staging(engine, 'staging.metadata_repositorio', 'codigo_dominio_atributo', dominio)}")
        print(f"   MetadataTecnica: filas por id_carga = "
              f"{contar_staging(engine, 'staging.metadata_tecnica', 'codigo_dominio_atributo', dominio)}")
        try:
            datos = datos_desde_staging(
                staging, [c for c in HOJAS_ADS if c.nombre_hoja in hojas_contrato], dominio, ultima.id_carga)
            fuente = "STAGING"
            print(f"   OK -> filas por hoja: {resumen_filas(datos)}")
        except FuenteNoDisponibleError as exc:
            print(f"   NO sirve: {exc}")
            veredicto.append(f"Staging tampoco sirve ({exc}): el generador falla con error, no con hoja vacia.")
            datos = {}

    # 5) gdd
    with engine.connect() as conn:
        total, sin_clase, sin_fecha = conn.execute(text(
            "SELECT COUNT(*), SUM(CASE WHEN c.clase IS NULL THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN c.fecha_aprobacion IS NULL THEN 1 ELSE 0 END) "
            "FROM gdd.atributo_fuente_consumo c JOIN gdd.atributo a ON a.id = c.id_atributo "
            "WHERE a.codigo_dominio = :d AND a.activo = 1 AND c.activo = 1"), {"d": dominio}).one()
    print(f"5) gdd.atributo_fuente_consumo activos de {dominio}: {total} "
          f"(sin clase: {sin_clase or 0}, sin fecha: {sin_fecha or 0})")

    # 6) veredicto
    n_hoja = len(datos.get(HOJA, [])) if datos else 0
    print("\n== Veredicto ==")
    print(f"Fuente que usaria el generador: {fuente or 'NINGUNA'}; filas de {HOJA}: {n_hoja}; "
          f"consumos activos en gdd: {total}")
    if n_hoja == 0 and total:
        print(f"-> La hoja {HOJA} saldria VACIA aunque gdd tiene {total} consumo(s) activo(s) del dominio.")
        print("   El generador no lee gdd.*: solo el archivo o el staging de la ultima carga OK.")
    for v in veredicto:
        print(f"- {v}")
    if n_hoja == 0 and total:
        print("Siguiente paso posible: volver a cargar el dominio con la plantilla vigente (hoja "
              f"{HOJA} llena) o completar staging.{HOJA.lower()} para esa carga.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

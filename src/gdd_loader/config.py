"""
Capa de configuracion.

Ningun otro modulo del proyecto lee variables de entorno directamente:
todos reciben un objeto Settings ya construido. Esto es lo que permite
correr los tests sin depender de un .env real, y cambiar de servidor o
de estrategia de carga sin tocar codigo.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    carpeta_origen: Path
    sql_servidor: str
    sql_base_datos: str
    sql_driver: str
    sql_auth_modo: str  # "WINDOWS" o "SQL"
    sql_usuario: str
    sql_password: str
    estrategia_staging: str  # "TRUNCATE" o "DOMINIO"
    log_dir: Path
    log_level: str
    # --- Fase 1: control de cargas (solo los usa procesar_cli) ---
    carpeta_archivo: Path | None = None
    carpeta_rechazados: Path | None = None
    id_plantilla: str = "GDD-DOMINIO"
    exigir_control: bool = False  # False = transicion (acepta sin _Control si la huella coincide)
    segundos_estabilidad: int = 60
    dias_retencion: int = 365

    def validar_procesamiento(self) -> None:
        """Validaciones adicionales para procesar_cli (control de cargas).
        Los CLIs anteriores (cli/merge_cli) no las requieren."""
        errores = []
        if self.estrategia_staging != "DOMINIO":
            errores.append(
                "procesar_cli requiere GDD_ESTRATEGIA_STAGING=DOMINIO (TRUNCATE borraria "
                "otros dominios en una carga normal)"
            )
        carpetas = {
            "GDD_CARPETA_ORIGEN": self.carpeta_origen,
            "GDD_CARPETA_ARCHIVO": self.carpeta_archivo,
            "GDD_CARPETA_RECHAZADOS": self.carpeta_rechazados,
        }
        for nombre, carpeta in carpetas.items():
            if carpeta is None or str(carpeta) in ("", "."):
                errores.append(f"{nombre} no esta configurada")
        definidas = [c.resolve() for c in carpetas.values() if c is not None and str(c) not in ("", ".")]
        if len(set(definidas)) != len(definidas):
            errores.append("Las carpetas de origen, archivo y rechazados deben ser distintas")
        if self.segundos_estabilidad < 0:
            errores.append("GDD_SEGUNDOS_ESTABILIDAD no puede ser negativo")
        if self.dias_retencion < 0:
            errores.append("GDD_DIAS_RETENCION no puede ser negativo")
        if errores:
            raise ValueError("Configuracion invalida para procesar_cli:\n- " + "\n- ".join(errores))

    def validar(self) -> None:
        errores = []
        if self.sql_auth_modo not in ("WINDOWS", "SQL"):
            errores.append(
                f"GDD_SQL_AUTH_MODO invalido: '{self.sql_auth_modo}' (debe ser WINDOWS o SQL)"
            )
        if self.sql_auth_modo == "SQL" and not (self.sql_usuario and self.sql_password):
            errores.append("GDD_SQL_AUTH_MODO=SQL requiere GDD_SQL_USUARIO y GDD_SQL_PASSWORD")
        if not self.sql_servidor or self.sql_servidor == "TU_SERVIDOR_ONPREM":
            errores.append("GDD_SQL_SERVIDOR no esta configurado")
        if self.estrategia_staging not in ("TRUNCATE", "DOMINIO"):
            errores.append(
                f"GDD_ESTRATEGIA_STAGING invalido: '{self.estrategia_staging}' "
                "(debe ser TRUNCATE o DOMINIO)"
            )
        if errores:
            raise ValueError("Configuracion invalida:\n- " + "\n- ".join(errores))


def _ruta_opcional(variable: str) -> Path | None:
    valor = os.environ.get(variable, "").strip()
    return Path(valor) if valor else None


def cargar_settings(env_file: str | Path | None = None) -> Settings:
    """Lee el .env (si existe) y las variables de entorno, y arma Settings."""
    if env_file is not None:
        load_dotenv(env_file, override=True)
    else:
        load_dotenv(override=False)  # busca ".env" en el directorio actual

    settings = Settings(
        carpeta_origen=Path(os.environ.get("GDD_CARPETA_ORIGEN", "")),
        sql_servidor=os.environ.get("GDD_SQL_SERVIDOR", ""),
        sql_base_datos=os.environ.get("GDD_SQL_BASE_DATOS", "DGODELIVERY"),
        sql_driver=os.environ.get("GDD_SQL_DRIVER", "ODBC Driver 17 for SQL Server"),
        sql_auth_modo=os.environ.get("GDD_SQL_AUTH_MODO", "WINDOWS").upper(),
        sql_usuario=os.environ.get("GDD_SQL_USUARIO", ""),
        sql_password=os.environ.get("GDD_SQL_PASSWORD", ""),
        estrategia_staging=os.environ.get("GDD_ESTRATEGIA_STAGING", "DOMINIO").upper(),
        log_dir=Path(os.environ.get("GDD_LOG_DIR", "logs")),
        log_level=os.environ.get("GDD_LOG_LEVEL", "INFO").upper(),
        carpeta_archivo=_ruta_opcional("GDD_CARPETA_ARCHIVO"),
        carpeta_rechazados=_ruta_opcional("GDD_CARPETA_RECHAZADOS"),
        id_plantilla=os.environ.get("GDD_ID_PLANTILLA", "GDD-DOMINIO").strip(),
        exigir_control=os.environ.get("GDD_EXIGIR_CONTROL", "NO").strip().upper() in ("SI", "SÍ", "TRUE", "1"),
        segundos_estabilidad=int(os.environ.get("GDD_SEGUNDOS_ESTABILIDAD", "60")),
        dias_retencion=int(os.environ.get("GDD_DIAS_RETENCION", "365")),
    )
    return settings

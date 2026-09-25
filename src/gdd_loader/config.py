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
    )
    return settings

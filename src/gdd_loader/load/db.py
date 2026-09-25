"""Fabrica del engine de SQLAlchemy hacia SQL Server."""

from __future__ import annotations

from urllib.parse import quote_plus

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from gdd_loader.config import Settings


def construir_engine(settings: Settings) -> Engine:
    driver = quote_plus(settings.sql_driver)

    if settings.sql_auth_modo == "WINDOWS":
        conn_str = (
            f"mssql+pyodbc://@{settings.sql_servidor}/{settings.sql_base_datos}"
            f"?driver={driver}&trusted_connection=yes"
        )
    else:  # "SQL"
        usuario = quote_plus(settings.sql_usuario)
        password = quote_plus(settings.sql_password)
        conn_str = (
            f"mssql+pyodbc://{usuario}:{password}@{settings.sql_servidor}"
            f"/{settings.sql_base_datos}?driver={driver}"
        )

    return create_engine(conn_str, fast_executemany=True)

"""Configuracion de logging centralizada (consola + archivo rotativo)."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def configurar_logging(log_dir: Path, nivel: str = "INFO") -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("gdd_loader")
    logger.setLevel(nivel)
    logger.handlers.clear()  # evita duplicar handlers si se llama mas de una vez

    formato = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    consola = logging.StreamHandler()
    consola.setFormatter(formato)
    logger.addHandler(consola)

    archivo = RotatingFileHandler(
        log_dir / "gdd_loader.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8"
    )
    archivo.setFormatter(formato)
    logger.addHandler(archivo)

    return logger

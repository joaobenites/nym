"""Logging em arquivo (com rotação) e no console."""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from app.config import Settings

_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def _close_handlers(logger: logging.Logger) -> None:
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()  # libera o arquivo (no Windows, arquivo aberto não pode ser apagado)


def setup_logging(settings: Settings) -> logging.Logger:
    """Configura o logger "nym". Reconfigura se o diretório de logs mudar."""
    logger = logging.getLogger("nym")
    log_path = settings.log_dir / "nym.log"
    if getattr(logger, "_nym_log_path", None) == log_path:
        return logger
    _close_handlers(logger)

    logger.setLevel(getattr(logging, settings.log_level, logging.INFO))
    formatter = logging.Formatter(_FORMAT)

    settings.log_dir.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(log_path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    file_handler.setFormatter(formatter)
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.addHandler(console)
    logger.propagate = False
    logger._nym_log_path = log_path  # type: ignore[attr-defined]
    return logger


def shutdown_logging() -> None:
    """Fecha os handlers do Nym. Usado ao encerrar e nos testes."""
    logger = logging.getLogger("nym")
    _close_handlers(logger)
    logger._nym_log_path = None  # type: ignore[attr-defined]

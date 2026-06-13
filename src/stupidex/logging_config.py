"""
Sistema de logging JSON estruturado para Stupidex.
Facilita a ingestão em ferramentas como ELK, Datadog, CloudWatch.
"""

import json
import logging
import os
import sys
from datetime import datetime
from typing import Any, Dict, Optional


class JSONFormatter(logging.Formatter):
    """Formatador de log em formato JSON estruturado."""

    def format(self, record: logging.LogRecord) -> str:
        log_data: Dict[str, Any] = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        # Adicionar user_id se disponível
        if hasattr(record, "user_id") and record.user_id:
            log_data["user_id"] = record.user_id

        # Adicionar exception info se presente
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        # Adicionar extra fields
        for key, value in record.__dict__.items():
            if key not in log_data and not callable(value):
                log_data[key] = value

        return json.dumps(log_data)


def setup_logging(log_level: Optional[str] = None) -> logging.Logger:
    """
    Configura o sistema de logging estruturado.

    Args:
        log_level: Nível de log (DEBUG, INFO, WARNING, ERROR, CRITICAL).
                   Se não especificado, lê da variável de ambiente LOG_LEVEL.

    Returns:
        Logger configurado.
    """
    level = getattr(logging, (log_level or os.getenv("LOG_LEVEL", "INFO")).upper())

    # Configurar root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Limpar handlers existentes
    root_logger.handlers.clear()

    # Criar handler com formatador JSON
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)
    handler.setFormatter(JSONFormatter())

    root_logger.addHandler(handler)

    return logging.getLogger("stupidex")


class StructLogAdapter(logging.LoggerAdapter):
    """Adapter para adicionar contexto estruturado aos logs."""

    def process(self, msg: str, kwargs: Dict[str, Any]) -> tuple:
        log_data = kwargs.get("extra", {})
        if "user_id" in kwargs:
            log_data["user_id"] = kwargs.pop("user_id")
        kwargs["extra"] = log_data
        return msg, kwargs

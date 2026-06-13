"""Structured logging for Stupidex production deployments.

Provides JSON-formatted logging with context (user_id, session_id, request_id)
for better observability and log aggregation.
"""

import json
import logging
import os
import sys
import threading
import time
import uuid
from typing import Any


class StructuredFormatter(logging.Formatter):
    """JSON formatter that adds contextual fields to every log record."""

    def __init__(self, service_name: str = "stupidex"):
        super().__init__()
        self.service_name = service_name
        self._local = threading.local()

    def set_context(self, **kwargs: Any) -> None:
        """Set thread-local context for logging (user_id, session_id, etc.)."""
        if not hasattr(self._local, "context"):
            self._local.context = {}
        self._local.context.update(kwargs)

    def clear_context(self) -> None:
        """Clear thread-local context."""
        if hasattr(self._local, "context"):
            self._local.context = {}

    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON with context."""
        log_entry = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)),
            "level": record.levelname,
            "service": self.service_name,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Add exception info if present
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)

        # Add thread-local context
        if hasattr(self._local, "context") and self._local.context:
            log_entry["context"] = self._local.context.copy()

        # Add extra fields from record
        for key, value in record.__dict__.items():
            if key not in {
                "msg",
                "args",
                "created",
                "filename",
                "funcName",
                "levelname",
                "levelno",
                "lineno",
                "module",
                "msecs",
                "name",
                "pathname",
                "process",
                "processName",
                "relativeCreated",
                "stack_info",
                "exc_info",
                "exc_text",
                "thread",
                "threadName",
                "taskName",
            }:
                log_entry[key] = value

        return json.dumps(log_entry, ensure_ascii=False, default=str)


def setup_logging(
    level: str | None = None,
    service_name: str = "stupidex",
    enable_console: bool = True,
    enable_file: bool = False,
    log_file: str | None = None,
) -> StructuredFormatter:
    """Configure structured logging for the application.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL).
               Defaults to LOG_LEVEL env var or INFO.
        service_name: Service name for log entries.
        enable_console: Output logs to stdout.
        enable_file: Output logs to a file.
        log_file: Path to log file (required if enable_file=True).

    Returns:
        The configured formatter (for setting context).
    """
    logger = logging.getLogger()
    logger.setLevel(getattr(logging, (level or os.getenv("LOG_LEVEL", "INFO")).upper()))

    # Remove existing handlers
    logger.handlers.clear()

    formatter = StructuredFormatter(service_name=service_name)

    # Console handler
    if enable_console:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    # File handler
    if enable_file:
        if not log_file:
            raise ValueError("log_file is required when enable_file=True")
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return formatter


def get_logger(name: str) -> logging.Logger:
    """Get a logger instance with the given name."""
    return logging.getLogger(name)


# Global formatter instance (set by setup_logging)
_formatter: StructuredFormatter | None = None


def set_log_context(**kwargs: Any) -> None:
    """Set global log context for the current thread."""
    global _formatter
    if _formatter:
        _formatter.set_context(**kwargs)


def clear_log_context() -> None:
    """Clear global log context for the current thread."""
    global _formatter
    if _formatter:
        _formatter.clear_context()


def init_app_logging(app=None) -> StructuredFormatter:
    """Initialize logging for Flask app."""
    formatter = setup_logging(
        service_name="stupidex-web",
        enable_console=True,
        enable_file=os.getenv("LOG_FILE_ENABLED", "false").lower() == "true",
        log_file=os.getenv("LOG_FILE_PATH", "/var/log/stupidex/app.log"),
    )
    global _formatter
    _formatter = formatter

    if app:
        # Configure Flask's logger
        app.logger.handlers.clear()
        for handler in logging.getLogger().handlers:
            app.logger.addHandler(handler)
        app.logger.setLevel(logging.getLogger().level)

    return formatter

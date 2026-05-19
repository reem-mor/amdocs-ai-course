"""Structured logging setup — provides a configured logger factory for all modules.

Format: ``%(asctime)s [%(levelname)s] %(name)s — %(message)s``

Logging is configured exactly once per process (idempotent), at INFO level by
default. The stream handler is forced to UTF-8 so the em-dash separator (and
any other non-ASCII payload) does not blow up on Windows consoles that default
to cp1252. Call ``get_logger(__name__)`` from any module to obtain a child
logger.
"""

from __future__ import annotations

import io
import logging
import sys
from threading import Lock
from typing import TextIO

_LOG_FORMAT: str = "%(asctime)s [%(levelname)s] %(name)s \u2014 %(message)s"
_DATE_FORMAT: str = "%Y-%m-%dT%H:%M:%S%z"
_DEFAULT_LEVEL: int = logging.INFO
_HANDLER_NAME: str = "incidentiq.stdout"

_configured: bool = False
_lock: Lock = Lock()


def _utf8_stream() -> TextIO:
    """Return a UTF-8 text stream pointing at the process stdout.

    Prefers ``sys.stdout.reconfigure(encoding="utf-8")`` when available (CPython
    3.7+ on a ``TextIOWrapper``). Falls back to wrapping the underlying binary
    buffer in a fresh ``TextIOWrapper`` if reconfigure is not exposed (e.g. the
    stream has been replaced by something exotic in a test harness).
    """
    stream: TextIO = sys.stdout
    reconfigure = getattr(stream, "reconfigure", None)
    if callable(reconfigure):
        try:
            reconfigure(encoding="utf-8", errors="replace")
            return stream
        except (ValueError, OSError):
            pass

    buffer = getattr(stream, "buffer", None)
    if buffer is not None:
        return io.TextIOWrapper(
            buffer,
            encoding="utf-8",
            errors="replace",
            line_buffering=True,
            write_through=True,
        )
    return stream


def _configure_root_logger() -> None:
    """Attach a single UTF-8 stdout handler to the root logger with the project format.

    Idempotent and thread-safe: safe to call multiple times. The project handler
    is added at most once; pre-existing handlers on the root logger are left
    untouched.
    """
    global _configured
    with _lock:
        if _configured:
            return

        root: logging.Logger = logging.getLogger()
        root.setLevel(_DEFAULT_LEVEL)

        handler: logging.StreamHandler = logging.StreamHandler(stream=_utf8_stream())
        handler.setLevel(_DEFAULT_LEVEL)
        handler.setFormatter(
            logging.Formatter(fmt=_LOG_FORMAT, datefmt=_DATE_FORMAT)
        )
        handler.set_name(_HANDLER_NAME)

        already_attached: bool = any(
            getattr(h, "name", None) == _HANDLER_NAME for h in root.handlers
        )
        if not already_attached:
            root.addHandler(handler)

        _configured = True


def get_logger(name: str) -> logging.Logger:
    """Return a configured logger for the given module name.

    Args:
        name: Typically ``__name__`` of the calling module.

    Returns:
        A ``logging.Logger`` instance that emits to stdout in UTF-8 using the
        project format.
    """
    _configure_root_logger()
    return logging.getLogger(name)

import json
import logging
import os
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from app.core.logging_config import sanitize_log_value


logger = logging.getLogger("ssalgo.trading")
_file_logging_configured = False


def trading_debug_enabled():
    value = os.getenv("DEBUG_TRADING", os.getenv("SSLAGO_DEBUG_TRADING", "false"))
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def configure_trading_debug_logging():
    global _file_logging_configured
    if _file_logging_configured:
        return

    log_path = Path(
        os.getenv(
            "DEBUG_TRADING_LOG_FILE",
            Path(__file__).resolve().parents[2] / "logs" / "trading_debug.log",
        )
    )
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        log_path,
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = True
    _file_logging_configured = True


def trading_event(event: str, *, level=logging.INFO, force=False, **details: Any):
    if not force and not trading_debug_enabled():
        return
    configure_trading_debug_logging()
    payload = {
        "event": event,
        **sanitize_log_value(details),
    }
    logger.log(level, json.dumps(payload, default=str, sort_keys=True))


def trading_exception(event: str, exc: Exception, **details: Any):
    trading_event(
        event,
        level=logging.ERROR,
        force=True,
        error=str(exc),
        exception_type=type(exc).__name__,
        stack_trace=traceback.format_exc(),
        **details,
    )

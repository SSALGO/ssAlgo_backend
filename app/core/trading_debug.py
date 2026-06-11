import json
import logging
import os
import traceback
from typing import Any

from app.core.logging_config import sanitize_log_value


logger = logging.getLogger("ssalgo.trading")


def trading_debug_enabled():
    value = os.getenv("DEBUG_TRADING", os.getenv("SSLAGO_DEBUG_TRADING", "false"))
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def trading_event(event: str, *, level=logging.INFO, force=False, **details: Any):
    if not force and not trading_debug_enabled():
        return
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

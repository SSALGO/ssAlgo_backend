import os
from functools import lru_cache
from typing import Any

import requests

from app.core.logging_config import sanitize_log_value
from app.core.trading_debug import trading_event


def broker_diagnostics_enabled() -> bool:
    value = os.getenv("SSLAGO_ALICEBLUE_DIAGNOSTICS", "false")
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


@lru_cache(maxsize=1)
def outbound_public_ip() -> str:
    if not broker_diagnostics_enabled():
        return ""
    try:
        return requests.get("https://api.ipify.org", timeout=5).text.strip()
    except Exception as exc:
        return f"unavailable:{type(exc).__name__}"


def log_aliceblue_diagnostic(event: str, **details: Any) -> None:
    if not broker_diagnostics_enabled():
        return
    sanitized_details = sanitize_log_value(details)
    sanitized_details.pop("broker", None)
    sanitized_details.setdefault("public_ip", outbound_public_ip())
    trading_event(
        event,
        force=True,
        broker="aliceblue",
        **sanitized_details,
    )


def response_summary(response: requests.Response, body: Any = None) -> dict:
    return {
        "http_status": getattr(response, "status_code", None),
        "url": getattr(response, "url", ""),
        "headers": {
            key: value
            for key, value in dict(getattr(response, "headers", {}) or {}).items()
            if key.lower() in {"content-type", "date", "server", "x-request-id"}
        },
        "body": body,
    }

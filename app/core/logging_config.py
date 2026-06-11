import logging
import os
from typing import Any


SENSITIVE_KEYS = {
    "password",
    "pwd",
    "pin",
    "secret",
    "api_secret",
    "apisecret",
    "secret_key",
    "totp",
    "totp_key",
    "factor2",
    "interactive_secret",
    "epassword",
    "token",
    "access_token",
    "auth_code",
    "session",
    "session_id",
    "sessionid",
    "user_session",
    "signature",
    "otp",
}


def mask_value(value: Any) -> Any:
    if value is None:
        return None
    text = str(value)
    if len(text) <= 4:
        return "***"
    return f"{text[:2]}***{text[-2:]}"


def sanitize_log_value(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized = {}
        for key, item in value.items():
            if str(key).lower() in SENSITIVE_KEYS:
                sanitized[key] = mask_value(item)
            else:
                sanitized[key] = sanitize_log_value(item)
        return sanitized
    if isinstance(value, list):
        return [sanitize_log_value(item) for item in value]
    return value


def configure_logging(level: str | None = None):
    logging.basicConfig(
        level=getattr(logging, (level or os.getenv("SSLAGO_LOG_LEVEL", "INFO")).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def log_print(logger: logging.Logger, *args: Any, **_kwargs):
    message = " ".join(str(sanitize_log_value(arg)) for arg in args)
    logger.info(message)

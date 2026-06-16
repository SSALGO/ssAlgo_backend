import hashlib
from urllib.parse import parse_qs, urlencode, urlparse

import requests

from app.core.trading_debug import trading_event
from app.domain.brokers.diagnostics import (
    log_aliceblue_diagnostic,
    response_summary,
)


ALICEBLUE_LOGIN_ORIGIN = "https://ant.aliceblueonline.com"
ALICEBLUE_SESSION_URL = (
    "https://a3.aliceblueonline.com/open-api/od/v1/vendor/getUserDetails"
)


class AliceBlueAuthError(RuntimeError):
    pass


class AliceBlueDirectAuthError(AliceBlueAuthError):
    pass


class AliceBlueSessionExchangeError(AliceBlueAuthError):
    pass


class AliceBlueDirectAuthenticator:
    def authenticate(self, **_kwargs):
        raise AliceBlueDirectAuthError(
            "AliceBlue password/TOTP authentication is disabled. "
            "Reconnect the broker using AliceBlue redirect login."
        )


def build_aliceblue_connect_url(app_code, callback_url, state):
    if not str(app_code or "").strip():
        raise AliceBlueAuthError("AliceBlue app code is not configured")
    params = {"appcode": str(app_code).strip()}
    if callback_url:
        params["redirect_uri"] = str(callback_url).strip()
    if state:
        params["state"] = str(state).strip()
    return f"{ALICEBLUE_LOGIN_ORIGIN}/?{urlencode(params)}"


def parse_aliceblue_callback(data):
    values = dict(data or {})
    redirect_url = values.get("redirectUrl") or values.get("redirect_url") or ""
    if redirect_url:
        query = parse_qs(urlparse(str(redirect_url)).query)
        for key, query_values in query.items():
            if query_values and key not in values:
                values[key] = query_values[0]

    auth_code = (
        values.get("authCode")
        or values.get("authcode")
        or values.get("code")
        or values.get("auth_code")
        or ""
    )
    user_id = (
        values.get("userId")
        or values.get("userid")
        or values.get("clientId")
        or values.get("client_id")
        or values.get("apikey")
        or ""
    )
    state = values.get("state") or ""
    return {
        "auth_code": str(auth_code).strip(),
        "user_id": str(user_id).strip(),
        "state": str(state).strip(),
        "raw": values,
    }


def exchange_auth_code_for_session(user_id, auth_code, app_secret, http=None, timeout=20):
    missing = [
        name
        for name, value in (
            ("alice_client_id", user_id),
            ("auth_code", auth_code),
            ("app_secret", app_secret),
        )
        if not str(value or "").strip()
    ]
    if missing:
        raise AliceBlueSessionExchangeError(
            "AliceBlue callback is missing " + ", ".join(missing)
        )

    checksum = hashlib.sha256(
        f"{str(user_id).strip()}{str(auth_code).strip()}{str(app_secret).strip()}".encode(
            "utf-8"
        )
    ).hexdigest()
    client = http or requests
    response = None
    body = None
    try:
        trading_event(
            "aliceblue_session_exchange_request",
            broker="aliceblue",
            url=ALICEBLUE_SESSION_URL,
            user_id=user_id,
            force=True,
        )
        log_aliceblue_diagnostic(
            "aliceblue_session_exchange_request",
            url=ALICEBLUE_SESSION_URL,
            user_id=user_id,
        )
        response = client.post(
            ALICEBLUE_SESSION_URL,
            json={"checkSum": checksum},
            headers={"Content-Type": "application/json"},
            timeout=timeout,
        )
        response.raise_for_status()
        body = response.json()
        log_aliceblue_diagnostic(
            "aliceblue_session_exchange_response",
            **response_summary(response, body),
        )
    except requests.RequestException as exc:
        if response is not None:
            try:
                body = response.json()
            except ValueError:
                body = getattr(response, "text", "")
            log_aliceblue_diagnostic(
                "aliceblue_session_exchange_error",
                error=str(exc),
                **response_summary(response, body),
            )
            trading_event(
                "aliceblue_session_exchange_error",
                broker="aliceblue",
                user_id=user_id,
                error=str(exc),
                **response_summary(response, body),
                force=True,
            )
        else:
            trading_event(
                "aliceblue_session_exchange_error",
                broker="aliceblue",
                user_id=user_id,
                error=str(exc),
                force=True,
            )
        raise AliceBlueSessionExchangeError(
            f"AliceBlue session exchange failed: {type(exc).__name__}"
        ) from exc
    except ValueError as exc:
        trading_event(
            "aliceblue_session_exchange_error",
            broker="aliceblue",
            user_id=user_id,
            error="AliceBlue session exchange returned a non-JSON response",
            exception_type=type(exc).__name__,
            **response_summary(response, getattr(response, "text", "") if response is not None else None),
            force=True,
        )
        raise AliceBlueSessionExchangeError(
            "AliceBlue session exchange returned a non-JSON response"
        ) from exc

    session_id = body.get("userSession") if isinstance(body, dict) else None
    if not session_id:
        trading_event(
            "aliceblue_session_exchange_error",
            broker="aliceblue",
            user_id=user_id,
            error="AliceBlue session exchange returned no userSession",
            **response_summary(response, body),
            force=True,
        )
        raise AliceBlueSessionExchangeError(
            "AliceBlue session exchange returned no userSession"
        )
    trading_event(
        "aliceblue_session_exchange_success",
        broker="aliceblue",
        user_id=user_id,
        session_returned=True,
        **response_summary(response, body),
        force=True,
    )
    return {
        "user_id": str(user_id).strip(),
        "auth_code": str(auth_code).strip(),
        "session_id": str(session_id).strip(),
        "raw": body,
    }


def classify_aliceblue_error(error):
    if isinstance(error, dict):
        text = " ".join(str(value) for value in error.values() if value is not None)
    else:
        text = str(error or "")
    normalized = text.lower()
    if any(token in normalized for token in ("ec097", "ip whitelist", "ip is not")):
        return "ip_restricted"
    if any(
        token in normalized
        for token in (
            "unauthorized",
            "invalid session",
            "session expired",
            "token expired",
            "401",
        )
    ):
        return "session_expired"
    return "broker_error"


def aliceblue_error_message(kind):
    if kind == "session_expired":
        return "AliceBlue session expired. Please reconnect AliceBlue."
    if kind == "ip_restricted":
        return "AliceBlue rejected the request due to IP restriction/whitelisting."
    return "AliceBlue request failed."

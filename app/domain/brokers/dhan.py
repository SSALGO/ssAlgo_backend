import datetime
from dataclasses import dataclass
from typing import Any
from zoneinfo import ZoneInfo

import requests

from app.core.secrets import decrypt_secret


DHAN_BASE_URL = "https://api.dhan.co/v2"
DHAN_TIMEZONE = ZoneInfo("Asia/Kolkata")


@dataclass
class DhanError(Exception):
    category: str
    public_message: str
    code: str = ""
    http_status: int | None = None
    retryable: bool = False
    token_invalid: bool = False

    def __str__(self):
        return self.public_message

    def to_dict(self):
        return {
            "category": self.category,
            "message": self.public_message,
            "code": self.code,
            "http_status": self.http_status,
            "retryable": self.retryable,
            "token_invalid": self.token_invalid,
        }


def canonicalize_dhan_credentials(values: dict | None) -> dict:
    values = dict(values or {})
    dhan_client_id = (
        values.get("dhanClientId")
        or values.get("dhan_client_id")
        or values.get("client_id")
        or ""
    )
    access_token = (
        values.get("accessToken")
        or values.get("access_token")
        or ""
    )
    return {
        "dhanClientId": str(decrypt_secret(dhan_client_id) or "").strip(),
        "accessToken": str(decrypt_secret(access_token) or "").strip(),
    }


def parse_token_expiry(value: Any):
    if value in (None, ""):
        return None
    if isinstance(value, datetime.datetime):
        parsed = value
    elif isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp /= 1000
        parsed = datetime.datetime.fromtimestamp(timestamp, tz=datetime.UTC)
    else:
        text = str(value).strip()
        parsed = None
        try:
            parsed = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            pass
        if parsed is None:
            for pattern in (
                "%d/%m/%Y %H:%M",
                "%d/%m/%Y %H:%M:%S",
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d %H:%M",
            ):
                try:
                    parsed = datetime.datetime.strptime(text, pattern)
                    break
                except ValueError:
                    continue
        if parsed is None:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=DHAN_TIMEZONE)
    return parsed.astimezone(datetime.UTC)


class DhanService:
    ERROR_CODES = {
        "DH-901": ("authentication", "Dhan access token is invalid or expired.", False, True),
        "DH-902": ("api_access", "Dhan API access is unavailable for this account.", False, False),
        "DH-903": ("segment_disabled", "The required Dhan trading segment is not enabled.", False, False),
        "DH-904": ("rate_limit", "Dhan API rate limit exceeded. Please retry shortly.", True, False),
        "DH-905": ("invalid_request", "Dhan rejected the request parameters.", False, False),
        "DH-906": ("order_rejected", "Dhan rejected the order.", False, False),
        "DH-907": ("data_unavailable", "Requested Dhan account data is unavailable.", False, False),
        "DH-908": ("broker_error", "Dhan is temporarily unable to process the request.", True, False),
        "DH-909": ("network", "Unable to reach Dhan. Please retry.", True, False),
    }

    def __init__(
        self,
        dhan_client_id: str,
        access_token: str,
        http=None,
        base_url: str = DHAN_BASE_URL,
        timeout=(5, 15),
    ):
        self.dhan_client_id = str(dhan_client_id or "").strip()
        self.access_token = str(access_token or "").strip()
        self.http = http or requests.Session()
        self.base_url = str(base_url or DHAN_BASE_URL).rstrip("/")
        self.timeout = timeout
        if not self.dhan_client_id or not self.access_token:
            raise DhanError(
                "missing_credentials",
                "Dhan Client ID and access token are required.",
            )

    def _headers(self):
        return {
            "access-token": self.access_token,
            "dhanClientId": self.dhan_client_id,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    @staticmethod
    def _message(payload):
        if not isinstance(payload, dict):
            return ""
        return str(
            payload.get("errorMessage")
            or payload.get("message")
            or payload.get("remarks")
            or payload.get("statusMessage")
            or ""
        ).strip()

    @classmethod
    def normalize_error(cls, payload=None, http_status=None, exc=None):
        if isinstance(exc, (requests.Timeout, TimeoutError)):
            return DhanError("network_timeout", "Dhan request timed out.", retryable=True)
        if isinstance(exc, requests.RequestException):
            return DhanError("network", "Unable to reach Dhan.", retryable=True)

        payload = payload if isinstance(payload, dict) else {}
        code = str(payload.get("errorCode") or payload.get("code") or "").strip()
        message = cls._message(payload)
        normalized = message.lower()

        if code in cls.ERROR_CODES:
            category, public_message, retryable, token_invalid = cls.ERROR_CODES[code]
            if message and category in {"invalid_request", "order_rejected", "broker_error"}:
                public_message = message
            return DhanError(
                category,
                public_message,
                code=code,
                http_status=http_status,
                retryable=retryable,
                token_invalid=token_invalid,
            )
        if http_status in {401, 403} or any(
            token in normalized
            for token in ("invalid token", "token expired", "access token", "unauthor")
        ):
            return DhanError(
                "authentication",
                "Dhan access token is invalid or expired.",
                code=code,
                http_status=http_status,
                token_invalid=True,
            )
        if http_status == 429 or "rate limit" in normalized or "too many request" in normalized:
            return DhanError(
                "rate_limit",
                "Dhan API rate limit exceeded. Please retry shortly.",
                code=code,
                http_status=http_status,
                retryable=True,
            )
        if any(token in normalized for token in ("static ip", "ip not allowed", "ip is not", "whitelist ip")):
            return DhanError(
                "static_ip_restricted",
                "Dhan rejected the request because this server IP is not allowed.",
                code=code,
                http_status=http_status,
            )
        if any(token in normalized for token in ("insufficient", "margin shortfall", "not enough balance")):
            return DhanError(
                "insufficient_balance",
                "Insufficient Dhan balance or margin for this order.",
                code=code,
                http_status=http_status,
            )
        if any(token in normalized for token in ("segment", "not enabled", "not activated")):
            return DhanError(
                "segment_disabled",
                "The required Dhan trading segment is not enabled.",
                code=code,
                http_status=http_status,
            )
        if "client" in normalized and any(token in normalized for token in ("invalid", "mismatch", "not found")):
            return DhanError(
                "invalid_client_id",
                "Dhan Client ID is invalid or does not match the access token.",
                code=code,
                http_status=http_status,
            )
        if http_status and http_status >= 500:
            return DhanError(
                "broker_error",
                "Dhan is temporarily unable to process the request.",
                code=code,
                http_status=http_status,
                retryable=True,
            )
        return DhanError(
            "request_failed",
            message or "Dhan rejected the request.",
            code=code,
            http_status=http_status,
        )

    @staticmethod
    def _broker_order_id(payload):
        if not isinstance(payload, dict):
            return None
        return (
            payload.get("orderId")
            or payload.get("order_id")
            or payload.get("brokerOrderId")
            or payload.get("id")
        )

    @staticmethod
    def _status(payload, default="ok"):
        if not isinstance(payload, dict):
            return default
        return str(
            payload.get("orderStatus")
            or payload.get("status")
            or payload.get("Status")
            or default
        ).strip()

    def _request(self, method, path, action, payload=None):
        try:
            response = self.http.request(
                method,
                f"{self.base_url}/{str(path).lstrip('/')}",
                headers=self._headers(),
                json=payload,
                timeout=self.timeout,
            )
        except Exception as exc:
            raise self.normalize_error(exc=exc) from exc

        try:
            body = response.json()
        except (TypeError, ValueError):
            body = {}

        status_text = self._status(body, "").lower()
        message_text = self._message(body).lower()
        rejected = status_text in {"rejected", "failed", "error", "expired"} or any(
            token in message_text for token in ("reject", "invalid", "error", "failed")
        )
        if response.status_code >= 400 or (isinstance(body, dict) and body.get("errorCode")) or rejected:
            raise self.normalize_error(body, http_status=response.status_code)

        return {
            "success": True,
            "broker": "dhan",
            "action": action,
            "status": self._status(body),
            "message": self._message(body) or "ok",
            "broker_order_id": (
                str(self._broker_order_id(body))
                if self._broker_order_id(body) is not None
                else None
            ),
            "data": body,
        }

    def verify_connection(self):
        profile = self.get_profile()
        data = profile.get("data") or {}
        returned_client_id = str(
            data.get("dhanClientId")
            or data.get("dhan_client_id")
            or data.get("clientId")
            or ""
        ).strip()
        if returned_client_id and returned_client_id != self.dhan_client_id:
            raise DhanError(
                "invalid_client_id",
                "Dhan Client ID does not match the access token.",
            )
        profile["token_expires_at"] = parse_token_expiry(
            data.get("tokenValidity")
            or data.get("token_validity")
            or data.get("tokenExpiry")
        )
        if (
            profile["token_expires_at"]
            and profile["token_expires_at"] <= datetime.datetime.now(datetime.UTC)
        ):
            raise DhanError(
                "authentication",
                "Dhan access token is expired.",
                token_invalid=True,
            )
        return profile

    def get_profile(self):
        return self._request("GET", "/profile", "profile")

    def get_funds(self):
        return self._request("GET", "/fundlimit", "funds")

    def get_positions(self):
        return self._request("GET", "/positions", "positions")

    def get_holdings(self):
        return self._request("GET", "/holdings", "holdings")

    def get_orderbook(self):
        return self._request("GET", "/orders", "orderbook")

    def place_order(self, payload):
        return self._request("POST", "/orders", "place_order", payload=payload)

    def modify_order(self, order_id, payload):
        return self._request(
            "PUT",
            f"/orders/{order_id}",
            "modify_order",
            payload=payload,
        )

    def cancel_order(self, order_id):
        return self._request("DELETE", f"/orders/{order_id}", "cancel_order")

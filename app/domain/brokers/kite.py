import datetime
import hashlib
import time
from urllib.parse import urlencode

import requests

from app.core.config import AppConfig
from app.core.secrets import decrypt_secret, encrypt_secret
from app.core.trading_debug import trading_event


class KiteError(RuntimeError):
    pass


class KiteTokenExpired(KiteError):
    pass


class KiteService:
    BASE_URL = "https://api.kite.trade"
    LOGIN_URL = "https://kite.zerodha.com/connect/login"
    BROKER_ID = "zerodha"
    BROKER_NAME = "KITE"

    def __init__(self, db, http=None):
        self.db = db
        self.http = http or requests

    @staticmethod
    def today():
        return datetime.datetime.utcnow().date()

    @staticmethod
    def now():
        return datetime.datetime.utcnow()

    @property
    def api_key(self):
        return str(AppConfig.KITE_API_KEY or "").strip()

    @property
    def api_secret(self):
        return str(AppConfig.KITE_API_SECRET or "").strip()

    @property
    def redirect_url(self):
        return str(AppConfig.KITE_REDIRECT_URL or "").strip()

    def require_config(self):
        missing = []
        if not self.api_key:
            missing.append("KITE_API_KEY")
        if not self.api_secret:
            missing.append("KITE_API_SECRET")
        if missing:
            raise KiteError(f"Kite is not configured: missing {', '.join(missing)}")

    def generate_login_url(self, state=None):
        self.require_config()
        params = {"v": 3, "api_key": self.api_key}
        if state:
            params["state"] = str(state).strip()
        return f"{self.LOGIN_URL}?{urlencode(params)}"

    def checksum(self, request_token):
        return hashlib.sha256(
            f"{self.api_key}{request_token}{self.api_secret}".encode("utf-8")
        ).hexdigest()

    def generate_session(self, request_token):
        self.require_config()
        if not str(request_token or "").strip():
            raise KiteError("Kite request_token is missing")
        response = self.http.post(
            f"{self.BASE_URL}/session/token",
            data={
                "api_key": self.api_key,
                "request_token": str(request_token).strip(),
                "checksum": self.checksum(request_token),
            },
            headers={"X-Kite-Version": "3"},
            timeout=20,
        )
        try:
            body = response.json()
        except ValueError as exc:
            raise KiteError("Kite session exchange returned a non-JSON response") from exc
        if response.status_code >= 400 or body.get("status") == "error":
            raise KiteError(body.get("message") or f"Kite session exchange failed: {response.status_code}")
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, dict) or not data.get("access_token"):
            raise KiteError("Kite session exchange returned no access_token")
        return data

    def save_session(self, user, session):
        now = self.now()
        access_token = str(session.get("access_token") or "").strip()
        if not access_token:
            raise KiteError("Kite access_token is missing")
        fields = {
            "user": user,
            "userId": user,
            "broker": self.BROKER_ID,
            "brokerName": self.BROKER_NAME,
            "kiteUserId": session.get("user_id") or session.get("user_name") or "",
            "apiKey": self.api_key,
            "api_key": self.api_key,
            "accessTokenEncrypted": encrypt_secret(access_token),
            "access_token": encrypt_secret(access_token),
            "refreshTokenEncrypted": encrypt_secret(session.get("refresh_token", "")) if session.get("refresh_token") else "",
            "publicToken": session.get("public_token", ""),
            "loginTime": now,
            "tokenDate": self.today().isoformat(),
            "isConnected": True,
            "connectionStatus": "connected",
            "token_status": "connected",
            "lastError": "",
            "last_verified_at": now,
            "connected_at": now,
            "updatedAt": now,
        }
        self.db["apis"].update_one(
            {"user": user, "broker": self.BROKER_ID},
            {"$set": fields, "$setOnInsert": {"createdAt": now}},
            upsert=True,
        )
        self.db["broker"].update_one(
            {"user": user},
            {"$set": {"user": user, "selectedbroker": self.BROKER_ID}},
            upsert=True,
        )
        self.db["broker_health"].update_one(
            {"user": user, "broker": self.BROKER_ID},
            {
                "$set": {
                    "login_status": "connected",
                    "websocket_status": "not_tested",
                    "token_status": "connected",
                    "last_error": "",
                    "connected_at": now,
                    "last_verified_at": now,
                    "updated_at": now,
                },
                "$setOnInsert": {"user": user, "broker": self.BROKER_ID, "created_at": now},
            },
            upsert=True,
        )
        return fields

    def connection_row(self, user):
        return self.db["apis"].find_one({"user": user, "broker": self.BROKER_ID}) or {}

    def access_token(self, user, *, require_valid=True):
        row = self.connection_row(user)
        if require_valid and not row.get("isConnected", True):
            raise KiteTokenExpired("Kite session expired. Please reconnect Kite.")
        token_date = str(row.get("tokenDate") or row.get("token_date") or "").strip()
        if require_valid and token_date and token_date != self.today().isoformat():
            self.mark_token_expired(user, "Kite session expired. Please reconnect Kite.")
            raise KiteTokenExpired("Kite session expired. Please reconnect Kite.")
        encrypted = row.get("accessTokenEncrypted") or row.get("access_token")
        if not encrypted:
            raise KiteError("Kite access token is missing. Please reconnect Kite.")
        try:
            return decrypt_secret(encrypted)
        except (TypeError, ValueError) as exc:
            raise KiteError("Kite access token could not be decrypted. Please reconnect Kite.") from exc

    def validate_order_token(self, user):
        row = self.connection_row(user)
        token_date = str(row.get("tokenDate") or row.get("token_date") or "").strip()
        if token_date != self.today().isoformat():
            self.mark_token_expired(user, "Kite session expired. Please reconnect Kite.")
            raise KiteTokenExpired("Kite session expired. Please reconnect Kite.")
        return self.access_token(user, require_valid=True)

    def mark_token_expired(self, user, message):
        now = self.now()
        self.db["apis"].update_one(
            {"user": user, "broker": self.BROKER_ID},
            {"$set": {"isConnected": False, "connectionStatus": "token_expired", "token_status": "token_expired", "lastError": message, "updatedAt": now}},
            upsert=True,
        )
        self.db["broker_health"].update_one(
            {"user": user, "broker": self.BROKER_ID},
            {"$set": {"login_status": "reconnect_required", "token_status": "token_expired", "last_error": message, "updated_at": now}},
            upsert=True,
        )
        trading_event("kite_token_expired", broker="zerodha", user=user, force=True)

    def headers(self, user, *, require_valid=True):
        return {
            "X-Kite-Version": "3",
            "Authorization": f"token {self.api_key}:{self.access_token(user, require_valid=require_valid)}",
        }

    def request(self, method, path, user, **kwargs):
        started = time.perf_counter()
        require_valid = kwargs.pop("require_valid", True)
        response = self.http.request(
            method,
            f"{self.BASE_URL}{path}",
            headers={**self.headers(user, require_valid=require_valid), **kwargs.pop("headers", {})},
            timeout=kwargs.pop("timeout", 15),
            **kwargs,
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
        try:
            body = response.json()
        except ValueError:
            body = {"status": "error", "message": f"Kite returned non-JSON response ({response.status_code})"}
        if response.status_code in {403, 401}:
            self.mark_token_expired(user, body.get("message") or "Kite token rejected. Please reconnect Kite.")
        return body, response.status_code, latency_ms

    def get_profile(self, user):
        body, _status, _latency = self.request("GET", "/user/profile", user)
        return body

    def get_margins(self, user):
        body, _status, _latency = self.request("GET", "/user/margins", user)
        return body

    def get_orders(self, user):
        body, _status, _latency = self.request("GET", "/orders", user)
        return body

    def get_order_history(self, user, order_id):
        body, _status, _latency = self.request("GET", f"/orders/{order_id}", user)
        return body

    def get_positions(self, user):
        body, _status, _latency = self.request("GET", "/portfolio/positions", user)
        return body

    def get_holdings(self, user):
        body, _status, _latency = self.request("GET", "/portfolio/holdings", user)
        return body

    def place_order(self, user, payload):
        self.validate_order_token(user)
        variety = str(payload.get("variety") or "regular").lower()
        request_payload = {
            "exchange": payload["exchange"],
            "tradingsymbol": payload["tradingsymbol"],
            "transaction_type": payload["transaction_type"],
            "quantity": int(payload["quantity"]),
            "product": payload.get("product") or "MIS",
            "order_type": payload.get("order_type") or "MARKET",
            "validity": payload.get("validity") or "DAY",
        }
        if payload.get("price") is not None:
            request_payload["price"] = payload["price"]
        if payload.get("trigger_price") is not None:
            request_payload["trigger_price"] = payload["trigger_price"]
        body, status_code, latency_ms = self.request(
            "POST",
            f"/orders/{variety}",
            user,
            data=request_payload,
        )
        order_id = (body.get("data") or {}).get("order_id") if isinstance(body, dict) else None
        log_row = {
            "userId": user,
            "user": user,
            "broker": self.BROKER_ID,
            "strategyId": payload.get("strategy_id"),
            "signalId": payload.get("signal_id"),
            "requestPayload": request_payload,
            "brokerResponse": body,
            "orderId": order_id,
            "status": "placed" if body.get("status") == "success" else "rejected",
            "failureReason": "" if body.get("status") == "success" else body.get("message", ""),
            "placedAt": self.now(),
            "updatedAt": self.now(),
            "latencyMs": latency_ms,
            "ipAddress": "",
            "retryCount": 0,
            "source": payload.get("source") or "MANUAL",
        }
        self.db["order_logs"].insert_one(log_row)
        return body

    def cancel_order(self, user, order_id, variety="regular"):
        body, _status, _latency = self.request("DELETE", f"/orders/{variety}/{order_id}", user)
        return body

    def modify_order(self, user, order_id, payload):
        variety = str(payload.get("variety") or "regular").lower()
        body, _status, _latency = self.request("PUT", f"/orders/{variety}/{order_id}", user, data=payload)
        return body

    def disconnect(self, user):
        now = self.now()
        try:
            self.request("DELETE", "/session/token", user, require_valid=False)
        except Exception:
            pass
        self.db["apis"].update_one(
            {"user": user, "broker": self.BROKER_ID},
            {
                "$set": {
                    "isConnected": False,
                    "connectionStatus": "disconnected",
                    "token_status": "disconnected",
                    "updatedAt": now,
                },
                "$unset": {"accessTokenEncrypted": "", "access_token": "", "refreshTokenEncrypted": ""},
            },
        )
        self.db["broker_health"].update_one(
            {"user": user, "broker": self.BROKER_ID},
            {"$set": {"login_status": "disconnected", "token_status": "disconnected", "updated_at": now}},
            upsert=True,
        )

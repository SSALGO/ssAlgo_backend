import contextlib
import datetime
import io

from app.core.secrets import encrypt_secret
from app.core.trading_debug import trading_event, trading_exception
from .live_base import NormalizedLiveBrokerAdapter


def load_trade_hub():
    try:
        from TradeMaster.TradeSync import TradeHub
    except ModuleNotFoundError as exc:
        missing_module = exc.name or "unknown"
        raise ImportError(
            f"AliceBlue SDK could not load because Python module '{missing_module}' is missing. "
            "Reinstall the backend requirements and redeploy."
        ) from exc
    except ImportError as exc:
        raise ImportError(f"AliceBlue SDK could not load: {exc}") from exc
    return TradeHub


class AliceBlueBrokerAdapter(NormalizedLiveBrokerAdapter):
    broker_name = "aliceblue"

    PRODUCT_MAP = {
        "MIS": "INTRADAY",
        "INTRADAY": "INTRADAY",
        "CNC": "LONGTERM",
        "NRML": "LONGTERM",
        "DELIVERY": "LONGTERM",
        "LONGTERM": "LONGTERM",
    }

    ORDER_TYPE_MAP = {"MARKET": "MARKET", "MKT": "MARKET", "LIMIT": "LIMIT", "L": "LIMIT"}

    def login(self, credentials):
        values = self.load_credentials(credentials)
        user_id = str(values.get("apikey") or "").strip()
        auth_code = str(values.get("auth_code") or "").strip()
        secret_key = str(values.get("apisecret") or "").strip()
        session_id = str(values.get("user_session") or values.get("sessionID") or "").strip() or None
        if not user_id or not secret_key or not (auth_code or session_id):
            raise ValueError("AliceBlue requires apikey, apisecret, and auth_code or sessionID")

        TradeHub = load_trade_hub()
        self.client = TradeHub(user_id=user_id, auth_code=auth_code, secret_key=secret_key, session_id=session_id)
        with contextlib.redirect_stdout(io.StringIO()):
            response = self.client.get_session_id(session_id=session_id) if session_id else self.client.get_session_id()
        normalized = self.normalize_response("login", response, submitted_status="connected")

        def validate_session(candidate_response):
            session_value = None
            if isinstance(candidate_response, dict):
                session_value = (
                    candidate_response.get("sessionID")
                    or candidate_response.get("userSession")
                )
            if not session_value:
                return None, {
                    "success": False,
                    "status": "rejected",
                    "message": "AliceBlue did not return a session token",
                }
            try:
                profile_response = self.client.get_profile()
                profile_result = self.normalize_response(
                    "profile",
                    profile_response,
                    submitted_status="connected",
                )
                profile_status = str(
                    profile_response.get("stat", "")
                    if isinstance(profile_response, dict)
                    else ""
                ).strip().lower()
                if profile_status in {"not_ok", "not ok"}:
                    profile_result["success"] = False
                    profile_result["status"] = "rejected"
                    profile_result["message"] = (
                        profile_response.get("emsg")
                        or profile_response.get("message")
                        or "AliceBlue rejected the saved session"
                    )
            except Exception as exc:
                trading_exception(
                    "broker_login_profile_error",
                    exc,
                    user=credentials.user,
                    broker=self.broker_name,
                )
                profile_result = {
                    "success": False,
                    "broker": self.broker_name,
                    "action": "profile",
                    "status": "rejected",
                    "message": str(exc),
                    "raw": None,
                }
            return session_value, profile_result

        session_value, profile_result = validate_session(response)
        if session_id and not profile_result["success"] and auth_code:
            self.client = TradeHub(
                user_id=user_id,
                auth_code=auth_code,
                secret_key=secret_key,
            )
            with contextlib.redirect_stdout(io.StringIO()):
                response = self.client.get_session_id()
            normalized = self.normalize_response(
                "login", response, submitted_status="connected"
            )
            session_value, profile_result = validate_session(response)

        if session_value:
            normalized["success"] = bool(profile_result["success"])
            normalized["status"] = (
                "connected" if normalized["success"] else "rejected"
            )
            normalized["message"] = (
                "ok" if normalized["success"] else profile_result["message"]
            )
            normalized["profile_check"] = profile_result
            if normalized["success"] and self.db is not None:
                encrypted_session = encrypt_secret(session_value)
                self.db["apis"].update_one(
                    {"user": credentials.user, "broker": self.broker_name},
                    {
                        "$set": {
                            "user_session": encrypted_session,
                            "sessionID": encrypted_session,
                            "session_date": datetime.datetime.now().strftime("%Y-%m-%d"),
                        }
                    },
                    upsert=True,
                )
        else:
            normalized["success"] = False
            normalized["status"] = "rejected"
            normalized["message"] = profile_result["message"]
        self.update_login_health(credentials.user, normalized["success"], normalized["message"])
        return normalized

    @staticmethod
    def _instrument_kwargs(metadata):
        if metadata.get("instrument"):
            return {"instrument": metadata["instrument"]}
        return {
            "instrumentId": metadata.get("instrumentId") or metadata.get("instrument_id") or metadata.get("token") or metadata.get("optiontoken"),
            "exchange": metadata.get("exchange") or metadata.get("exch"),
        }

    def place_order(self, order):
        self.check_risk(order, mode="live")
        client = self.client_or_login(order.user)
        metadata = dict(order.metadata or {})
        order_type = self.ORDER_TYPE_MAP.get(str(order.order_type or "MARKET").upper(), str(order.order_type or "MARKET").upper())
        product = self.PRODUCT_MAP.get(str(order.product_type or metadata.get("product") or "LONGTERM").upper(), order.product_type or "LONGTERM")
        request_payload = {
            "transactionType": str(order.side).upper(),
            "quantity": int(order.quantity),
            "orderComplexity": metadata.get("orderComplexity") or "REGULAR",
            "product": product,
            "orderType": order_type,
            "price": "" if order_type == "MARKET" else order.price or 0,
            "slTriggerPrice": metadata.get("trigger_price") or "",
            "validity": metadata.get("validity") or "DAY",
            "orderTag": metadata.get("orderTag") or "ssalgo",
            **self._instrument_kwargs(metadata),
        }
        trading_event(
            "broker_api_request",
            user=order.user,
            broker=self.broker_name,
            strategy_id=order.strategy_id,
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            payload=request_payload,
        )
        try:
            response = client.placeOrder(**request_payload)
        except Exception as exc:
            trading_exception(
                "broker_api_error",
                exc,
                user=order.user,
                broker=self.broker_name,
                strategy_id=order.strategy_id,
                symbol=order.symbol,
                side=order.side,
                quantity=order.quantity,
            )
            raise
        normalized = self.normalize_response("place_order", response)
        return self.record_order_result(order, normalized, raw_request=request_payload)

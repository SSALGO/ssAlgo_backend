import contextlib
import io

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
        if isinstance(response, dict) and (response.get("sessionID") or response.get("userSession")):
            normalized["success"] = True
            normalized["status"] = "connected"
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

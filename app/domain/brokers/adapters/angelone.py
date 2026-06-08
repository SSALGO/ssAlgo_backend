import pyotp

from .live_base import NormalizedLiveBrokerAdapter


class AngelOneBrokerAdapter(NormalizedLiveBrokerAdapter):
    broker_name = "angelone"

    def login(self, credentials):
        values = self.load_credentials(credentials)
        required = ["apikey", "client_id", "pwd", "totp_key"]
        missing = [field for field in required if not str(values.get(field) or "").strip()]
        if missing:
            raise ValueError(f"Angel One missing credentials: {', '.join(missing)}")
        try:
            from SmartApi import SmartConnect
        except ImportError as exc:
            raise ImportError("SmartApi package is required for Angel One live trading") from exc
        self.client = SmartConnect(api_key=values["apikey"])
        response = self.client.generateSession(values["client_id"], values["pwd"], pyotp.TOTP(values["totp_key"]).now())
        normalized = self.normalize_response("login", response, submitted_status="connected")
        if isinstance(response, dict) and response.get("data"):
            normalized["success"] = True
            normalized["status"] = "connected"
        self.update_login_health(credentials.user, normalized["success"], normalized["message"])
        return normalized

    def place_order(self, order):
        self.check_risk(order, mode="live")
        client = self.client_or_login(order.user)
        metadata = dict(order.metadata or {})
        params = {
            "variety": metadata.get("variety") or "NORMAL",
            "tradingsymbol": metadata.get("tradingsymbol") or order.symbol,
            "symboltoken": str(metadata.get("symboltoken") or metadata.get("token") or metadata.get("optiontoken") or ""),
            "transactiontype": str(order.side).upper(),
            "exchange": order.exchange or metadata.get("exchange") or metadata.get("exch") or "NFO",
            "ordertype": str(order.order_type or "MARKET").upper(),
            "producttype": metadata.get("producttype") or order.product_type or "CARRYFORWARD",
            "duration": metadata.get("duration") or "DAY",
            "price": str(order.price or 0),
            "squareoff": str(metadata.get("squareoff") or 0),
            "stoploss": str(metadata.get("stoploss") or 0),
            "quantity": int(order.quantity),
        }
        normalized = self.normalize_response("place_order", client.placeOrder(params))
        return self.record_order_result(order, normalized, raw_request=params)

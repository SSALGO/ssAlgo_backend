from .live_base import NormalizedLiveBrokerAdapter


class FyersBrokerAdapter(NormalizedLiveBrokerAdapter):
    broker_name = "fyers"

    EXCHANGE_MAP = {"MCX": "MCX", "MFO": "MCX", "NFO": "NSE", "NSE": "NSE", "BSE": "BSE", "BFO": "BSE"}

    def login(self, credentials):
        values = self.load_credentials(credentials)
        client_id = str(values.get("client_id") or "").strip()
        access_token = str(values.get("access_token") or values.get("token") or "").strip()
        if not client_id or not access_token:
            raise ValueError("Fyers client_id and access_token are required")
        try:
            from fyers_apiv3 import fyersModel
        except ImportError as exc:
            raise ImportError("fyers_apiv3 package is required for Fyers live trading") from exc
        self.client = fyersModel.FyersModel(client_id=client_id, is_async=False, token=access_token, log_path="")
        response = self.client.get_profile()
        normalized = self.normalize_response("login", response, submitted_status="connected")
        if isinstance(response, dict) and response.get("code") == 200:
            normalized["success"] = True
            normalized["status"] = "connected"
        self.update_login_health(credentials.user, normalized["success"], normalized["message"])
        return normalized

    def place_order(self, order):
        self.check_risk(order, mode="live")
        client = self.client_or_login(order.user)
        metadata = dict(order.metadata or {})
        symbol = metadata.get("fyers_symbol") or metadata.get("formatted_symbol") or order.symbol
        if ":" not in str(symbol):
            exchange = self.EXCHANGE_MAP.get(str(order.exchange or metadata.get("exchange") or metadata.get("exch") or "NSE").upper(), "NSE")
            symbol = f"{exchange}:{symbol}"
        data = {
            "symbol": symbol,
            "qty": int(order.quantity),
            "type": int(metadata.get("type") or (1 if str(order.order_type).upper() == "LIMIT" else 2)),
            "side": 1 if str(order.side).upper() == "BUY" else -1,
            "productType": metadata.get("productType") or order.product_type or "MARGIN",
            "limitPrice": float(order.price or metadata.get("limitPrice") or 0),
            "stopPrice": float(metadata.get("stopPrice") or 0),
            "validity": metadata.get("validity") or "DAY",
            "disclosedQty": int(metadata.get("disclosedQty") or 0),
            "offlineOrder": bool(metadata.get("offlineOrder") or False),
            "orderTag": metadata.get("orderTag") or "ssalgo",
            "stopLoss": float(metadata.get("stopLoss") or 0),
            "takeProfit": float(metadata.get("takeProfit") or 0),
        }
        normalized = self.normalize_response("place_order", client.place_order(data=data))
        return self.record_order_result(order, normalized, raw_request=data)

from .live_base import NormalizedLiveBrokerAdapter


class ZerodhaBrokerAdapter(NormalizedLiveBrokerAdapter):
    broker_name = "zerodha"

    def login(self, credentials):
        values = self.load_credentials(credentials)
        api_key = str(values.get("api_key") or "").strip()
        access_token = str(values.get("access_token") or "").strip()
        if not api_key or not access_token:
            raise ValueError("Zerodha api_key and access_token are required; generate access_token outside the adapter")
        try:
            from kiteconnect import KiteConnect
        except ImportError as exc:
            raise ImportError("kiteconnect package is required for Zerodha live trading") from exc
        self.client = KiteConnect(api_key=api_key)
        self.client.set_access_token(access_token)
        response = self.client.profile() if hasattr(self.client, "profile") else {"status": "success"}
        normalized = self.normalize_response("login", response, submitted_status="connected")
        self.update_login_health(credentials.user, normalized["success"], normalized["message"])
        return normalized

    def place_order(self, order):
        self.check_risk(order, mode="live")
        client = self.client_or_login(order.user)
        metadata = dict(order.metadata or {})
        response = client.place_order(
            tradingsymbol=metadata.get("tradingsymbol") or order.symbol,
            exchange=order.exchange or metadata.get("exchange") or metadata.get("exch") or "NFO",
            transaction_type=str(order.side).upper(),
            quantity=int(order.quantity),
            variety=metadata.get("variety") or "regular",
            order_type=str(order.order_type or "MARKET").upper(),
            product=metadata.get("product") or order.product_type or "NRML",
            validity=metadata.get("validity") or "DAY",
            price=float(order.price or 0),
        )
        normalized = self.normalize_response("place_order", response, broker_order_id=response if isinstance(response, str) else None)
        return self.record_order_result(order, normalized, raw_request=metadata)

from .live_base import NormalizedLiveBrokerAdapter


class DhanBrokerAdapter(NormalizedLiveBrokerAdapter):
    broker_name = "dhan"

    EXCHANGE_SEGMENTS = {
        "NSE": "NSE_EQ",
        "NSECM": "NSE_EQ",
        "BSE": "BSE_EQ",
        "BSECM": "BSE_EQ",
        "NFO": "NSE_FNO",
        "NSEFO": "NSE_FNO",
        "BFO": "BSE_FNO",
        "BSEFO": "BSE_FNO",
        "MCX": "MCX_COMM",
        "MCXFO": "MCX_COMM",
        "CDS": "NSE_CURRENCY",
        "CDSFO": "NSE_CURRENCY",
    }

    def _load_credentials(self, credentials):
        return self.load_credentials(credentials)

    def _normalize_response(self, action, response, broker_order_id=None):
        return self.normalize_response(action, response, broker_order_id=broker_order_id)

    def login(self, credentials):
        values = self._load_credentials(credentials)
        client_id = str(values.get("client_id") or "").strip()
        access_token = str(values.get("access_token") or "").strip()
        if not client_id or not access_token:
            raise ValueError("Dhan client_id and access_token are required")
        try:
            from dhanhq import dhanhq
        except ImportError as exc:
            raise ImportError("dhanhq package is required for Dhan live trading") from exc

        self.credentials = values
        self.client = dhanhq(client_id, access_token)
        response = self.client.get_fund_limits()
        normalized = self._normalize_response("login", response)
        normalized["status"] = "connected" if normalized["success"] else "rejected"
        self.update_login_health(credentials.user, normalized["success"], normalized["message"])
        return normalized

    def _exchange_segment(self, exchange):
        return self.EXCHANGE_SEGMENTS.get(str(exchange or "").upper(), exchange or "NSE_FNO")

    def place_order(self, order):
        self.check_risk(order, mode="live")
        client = self.client_or_login(order.user)
        metadata = dict(order.metadata or {})
        security_id = metadata.get("security_id") or metadata.get("optiontoken") or metadata.get("token")
        if not security_id:
            raise ValueError("Dhan order requires security_id, optiontoken, or token in metadata")
        response = client.place_order(
            security_id=str(security_id),
            exchange_segment=self._exchange_segment(order.exchange or metadata.get("exchange") or metadata.get("exch")),
            transaction_type=str(order.side).upper(),
            quantity=int(order.quantity),
            order_type=str(order.order_type or "MARKET").upper(),
            product_type=str(order.product_type or metadata.get("product_type") or "MARGIN").upper(),
            price=float(order.price or 0),
            trigger_price=float(metadata.get("trigger_price") or 0),
            disclosed_quantity=int(metadata.get("disclosed_quantity") or 0),
            after_market_order=bool(metadata.get("after_market_order") or False),
            validity=metadata.get("validity") or "DAY",
            amo_time=metadata.get("amo_time") or "OPEN",
            bo_profit_value=metadata.get("bo_profit_value"),
            bo_stop_loss_Value=metadata.get("bo_stop_loss_Value"),
            tag=metadata.get("tag"),
        )
        normalized = self._normalize_response("place_order", response)
        return self.record_order_result(order, normalized, raw_request=metadata)

    def cancel_order(self, user, order_id):
        client = self.client_or_login(user)
        response = client.cancel_order(order_id)
        normalized = self._normalize_response("cancel_order", response, broker_order_id=order_id)
        if self.order_lifecycle and normalized["success"]:
            self.order_lifecycle.transition(order_id, "cancelled", normalized)
        return normalized

    def positions(self, user):
        return self._normalize_response("positions", self.client_or_login(user).get_positions())

    def funds(self, user):
        return self._normalize_response("funds", self.client_or_login(user).get_fund_limits())

    def quote(self, symbol, **kwargs):
        client = self.client_or_login(kwargs.get("user") or "")
        security_id = kwargs.get("security_id") or kwargs.get("token")
        exchange_segment = self._exchange_segment(kwargs.get("exchange"))
        if hasattr(client, "ticker_data") and security_id:
            return self._normalize_response("quote", client.ticker_data(exchange_segment, str(security_id)))
        return {"success": False, "broker": self.broker_name, "action": "quote", "message": "Dhan quote requires security_id"}

    def subscribe(self, symbols, **kwargs):
        return {
            "success": False,
            "broker": self.broker_name,
            "action": "subscribe",
            "symbols": list(symbols or []),
            "message": "Dhan websocket subscription belongs in the worker process",
        }

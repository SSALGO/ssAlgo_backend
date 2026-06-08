from .live_base import NormalizedLiveBrokerAdapter


class SMCBrokerAdapter(NormalizedLiveBrokerAdapter):
    broker_name = "smc"

    EXCHANGE_MAP = {"NFO": "NSEFO", "NSE": "NSECM", "BSE": "BSECM", "BFO": "BSEFO", "MCX": "MCXFO"}

    def login(self, credentials):
        values = self.load_credentials(credentials)
        required = ["interactive_key", "interactive_secret", "source"]
        missing = [field for field in required if not str(values.get(field) or "").strip()]
        if missing:
            raise ValueError(f"SMC missing credentials: {', '.join(missing)}")
        from XTSConnect import XTSConnect
        self.client = XTSConnect(values["interactive_key"], values["interactive_secret"], values.get("source") or "WEBAPI")
        response = self.client.interactive_login()
        normalized = self.normalize_response("login", response, submitted_status="connected")
        self.update_login_health(credentials.user, normalized["success"], normalized["message"])
        return normalized

    def place_order(self, order):
        self.check_risk(order, mode="live")
        client = self.client_or_login(order.user)
        metadata = dict(order.metadata or {})
        payload = {
            "exchangeSegment": self.EXCHANGE_MAP.get(str(order.exchange or metadata.get("exchange") or metadata.get("exch") or "NFO").upper(), "NSEFO"),
            "exchangeInstrumentID": int(metadata.get("instrument_id") or metadata.get("token") or metadata.get("optiontoken") or 0),
            "productType": metadata.get("productType") or order.product_type or "NRML",
            "orderType": str(order.order_type or "MARKET").upper(),
            "orderSide": str(order.side).upper(),
            "timeInForce": metadata.get("timeInForce") or "DAY",
            "disclosedQuantity": int(metadata.get("disclosedQuantity") or 0),
            "orderQuantity": int(order.quantity),
            "limitPrice": float(order.price or 0),
            "stopPrice": float(metadata.get("stopPrice") or 0),
            "apiOrderSource": metadata.get("apiOrderSource") or "WEBAPI",
            "orderUniqueIdentifier": metadata.get("orderUniqueIdentifier") or "ssalgo",
        }
        normalized = self.normalize_response("place_order", client.place_order(**payload))
        return self.record_order_result(order, normalized, raw_request=payload)

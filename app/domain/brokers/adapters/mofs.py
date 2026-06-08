import pyotp

from .live_base import NormalizedLiveBrokerAdapter


class MotilalOswalBrokerAdapter(NormalizedLiveBrokerAdapter):
    broker_name = "mofs"

    EXCHANGE_MAP = {"NFO": "NSEFO", "NSE": "NSE", "BSE": "BSE", "BFO": "BSEFO", "MCX": "MCX"}

    def login(self, credentials):
        values = self.load_credentials(credentials)
        required = ["api_key", "client_id", "password", "_2_FA", "totp_key"]
        missing = [field for field in required if not str(values.get(field) or "").strip()]
        if missing:
            raise ValueError(f"Motilal Oswal missing credentials: {', '.join(missing)}")
        from MOFSLOPENAPI import MOFSLOPENAPI
        self.client = MOFSLOPENAPI(values["api_key"], "https://openapi.motilaloswal.com", values["client_id"], "Desktop", "chrome", "104")
        response = self.client.login(
            f_clientID=values["client_id"],
            f_password=values["password"],
            f_twoFA=values["_2_FA"],
            f_totp=pyotp.TOTP(values["totp_key"]).now(),
            f_vendorinfo=values["client_id"],
        )
        normalized = self.normalize_response("login", response, submitted_status="connected")
        self.update_login_health(credentials.user, normalized["success"], normalized["message"])
        return normalized

    def place_order(self, order):
        self.check_risk(order, mode="live")
        client = self.client_or_login(order.user)
        metadata = dict(order.metadata or {})
        values = self.credentials
        payload = {
            "clientcode": values.get("client_id"),
            "exchange": self.EXCHANGE_MAP.get(str(order.exchange or metadata.get("exchange") or metadata.get("exch") or "NSE").upper(), "NSE"),
            "symboltoken": str(metadata.get("symboltoken") or metadata.get("token") or metadata.get("optiontoken") or ""),
            "buyorsell": str(order.side).upper(),
            "ordertype": str(order.order_type or "MARKET").upper(),
            "producttype": metadata.get("producttype") or "NORMAL",
            "orderduration": metadata.get("orderduration") or "DAY",
            "price": float(order.price or 0),
            "triggerprice": float(metadata.get("triggerprice") or 0),
            "quantityinlot": int(metadata.get("quantityinlot") or order.quantity),
            "disclosedquantity": int(metadata.get("disclosedquantity") or 0),
            "amoorder": metadata.get("amoorder") or "N",
            "algoid": metadata.get("algoid") or "",
            "tag": metadata.get("tag") or "ssalgo",
        }
        normalized = self.normalize_response("place_order", client.PlaceOrder(payload))
        return self.record_order_result(order, normalized, raw_request=payload)

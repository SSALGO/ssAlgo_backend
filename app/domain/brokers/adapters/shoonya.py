import pyotp

from .live_base import NormalizedLiveBrokerAdapter


class ShoonyaBrokerAdapter(NormalizedLiveBrokerAdapter):
    broker_name = "shoonya"

    def login(self, credentials):
        values = self.load_credentials(credentials)
        required = ["usr", "pwd", "factor2", "apikey"]
        missing = [field for field in required if not str(values.get(field) or "").strip()]
        if missing:
            raise ValueError(f"Shoonya missing credentials: {', '.join(missing)}")
        try:
            from NorenRestApiPy.NorenApi import NorenApi
        except ImportError as exc:
            raise ImportError("NorenRestApiPy is required for Shoonya live trading") from exc

        class ShoonyaApiPy(NorenApi):
            def __init__(self):
                super().__init__(
                    host="https://api.shoonya.com/NorenWClientTP/",
                    websocket="wss://api.shoonya.com/NorenWSTP/",
                )

        self.client = ShoonyaApiPy()
        response = self.client.login(
            userid=values["usr"],
            password=values["pwd"],
            twoFA=str(pyotp.TOTP(values["factor2"]).now()),
            vendor_code=f"{values['usr']}_U",
            api_secret=values["apikey"],
            imei=values.get("imei") or "abc1234",
        )
        normalized = self.normalize_response("login", response, submitted_status="connected")
        if isinstance(response, dict) and response.get("susertoken"):
            normalized["success"] = True
            normalized["status"] = "connected"
        self.update_login_health(credentials.user, normalized["success"], normalized["message"])
        return normalized

    def place_order(self, order):
        self.check_risk(order, mode="live")
        client = self.client_or_login(order.user)
        metadata = dict(order.metadata or {})
        response = client.place_order(
            buy_or_sell="B" if str(order.side).upper() == "BUY" else "S",
            product_type=metadata.get("product_type") or order.product_type or "M",
            exchange=order.exchange or metadata.get("exchange") or metadata.get("exch") or "NFO",
            tradingsymbol=metadata.get("tradingsymbol") or order.symbol,
            quantity=int(order.quantity),
            discloseqty=int(metadata.get("discloseqty") or 0),
            price_type="MKT" if str(order.order_type).upper() == "MARKET" else str(order.order_type).upper(),
            price=float(order.price or 0),
            trigger_price=float(metadata.get("trigger_price") or 0),
            retention=metadata.get("retention") or "DAY",
            remarks=metadata.get("remarks") or "ssalgo",
        )
        normalized = self.normalize_response("place_order", response)
        return self.record_order_result(order, normalized, raw_request=metadata)

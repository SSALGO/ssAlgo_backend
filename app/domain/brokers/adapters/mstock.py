import requests

from .live_base import NormalizedLiveBrokerAdapter


class MStockBrokerAdapter(NormalizedLiveBrokerAdapter):
    broker_name = "mstock"

    BASE_URL = "https://api.mstock.trade/openapi/typea"

    def login(self, credentials):
        values = self.load_credentials(credentials)
        apikey = str(values.get("apikey") or "").strip()
        access_token = str(values.get("access_token") or "").strip()
        if not apikey or not access_token:
            raise ValueError("mStock apikey and access_token are required")
        self.client = requests.Session()
        self.client.headers.update({
            "X-Mirae-Version": "1",
            "Authorization": f"token {apikey}:{access_token}",
            "Content-Type": "application/x-www-form-urlencoded",
        })
        response = {"status": "success", "message": "mStock credentials loaded"}
        normalized = self.normalize_response("login", response, submitted_status="connected")
        self.update_login_health(credentials.user, normalized["success"], normalized["message"])
        return normalized

    def place_order(self, order):
        self.check_risk(order, mode="live")
        session = self.client_or_login(order.user)
        metadata = dict(order.metadata or {})
        payload = {
            "tradingsymbol": metadata.get("tradingsymbol") or order.symbol,
            "exchange": order.exchange or metadata.get("exchange") or metadata.get("exch") or "NFO",
            "transaction_type": str(order.side).upper(),
            "order_type": str(order.order_type or "MARKET").upper(),
            "quantity": int(order.quantity),
            "product": metadata.get("product") or order.product_type or "NRML",
            "validity": metadata.get("validity") or "DAY",
            "price": str(order.price or 0),
            "variety": metadata.get("variety") or "regular",
        }
        response = session.post(f"{self.BASE_URL}/orders/regular", data=payload, timeout=15)
        try:
            raw = response.json()
        except ValueError:
            raw = {"status": "error", "message": response.text, "http_status": response.status_code}
        normalized = self.normalize_response("place_order", raw)
        return self.record_order_result(order, normalized, raw_request=payload)

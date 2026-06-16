from .live_base import NormalizedLiveBrokerAdapter
from app.domain.brokers.kite import KiteService, KiteTokenExpired


class ZerodhaBrokerAdapter(NormalizedLiveBrokerAdapter):
    broker_name = "zerodha"

    def login(self, credentials):
        service = KiteService(self.db)
        response = service.get_profile(credentials.user)
        normalized = self.normalize_response("login", response, submitted_status="connected")
        self.update_login_health(credentials.user, normalized["success"], normalized["message"])
        return normalized

    def place_order(self, order):
        self.check_risk(order, mode="live")
        metadata = dict(order.metadata or {})
        payload = {
            "tradingsymbol": metadata.get("tradingsymbol") or order.symbol,
            "exchange": order.exchange or metadata.get("exchange") or metadata.get("exch") or "NFO",
            "transaction_type": str(order.side).upper(),
            "quantity": int(order.quantity),
            "variety": metadata.get("variety") or "regular",
            "order_type": str(order.order_type or "MARKET").upper(),
            "product": metadata.get("product") or order.product_type or "NRML",
            "validity": metadata.get("validity") or "DAY",
            "price": order.price,
            "trigger_price": metadata.get("trigger_price"),
            "strategy_id": order.strategy_id,
            "source": metadata.get("source") or "STRATEGY",
        }
        try:
            response = KiteService(self.db).place_order(order.user, payload)
        except KiteTokenExpired as exc:
            response = {"status": "error", "message": str(exc)}
        broker_order_id = (response.get("data") or {}).get("order_id") if isinstance(response, dict) else None
        normalized = self.normalize_response("place_order", response, broker_order_id=broker_order_id)
        return self.record_order_result(order, normalized, raw_request=metadata)

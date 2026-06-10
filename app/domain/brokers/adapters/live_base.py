from .base import BrokerAdapter
from app.core.secrets import decrypt_secret_fields
from app.domain.brokers.health import SECRET_FIELD_NAMES


class NormalizedLiveBrokerAdapter(BrokerAdapter):
    """Common helpers for broker adapters migrated out of connector.py."""

    ORDER_ID_KEYS = (
        "order_id",
        "orderId",
        "orderid",
        "norenordno",
        "id",
        "data",
        "UniqueOrderID",
        "uniqueorderid",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.client = None
        self.credentials = {}

    def load_credentials(self, credentials):
        values = dict(credentials.values or {})
        if self.db is not None:
            saved = self.db["apis"].find_one({"user": credentials.user, "broker": self.broker_name}) or {}
            values = {**saved, **values}
        values = decrypt_secret_fields(values, SECRET_FIELD_NAMES)
        self.credentials = values
        return values

    def normalize_response(self, action, response, broker_order_id=None, submitted_status="submitted"):
        success = True
        message = "ok"
        status = submitted_status
        raw = response

        if isinstance(response, dict):
            status_text = " ".join(
                str(response.get(key, ""))
                for key in ("status", "Status", "message", "Message", "remarks", "emsg", "error", "Error")
            ).lower()
            if any(word in status_text for word in ("fail", "error", "reject", "invalid", "unauthor", "not_ok", "not ok")):
                success = False
                status = "rejected"
                message = (
                    response.get("message")
                    or response.get("Message")
                    or response.get("remarks")
                    or response.get("emsg")
                    or response.get("error")
                    or response.get("Error")
                    or "Broker rejected request"
                )
            for key in self.ORDER_ID_KEYS:
                if not broker_order_id and response.get(key):
                    broker_order_id = response.get(key)
        elif response is False or response is None:
            success = False
            status = "rejected"
            message = "Empty broker response"

        return {
            "success": success,
            "broker": self.broker_name,
            "action": action,
            "status": status,
            "message": message,
            "broker_order_id": str(broker_order_id) if broker_order_id else None,
            "raw": raw,
        }

    def record_order_result(self, order, normalized, raw_request=None):
        order_id = None
        if self.order_lifecycle:
            order_id = self.order_lifecycle.create_order(
                user=order.user,
                broker=self.broker_name,
                symbol=order.symbol,
                side=order.side,
                quantity=order.quantity,
                strategy_id=order.strategy_id,
                requested_price=order.price,
                order_type=order.order_type,
                raw_request=raw_request or {},
            )
            self.order_lifecycle.transition(order_id, "submitted" if normalized["success"] else "rejected", normalized)
        normalized["order_id"] = order_id
        if self.health_service:
            self.health_service.update_health(
                order.user,
                self.broker_name,
                last_order_result=normalized,
                last_error="" if normalized["success"] else normalized["message"],
            )
        return normalized

    def update_login_health(self, user, success, message=""):
        if self.health_service:
            self.health_service.update_health(
                user,
                self.broker_name,
                login_status="connected" if success else "rejected",
                last_error="" if success else message,
            )

    def client_or_login(self, user):
        if self.client is None:
            self.login(type("Credentials", (), {"user": user, "broker": self.broker_name, "values": {}})())
        return self.client

    def cancel_order(self, user, order_id):
        client = self.client_or_login(user)
        for method_name in ("cancel_order", "cancelOrder", "cancelOrderById"):
            method = getattr(client, method_name, None)
            if callable(method):
                normalized = self.normalize_response("cancel_order", method(order_id), broker_order_id=order_id)
                if self.order_lifecycle and normalized["success"]:
                    self.order_lifecycle.transition(order_id, "cancelled", normalized)
                return normalized
        return {"success": False, "broker": self.broker_name, "action": "cancel_order", "message": "Cancel is not supported by adapter"}

    def positions(self, user):
        client = self.client_or_login(user)
        for method_name in ("positions", "get_positions", "positionbook", "getPosition"):
            method = getattr(client, method_name, None)
            if callable(method):
                return self.normalize_response("positions", method())
        return {"success": False, "broker": self.broker_name, "action": "positions", "message": "Positions are not supported by adapter"}

    def funds(self, user):
        client = self.client_or_login(user)
        for method_name in ("funds", "get_fund_limits", "get_funds", "rmsLimit", "GetLimits"):
            method = getattr(client, method_name, None)
            if callable(method):
                return self.normalize_response("funds", method())
        return {"success": False, "broker": self.broker_name, "action": "funds", "message": "Funds are not supported by adapter"}

    def quote(self, symbol, **kwargs):
        return {"success": False, "broker": self.broker_name, "action": "quote", "symbol": symbol, "message": "Quote is not implemented"}

    def subscribe(self, symbols, **kwargs):
        return {
            "success": False,
            "broker": self.broker_name,
            "action": "subscribe",
            "symbols": list(symbols or []),
            "message": "Websocket subscription belongs in the worker process",
        }

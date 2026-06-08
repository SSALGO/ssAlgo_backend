from .base import BrokerAdapter


class LegacyConnectorBrokerAdapter(BrokerAdapter):
    """Disabled adapter for brokers that are visible but not safe for live use."""

    broker_name = "legacy"

    def login(self, credentials):
        if self.health_service:
            self.health_service.update_health(
                credentials.user,
                self.broker_name,
                login_status="disabled",
                websocket_status="disabled",
                last_error="Live adapter migration is not complete",
            )
        return {
            "success": False,
            "broker": self.broker_name,
            "status": "disabled",
            "message": "Live adapter migration is not complete; broker is disabled for live trading.",
        }

    def place_order(self, order):
        return {
            "success": False,
            "broker": self.broker_name,
            "action": "place_order",
            "status": "rejected",
            "message": "Live adapter migration is not complete; order was not sent.",
        }

    def cancel_order(self, user, order_id):
        return {
            "success": False,
            "broker": self.broker_name,
            "action": "cancel_order",
            "status": "rejected",
            "broker_order_id": str(order_id),
            "message": "Live adapter migration is not complete; cancel was not sent.",
        }

    def positions(self, user):
        return []

    def funds(self, user):
        return {}

    def quote(self, symbol, **kwargs):
        return {}

    def subscribe(self, symbols, **kwargs):
        return {
            "success": False,
            "broker": self.broker_name,
            "action": "subscribe",
            "status": "disabled",
            "symbols": list(symbols or []),
            "message": "Live adapter migration is not complete; websocket subscription is disabled.",
        }

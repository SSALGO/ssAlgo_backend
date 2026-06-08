from .base import BrokerAdapter


class LegacyConnectorBrokerAdapter(BrokerAdapter):
    """Placeholder adapter for live brokers still implemented inside connector.py."""

    broker_name = "legacy"

    def login(self, credentials):
        return {
            "success": False,
            "broker": self.broker_name,
            "message": "Live adapter is still handled by connector.py and is not migrated yet.",
        }

    def place_order(self, order):
        raise NotImplementedError(f"{self.broker_name} adapter migration is not complete")

    def cancel_order(self, user, order_id):
        raise NotImplementedError(f"{self.broker_name} cancel_order is not migrated yet")

    def positions(self, user):
        return []

    def funds(self, user):
        return {}

    def quote(self, symbol, **kwargs):
        return {}

    def subscribe(self, symbols, **kwargs):
        return {"success": False, "symbols": symbols, "message": "Subscription is still handled by connector.py"}

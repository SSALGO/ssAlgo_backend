from .legacy import LegacyConnectorBrokerAdapter


class DeltaExchangeIndiaBrokerAdapter(LegacyConnectorBrokerAdapter):
    broker_name = "delta_exchange_india"

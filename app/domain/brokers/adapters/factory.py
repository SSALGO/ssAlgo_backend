from .aliceblue import AliceBlueBrokerAdapter
from .angelone import AngelOneBrokerAdapter
from .delta_exchange_india import DeltaExchangeIndiaBrokerAdapter
from .dhan import DhanBrokerAdapter
from .fyers import FyersBrokerAdapter
from .mofs import MotilalOswalBrokerAdapter
from .mstock import MStockBrokerAdapter
from .paper import PaperBrokerAdapter
from .shoonya import ShoonyaBrokerAdapter
from .smc import SMCBrokerAdapter
from .zerodha import ZerodhaBrokerAdapter


class BrokerAdapterFactory:
    ADAPTERS = {
        "aliceblue": AliceBlueBrokerAdapter,
        "angelone": AngelOneBrokerAdapter,
        "delta_exchange_india": DeltaExchangeIndiaBrokerAdapter,
        "dhan": DhanBrokerAdapter,
        "fyers": FyersBrokerAdapter,
        "mofs": MotilalOswalBrokerAdapter,
        "mstock": MStockBrokerAdapter,
        "paper": PaperBrokerAdapter,
        "shoonya": ShoonyaBrokerAdapter,
        "smc": SMCBrokerAdapter,
        "zerodha": ZerodhaBrokerAdapter,
    }

    def __init__(self, db=None, health_service=None, order_lifecycle=None, risk_service=None):
        self.db = db
        self.health_service = health_service
        self.order_lifecycle = order_lifecycle
        self.risk_service = risk_service

    def create(self, broker_name):
        adapter_class = self.ADAPTERS.get(broker_name)
        if not adapter_class:
            raise ValueError(f"Unsupported broker: {broker_name}")
        return adapter_class(
            db=self.db,
            health_service=self.health_service,
            order_lifecycle=self.order_lifecycle,
            risk_service=self.risk_service,
        )

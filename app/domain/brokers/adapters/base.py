from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from app.core.trading_debug import trading_event


@dataclass
class BrokerCredentials:
    user: str
    broker: str
    values: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BrokerOrder:
    user: str
    symbol: str
    side: str
    quantity: int
    broker: str = "paper"
    exchange: str = ""
    product_type: str = "INTRADAY"
    order_type: str = "MARKET"
    price: Optional[float] = None
    strategy_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class BrokerAdapter(ABC):
    broker_name = "base"

    def __init__(self, db=None, health_service=None, order_lifecycle=None, risk_service=None):
        self.db = db
        self.health_service = health_service
        self.order_lifecycle = order_lifecycle
        self.risk_service = risk_service

    def check_risk(self, order: BrokerOrder, mode="live"):
        if self.risk_service is None:
            return None
        result = self.risk_service.check_order(order, mode=mode)
        trading_event(
            "risk_validation_result",
            user=order.user,
            broker=order.broker,
            strategy_id=order.strategy_id,
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            mode=mode,
            allowed=result.allowed,
            reason=result.reason,
            checks=result.checks,
        )
        if not result.allowed:
            raise PermissionError(result.reason)
        return result

    @abstractmethod
    def login(self, credentials: BrokerCredentials):
        raise NotImplementedError

    @abstractmethod
    def place_order(self, order: BrokerOrder):
        raise NotImplementedError

    @abstractmethod
    def cancel_order(self, user, order_id):
        raise NotImplementedError

    @abstractmethod
    def positions(self, user):
        raise NotImplementedError

    @abstractmethod
    def funds(self, user):
        raise NotImplementedError

    @abstractmethod
    def quote(self, symbol, **kwargs):
        raise NotImplementedError

    @abstractmethod
    def subscribe(self, symbols, **kwargs):
        raise NotImplementedError

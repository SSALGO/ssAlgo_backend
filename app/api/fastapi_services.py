from app.core.database import get_database
from app.domain.backtesting.service import BacktestService
from app.domain.brokers.adapters import BrokerAdapterFactory
from app.domain.brokers.health import BrokerHealthService
from app.domain.audit.service import AuditLogService
from app.domain.orders.lifecycle import OrderLifecycleService
from app.domain.risk.service import RiskControlService


class FastAPITradingServices:
    def __init__(self, db=None):
        self.db = db if db is not None else get_database()
        self.audit = AuditLogService(self.db)
        self.order_lifecycle = OrderLifecycleService(self.db, audit_service=self.audit)
        self.health = BrokerHealthService(self.db)
        self.risk = RiskControlService(self.db)
        self.adapter_factory = BrokerAdapterFactory(
            db=self.db,
            health_service=self.health,
            order_lifecycle=self.order_lifecycle,
            risk_service=self.risk,
        )
        self.backtests = BacktestService()


_services = None


def get_trading_services():
    global _services
    if _services is None:
        _services = FastAPITradingServices()
    return _services

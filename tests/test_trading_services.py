import datetime
import sys
import types

from app.domain.backtesting.service import BacktestService
from app.domain.brokers.adapters import BrokerAdapterFactory, BrokerCredentials, BrokerOrder
from app.domain.brokers.health import BrokerHealthService
from app.domain.orders.lifecycle import OrderLifecycleService
from app.domain.risk.service import RiskControlService


def test_paper_order_creates_normalized_lifecycle(fake_db):
    db = fake_db
    lifecycle = OrderLifecycleService(db)
    risk = RiskControlService(db)
    adapter = BrokerAdapterFactory(db=db, order_lifecycle=lifecycle, risk_service=risk).create("paper")
    adapter.login(BrokerCredentials(user="alice", broker="paper"))

    result = adapter.place_order(BrokerOrder(
        user="alice",
        broker="paper",
        symbol="NIFTY",
        side="BUY",
        quantity=2,
        price=100,
    ))

    assert result["success"] is True
    assert result["status"] == "filled"
    order = lifecycle.get_order(result["order_id"])
    assert order["status"] == "filled"
    assert [event["status"] for event in order["events"]] == ["created", "submitted", "filled"]


def test_order_lifecycle_transition():
    from conftest import FakeDatabase

    lifecycle = OrderLifecycleService(FakeDatabase())
    order_id = lifecycle.create_order("alice", "paper", "NIFTY", "BUY", 1)
    order = lifecycle.transition(order_id, "cancelled", {"reason": "test"})
    assert order["status"] == "cancelled"
    assert order["events"][-1]["data"]["reason"] == "test"


def test_broker_status_reports_missing_credentials(fake_db):
    health = BrokerHealthService(fake_db)
    status = health.get_health("alice", "dhan")
    assert status["broker"] == "dhan"
    assert status["login_status"] == "missing_credentials"
    assert "client_id" in status["missing_credentials"]


def test_backtest_returns_required_metrics():
    candles = [
        {"close": 100}, {"close": 101}, {"close": 102}, {"close": 99},
        {"close": 98}, {"close": 104}, {"close": 106}, {"close": 103},
    ]
    result = BacktestService().run_sma_crossover(candles=candles, fast=2, slow=3)
    for key in ("win_rate", "drawdown", "profit_factor", "max_loss_streak", "equity_curve"):
        assert key in result
    assert isinstance(result["equity_curve"], list)


def test_risk_blocks_live_orders_by_default():
    from conftest import FakeDatabase

    risk = RiskControlService(FakeDatabase())
    result = risk.check_order(BrokerOrder(
        user="alice",
        broker="dhan",
        symbol="NIFTY",
        side="BUY",
        quantity=1,
    ))
    assert result.allowed is False
    assert "Live trading is disabled" in result.reason


def test_dhan_adapter_normalizes_order_response(monkeypatch):
    from conftest import FakeDatabase

    class FakeDhanClient:
        def __init__(self, client_id, access_token):
            self.client_id = client_id
            self.access_token = access_token

        def get_fund_limits(self):
            return {"status": "success", "message": "ok"}

        def place_order(self, **kwargs):
            return {"status": "success", "orderId": "D123", "kwargs": kwargs}

    module = types.SimpleNamespace(dhanhq=FakeDhanClient)
    monkeypatch.setitem(sys.modules, "dhanhq", module)

    db = FakeDatabase()
    db["apis"].insert_one({
        "user": "alice",
        "broker": "dhan",
        "client_id": "client",
        "access_token": "token",
    })
    db["risk_settings"].insert_one({
        "user": "alice",
        "live_enabled": True,
        "paper_only": False,
    })
    db["broker_health"].insert_one({
        "user": "alice",
        "broker": "dhan",
        "login_status": "connected",
        "websocket_status": "connected",
    })

    lifecycle = OrderLifecycleService(db)
    risk = RiskControlService(db)
    adapter = BrokerAdapterFactory(db=db, order_lifecycle=lifecycle, risk_service=risk).create("dhan")
    adapter.login(BrokerCredentials(user="alice", broker="dhan"))
    result = adapter.place_order(BrokerOrder(
        user="alice",
        broker="dhan",
        symbol="NIFTY",
        side="BUY",
        quantity=1,
        exchange="NFO",
        metadata={"security_id": "12345"},
    ))

    assert result["success"] is True
    assert result["broker_order_id"] == "D123"
    assert result["order_id"]


def _enable_live_for(fake_db, user, broker):
    fake_db["risk_settings"].insert_one({
        "user": user,
        "live_enabled": True,
        "paper_only": False,
    })
    fake_db["broker_health"].insert_one({
        "user": user,
        "broker": broker,
        "login_status": "connected",
        "websocket_status": "connected",
    })


def test_fyers_adapter_contract(monkeypatch, fake_db):
    class FakeFyersModel:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def get_profile(self):
            return {"code": 200, "message": "ok"}

        def place_order(self, data):
            return {"status": "success", "id": "FY123", "data": data}

    module = types.SimpleNamespace(fyersModel=types.SimpleNamespace(FyersModel=FakeFyersModel))
    monkeypatch.setitem(sys.modules, "fyers_apiv3", module)
    fake_db["apis"].insert_one({"user": "alice", "broker": "fyers", "client_id": "cid", "access_token": "token"})
    _enable_live_for(fake_db, "alice", "fyers")

    adapter = BrokerAdapterFactory(db=fake_db, order_lifecycle=OrderLifecycleService(fake_db), risk_service=RiskControlService(fake_db)).create("fyers")
    adapter.login(BrokerCredentials(user="alice", broker="fyers"))
    result = adapter.place_order(BrokerOrder(user="alice", broker="fyers", symbol="NIFTY24", side="BUY", quantity=1, exchange="NFO"))
    assert result["success"] is True
    assert result["broker_order_id"] == "FY123"


def test_zerodha_adapter_contract(monkeypatch, fake_db):
    class FakeKite:
        def __init__(self, api_key):
            self.api_key = api_key

        def set_access_token(self, token):
            self.token = token

        def profile(self):
            return {"status": "success"}

        def place_order(self, **kwargs):
            return "KITE123"

    monkeypatch.setitem(sys.modules, "kiteconnect", types.SimpleNamespace(KiteConnect=FakeKite))
    fake_db["apis"].insert_one({"user": "alice", "broker": "zerodha", "api_key": "key", "access_token": "token"})
    _enable_live_for(fake_db, "alice", "zerodha")

    adapter = BrokerAdapterFactory(db=fake_db, order_lifecycle=OrderLifecycleService(fake_db), risk_service=RiskControlService(fake_db)).create("zerodha")
    adapter.login(BrokerCredentials(user="alice", broker="zerodha"))
    result = adapter.place_order(BrokerOrder(user="alice", broker="zerodha", symbol="NIFTY24", side="SELL", quantity=1, exchange="NFO"))
    assert result["success"] is True
    assert result["broker_order_id"] == "KITE123"


def test_angelone_adapter_contract(monkeypatch, fake_db):
    class FakeSmartConnect:
        def __init__(self, api_key):
            self.api_key = api_key

        def generateSession(self, client_id, pwd, totp):
            return {"status": "success", "data": {"jwtToken": "jwt"}}

        def placeOrder(self, params):
            return {"status": "success", "orderid": "ANG123", "params": params}

    monkeypatch.setitem(sys.modules, "SmartApi", types.SimpleNamespace(SmartConnect=FakeSmartConnect))
    fake_db["apis"].insert_one({
        "user": "alice",
        "broker": "angelone",
        "apikey": "key",
        "client_id": "cid",
        "pwd": "pwd",
        "totp_key": "JBSWY3DPEHPK3PXP",
    })
    _enable_live_for(fake_db, "alice", "angelone")

    adapter = BrokerAdapterFactory(db=fake_db, order_lifecycle=OrderLifecycleService(fake_db), risk_service=RiskControlService(fake_db)).create("angelone")
    adapter.login(BrokerCredentials(user="alice", broker="angelone"))
    result = adapter.place_order(BrokerOrder(
        user="alice",
        broker="angelone",
        symbol="NIFTY24",
        side="BUY",
        quantity=1,
        exchange="NFO",
        metadata={"symboltoken": "123"},
    ))
    assert result["success"] is True

import datetime
import builtins
import sys
import types

import pytest

from app.domain.backtesting.service import BacktestService
from app.domain.brokers.adapters import BrokerAdapterFactory, BrokerCredentials, BrokerOrder
from app.domain.brokers.adapters.aliceblue import load_trade_hub
from app.domain.brokers.health import BrokerHealthService
from app.domain.audit.service import AuditLogService
from app.domain.orders.lifecycle import OrderLifecycleService
from app.domain.readiness.service import LiveReadinessService
from app.domain.reconciliation.service import BrokerReconciliationService
from app.domain.risk.service import RiskControlService
from app.workers.trading_worker import TradingWorker
from app.core.logging_config import sanitize_log_value
from app.core.secrets import decrypt_secret, encrypt_secret
from models import EMA_mode


def test_aliceblue_sdk_import_reports_nested_missing_dependency(monkeypatch):
    original_import = builtins.__import__

    def fail_sdk_import(name, *args, **kwargs):
        if name == "TradeMaster.TradeSync":
            raise ModuleNotFoundError("No module named 'setuptools'", name="setuptools")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fail_sdk_import)

    with pytest.raises(ImportError, match="Python module 'setuptools' is missing"):
        load_trade_hub()


def test_log_sanitizer_masks_camel_case_broker_session():
    sanitized = sanitize_log_value({
        "raw": {
            "userSession": "live-session-token",
            "status": "connected",
        }
    })

    assert sanitized["raw"]["userSession"] != "live-session-token"
    assert sanitized["raw"]["status"] == "connected"


def test_worker_refreshes_only_explicitly_selected_brokers(fake_db):
    fake_db["broker"].insert_one({"user": "alice", "selectedbroker": "aliceblue"})
    fake_db["broker"].insert_one({"user": "bob", "selectedbroker": "paper"})
    fake_db["strategies"].insert_one({"user": "alice", "status": "opened", "live": True})
    fake_db["strategies"].insert_one({"user": "bob", "position": "in", "live": True})
    fake_db["apis"].insert_one({"user": "alice", "broker": "aliceblue"})
    fake_db["apis"].insert_one({"user": "alice", "broker": "dhan"})
    fake_db["apis"].insert_one({"user": "charlie", "broker": "fyers"})

    worker = TradingWorker(db=fake_db)

    assert worker._users_with_broker_credentials() == [
        ("alice", "aliceblue"),
        ("bob", "paper"),
    ]


def test_worker_automatic_login_ignores_users_without_active_strategies(fake_db):
    fake_db["broker"].insert_one({"user": "active", "selectedbroker": "aliceblue"})
    fake_db["broker"].insert_one({"user": "inactive", "selectedbroker": "dhan"})
    fake_db["strategies"].insert_one({"user": "active", "status": "opened", "live": True})
    fake_db["strategies"].insert_one({"user": "inactive", "status": "closed", "live": True})

    worker = TradingWorker(db=fake_db)

    assert worker._users_with_broker_credentials() == [("active", "aliceblue")]


def test_worker_normalizes_selected_broker_alias(fake_db):
    fake_db["broker"].insert_one({"user": "alice", "selectedbroker": "delta"})
    fake_db["strategies"].insert_one({"user": "alice", "status": "opened", "live": True})

    worker = TradingWorker(db=fake_db)

    assert worker._users_with_broker_credentials() == [
        ("alice", "delta_exchange_india"),
    ]


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


def _algorithm_143_payload(**overrides):
    payload = {
        "botname": "Algo143Smoke",
        "user": "alice",
        "symbol": "NIFTY",
        "Expiry": "Current Week",
        "timeframe": "3m",
        "r1": "19",
        "k1": "20",
        "Newsignal": "true",
        "USEMA": "false",
        "ema": "200",
        "Intraday": "true",
        "FixedLot": "FixedLot",
        "BSmode": "true",
        "pct_point": "false",
        "pnlexit_tpslexit": "false",
        "strike": "0",
        "lot": "1",
        "initiallot": "1",
        "ttw": "0",
        "stepvalue": "1",
        "MultiFactor": "1",
        "candle1": "1",
        "candle2": "2",
        "slicing": "20",
        "DaysHead": "0",
        "RolloverTime": "13:01",
        "StartTime": "09:17",
        "ExitTime": "15:20",
        "trail": "1",
        "trail_stoploss": "1000",
        "tp": "24999",
        "sl": "24999",
        "status": "paused",
        "maxprofit": "100000",
        "maxloss": "100000",
        "live": "false",
        "position": "out",
        "botcode": "ALG143",
    }
    payload.update(overrides)
    return payload


def test_algorithm_143_options_configuration_loads_required_fields():
    strategy = EMA_mode(_algorithm_143_payload())

    assert strategy.strategy == "EMA"
    assert strategy.botcode == "ALG143"
    assert strategy.symbol == "NIFTY"
    assert strategy.timeframe == "3m"
    assert strategy.r1 == 19
    assert strategy.k1 == 20
    assert strategy.Newsignal is True
    assert strategy.USEMA is False
    assert strategy.BSmode is True
    assert strategy.lot == 1
    assert strategy.initiallot == 1
    assert strategy.trail == 1
    assert strategy.trail_stoploss == 1000
    assert strategy.tp == 24999
    assert strategy.sl == 24999
    assert strategy.live is False
    assert strategy.position == "out"


def test_algorithm_143_rejects_invalid_quantity_input():
    with pytest.raises(ValueError):
        EMA_mode(_algorithm_143_payload(lot="bad"))


def test_algorithm_143_rejects_missing_required_input():
    payload = _algorithm_143_payload()
    payload.pop("botname")

    with pytest.raises(KeyError):
        EMA_mode(payload)


def test_order_lifecycle_transition():
    from conftest import FakeDatabase

    lifecycle = OrderLifecycleService(FakeDatabase())
    order_id = lifecycle.create_order("alice", "paper", "NIFTY", "BUY", 1)
    order = lifecycle.transition(order_id, "cancelled", {"reason": "test"})
    assert order["status"] == "cancelled"
    assert order["events"][-1]["data"]["reason"] == "test"


def test_order_lifecycle_tracks_partial_and_rejected_statuses():
    from conftest import FakeDatabase

    lifecycle = OrderLifecycleService(FakeDatabase())
    partial_id = lifecycle.create_order("alice", "paper", "NIFTY", "BUY", 2, strategy_id="ALG143")
    partial = lifecycle.transition(partial_id, "partial_fill", {"filled_quantity": 1})
    assert partial["status"] == "partial_fill"
    assert partial["events"][-1]["data"]["filled_quantity"] == 1

    rejected_id = lifecycle.create_order("alice", "paper", "NIFTY", "BUY", 1, strategy_id="ALG143")
    rejected = lifecycle.transition(rejected_id, "rejected", {"reason": "Insufficient balance"})
    assert rejected["status"] == "rejected"
    assert rejected["events"][-1]["data"]["reason"] == "Insufficient balance"


def test_audit_log_masks_sensitive_details(fake_db):
    audit = AuditLogService(fake_db)
    row = audit.record(
        "broker_credentials_saved",
        user="alice",
        resource_type="broker_api",
        resource_id="dhan",
        details={"access_token": "secret-token", "broker": "dhan"},
    )

    assert row["details"]["access_token"] == "***"
    assert row["details"]["broker"] == "dhan"
    assert fake_db["audit_logs"].rows[0]["event"] == "broker_credentials_saved"


def test_audit_export_and_retention_prune(fake_db):
    audit = AuditLogService(fake_db)
    audit.record("recent_event", user="alice")
    fake_db["audit_logs"].insert_one({
        "event": "old_event",
        "user": "alice",
        "actor": "alice",
        "resource_type": "",
        "resource_id": "",
        "status": "success",
        "details": {},
        "created_at": datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=400),
    })

    csv_payload = audit.export_csv(user="alice")
    assert "recent_event" in csv_payload
    assert "old_event" in csv_payload

    deleted = audit.prune_older_than(365)
    assert deleted == 1
    assert [row["event"] for row in fake_db["audit_logs"].rows] == ["recent_event"]


def test_order_lifecycle_writes_audit_events(fake_db):
    audit = AuditLogService(fake_db)
    lifecycle = OrderLifecycleService(fake_db, audit_service=audit)

    order_id = lifecycle.create_order("alice", "paper", "NIFTY", "BUY", 1, strategy_id="S1", mode="paper")
    lifecycle.transition(order_id, "filled", {"fill_price": 100})
    events = [row["event"] for row in fake_db["audit_logs"].rows]

    assert "order_created" in events
    assert "order_filled" in events


def test_order_lifecycle_owner_lookup_and_transition():
    from conftest import FakeDatabase

    lifecycle = OrderLifecycleService(FakeDatabase())
    order_id = lifecycle.create_order("alice", "paper", "NIFTY", "BUY", 1)

    assert lifecycle.get_order_for_user(order_id, "bob") is None
    try:
        lifecycle.transition_for_user(order_id, "cancelled", "bob", {"reason": "test"})
    except ValueError as exc:
        assert "Order not found" in str(exc)
    else:
        raise AssertionError("Cross-user order transition should fail")


def test_broker_status_reports_missing_credentials(fake_db):
    health = BrokerHealthService(fake_db)
    status = health.get_health("alice", "dhan")
    assert status["broker"] == "dhan"
    assert status["login_status"] == "missing_credentials"
    assert "client_id" in status["missing_credentials"]


def test_broker_health_uses_legacy_selected_broker_from_apis(fake_db):
    fake_db["apis"].insert_one({
        "user": "alice",
        "broker": "dhan",
        "selected_broker": "dhan",
        "client_id": "1100980357",
        "access_token": "token",
    })

    health = BrokerHealthService(fake_db)
    status = health.get_health("alice", "dhan")

    assert health.active_broker("alice") == "dhan"
    assert status["active"] is True
    assert status["missing_credentials"] == []
    assert status["login_status"] == "not_tested"
    assert status["websocket_status"] == "not_tested"


def test_broker_health_falls_back_to_first_saved_api_broker(fake_db):
    fake_db["apis"].insert_one({
        "user": "alice",
        "broker": "aliceblue",
        "apikey": "1775863",
    })

    health = BrokerHealthService(fake_db)

    assert health.active_broker("alice") == "aliceblue"


def test_broker_saved_credentials_are_returned_masked(fake_db):
    fake_db["apis"].insert_one({
        "user": "alice",
        "broker": "aliceblue",
        "apikey": "1775863",
        "apisecret": "raw-secret",
        "auth_code": "raw-auth-code",
        "totp_key": "raw-totp",
        "alice_password": "raw-password",
        "sessionID": "raw-session",
        "app_key": "app-key",
    })

    saved = BrokerHealthService(fake_db).saved_credentials("alice")
    aliceblue = saved["aliceblue"]

    assert aliceblue["apikey"] == "1775863"
    assert aliceblue["app_key"] == "app-key"
    assert aliceblue["apisecret"] == ""
    assert aliceblue["auth_code"] == ""
    assert aliceblue["totp_key"] == ""
    assert aliceblue["alice_password"] == ""
    assert aliceblue["sessionID"] == ""
    assert aliceblue["secret_present"]["apisecret"] is True
    assert aliceblue["secret_present"]["auth_code"] is True
    assert aliceblue["secret_present"]["totp_key"] is True
    assert aliceblue["secret_present"]["alice_password"] is True
    assert aliceblue["secret_present"]["sessionID"] is True


def test_broker_legacy_delta_alias_is_canonicalized(fake_db):
    fake_db["apis"].insert_one({
        "user": "alice",
        "broker": "delta",
        "api_key": "key",
        "api_secret": "secret",
        "selectedbroker": "delta",
    })

    health = BrokerHealthService(fake_db)
    saved = health.saved_credentials("alice")

    assert health.active_broker("alice") == "delta_exchange_india"
    assert "delta_exchange_india" in saved
    assert "delta" not in saved
    assert saved["delta_exchange_india"]["api_secret"] == ""
    assert health.credential_row("alice", "delta_exchange_india")["broker"] == "delta"


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


def test_risk_uses_profile_trade_and_loss_limits(fake_db):
    fake_db["users"].insert_one({
        "username": "alice",
        "trade_limit": "1",
        "day_loss_limit": "100",
    })
    fake_db["normalized_orders"].insert_one({
        "user": "alice",
        "created_at": datetime.datetime.now(datetime.UTC),
    })
    risk = RiskControlService(fake_db)

    result = risk.check_order(BrokerOrder(user="alice", broker="paper", symbol="NIFTY", side="BUY", quantity=1), mode="paper")
    assert result.allowed is False
    assert "Daily order limit" in result.reason


def test_risk_rejects_invalid_order_shape(fake_db):
    risk = RiskControlService(fake_db)
    result = risk.check_order(BrokerOrder(user="alice", broker="paper", symbol="NIFTY", side="HOLD", quantity=1), mode="paper")
    assert result.allowed is False
    assert "side" in result.reason

    result = risk.check_order(BrokerOrder(user="alice", broker="paper", symbol="NIFTY", side="BUY", quantity=0), mode="paper")
    assert result.allowed is False
    assert "quantity" in result.reason


def test_risk_blocks_stale_live_quotes_when_enabled(fake_db):
    fake_db["risk_settings"].insert_one({
        "user": "alice",
        "live_enabled": True,
        "paper_only": False,
        "require_fresh_quote": True,
        "max_quote_age_seconds": 10,
    })
    fake_db["broker_health"].insert_one({
        "user": "alice",
        "broker": "dhan",
        "login_status": "connected",
        "websocket_status": "connected",
    })
    old_quote_time = datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=5)
    fake_db["market_quotes"].insert_one({"user": "alice", "symbol": "NIFTY", "updated_at": old_quote_time})

    result = RiskControlService(fake_db).check_order(BrokerOrder(
        user="alice",
        broker="dhan",
        symbol="NIFTY",
        side="BUY",
        quantity=1,
        strategy_id="S1",
        metadata={"idempotency_key": "live-1"},
    ))

    assert result.allowed is False
    assert "stale" in result.reason.lower()


def test_risk_blocks_live_order_without_connected_broker_health(fake_db):
    fake_db["risk_settings"].insert_one({
        "user": "alice",
        "live_enabled": True,
        "paper_only": False,
        "block_on_broker_disconnect": True,
    })

    result = RiskControlService(fake_db).check_order(BrokerOrder(
        user="alice",
        broker="dhan",
        symbol="NIFTY",
        side="BUY",
        quantity=1,
        strategy_id="ALG143",
        metadata={"idempotency_key": "algo143-live-1"},
    ))

    assert result.allowed is False
    assert "Broker login is not connected" in result.reason


def test_risk_blocks_duplicate_live_idempotency_key(fake_db):
    fake_db["risk_settings"].insert_one({
        "user": "alice",
        "live_enabled": True,
        "paper_only": False,
    })
    fake_db["broker_health"].insert_one({
        "user": "alice",
        "broker": "dhan",
        "login_status": "connected",
        "websocket_status": "connected",
    })
    fake_db["strategy_jobs"].insert_one({
        "user": "alice",
        "status": "completed",
        "idempotency_key": "algo143-live-1",
    })

    result = RiskControlService(fake_db).check_order(BrokerOrder(
        user="alice",
        broker="dhan",
        symbol="NIFTY",
        side="BUY",
        quantity=1,
        strategy_id="ALG143",
        metadata={"idempotency_key": "algo143-live-1"},
    ))

    assert result.allowed is False
    assert "Duplicate live order idempotency key" in result.reason


def test_risk_production_live_defaults_enable_required_gates(fake_db):
    service = RiskControlService(fake_db)

    settings = service.apply_production_live_defaults("alice")
    saved = fake_db["risk_settings"].find_one({"user": "alice"})

    assert settings["live_enabled"] is True
    assert settings["paper_only"] is False
    assert settings["require_market_hours"] is True
    assert settings["require_fresh_quote"] is True
    assert settings["block_on_broker_disconnect"] is True
    assert settings["duplicate_signal_window_seconds"] == 30
    assert saved["profile"] == "production_live_defaults"


def test_risk_suppresses_duplicate_strategy_signal(fake_db):
    fake_db["risk_settings"].insert_one({
        "user": "alice",
        "duplicate_signal_window_seconds": 60,
    })
    fake_db["normalized_orders"].insert_one({
        "user": "alice",
        "symbol": "NIFTY",
        "side": "BUY",
        "strategy_id": "S1",
        "created_at": datetime.datetime.now(datetime.UTC),
    })

    result = RiskControlService(fake_db).check_order(BrokerOrder(
        user="alice",
        broker="paper",
        symbol="NIFTY",
        side="BUY",
        quantity=1,
        strategy_id="S1",
    ), mode="paper")

    assert result.allowed is False
    assert "Duplicate strategy signal" in result.reason


def test_risk_blocks_strategy_cooldown_after_loss(fake_db):
    fake_db["strategy_risk_settings"].insert_one({
        "user": "alice",
        "strategy_id": "S1",
        "cooldown_after_loss_seconds": 3600,
    })
    fake_db["Opositions"].insert_one({
        "user": "alice",
        "botcode": "S1",
        "status": "close",
        "pnl": -50,
        "closed_at": datetime.datetime.now(datetime.UTC),
    })

    result = RiskControlService(fake_db).check_order(BrokerOrder(
        user="alice",
        broker="paper",
        symbol="NIFTY",
        side="BUY",
        quantity=1,
        strategy_id="S1",
    ), mode="paper")

    assert result.allowed is False
    assert "cooling down" in result.reason


def test_secret_encryption_round_trips_without_plaintext():
    encrypted = encrypt_secret("super-secret")
    assert encrypted != "super-secret"
    assert encrypted.startswith("enc:v1:")
    assert decrypt_secret(encrypted) == "super-secret"


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
        metadata={"security_id": "12345", "idempotency_key": "dhan-1"},
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
    result = adapter.place_order(BrokerOrder(
        user="alice",
        broker="fyers",
        symbol="NIFTY24",
        side="BUY",
        quantity=1,
        exchange="NFO",
        metadata={"idempotency_key": "fyers-1"},
    ))
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
    result = adapter.place_order(BrokerOrder(
        user="alice",
        broker="zerodha",
        symbol="NIFTY24",
        side="SELL",
        quantity=1,
        exchange="NFO",
        metadata={"idempotency_key": "zerodha-1"},
    ))
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
        metadata={"symboltoken": "123", "idempotency_key": "angelone-1"},
    ))
    assert result["success"] is True


def test_disabled_legacy_adapter_returns_normalized_rejection(fake_db):
    adapter = BrokerAdapterFactory(
        db=fake_db,
        health_service=BrokerHealthService(fake_db),
        order_lifecycle=OrderLifecycleService(fake_db),
        risk_service=RiskControlService(fake_db),
    ).create("delta_exchange_india")

    login_result = adapter.login(BrokerCredentials(user="alice", broker="delta_exchange_india"))
    order_result = adapter.place_order(BrokerOrder(
        user="alice",
        broker="delta_exchange_india",
        symbol="BTCUSD",
        side="BUY",
        quantity=1,
    ))

    assert login_result["success"] is False
    assert login_result["status"] == "disabled"
    assert order_result["success"] is False
    assert order_result["status"] == "rejected"


def test_worker_processes_paper_strategy_job(fake_db):
    worker = TradingWorker(db=fake_db, interval_seconds=0.01, relogin_interval_seconds=999, subscription_interval_seconds=999)
    queued = worker.enqueue_strategy_order({
        "user": "alice",
        "mode": "paper",
        "symbol": "NIFTY",
        "side": "BUY",
        "quantity": 1,
        "price": 100,
        "strategy_id": "S1",
    })

    processed = worker.process_strategy_jobs()
    saved_job = fake_db["strategy_jobs"].rows[0]
    orders = list(fake_db["normalized_orders"].find({"user": "alice"}))

    assert processed[0]["result"]["success"] is True
    assert saved_job["status"] == "completed"
    assert orders[0]["status"] == "filled"
    assert "strategy_job_enqueued" in [row["event"] for row in fake_db["audit_logs"].rows]
    assert "strategy_job_completed" in [row["event"] for row in fake_db["audit_logs"].rows]


def test_worker_rejects_invalid_strategy_job(fake_db):
    worker = TradingWorker(db=fake_db, interval_seconds=0.01, relogin_interval_seconds=999, subscription_interval_seconds=999)
    try:
        worker.enqueue_strategy_order({
            "user": "alice",
            "mode": "paper",
            "symbol": "NIFTY",
            "side": "HOLD",
            "quantity": 1,
            "price": 100,
        })
    except Exception as exc:
        assert "side" in str(exc)
    else:
        raise AssertionError("Invalid order side should be rejected")


def test_live_readiness_requires_burn_in_broker_and_risk(fake_db):
    service = LiveReadinessService(fake_db)
    result = service.check_user("alice", min_orders=1, min_days=2)
    assert result["ready"] is False
    assert "paper_burn_in" in result["missing"]

    fake_db["normalized_orders"].insert_one({
        "user": "alice",
        "broker": "paper",
        "status": "filled",
        "created_at": datetime.datetime.now(datetime.UTC),
    })
    fake_db["broker"].insert_one({"user": "alice", "selectedbroker": "dhan"})
    fake_db["broker_health"].insert_one({"user": "alice", "broker": "dhan", "login_status": "connected"})
    fake_db["risk_settings"].insert_one({"user": "alice", "kill_switch": False})

    result = service.check_user("alice", min_orders=1, min_days=2)
    assert result["ready"] is True


def test_broker_reconciliation_smoke_and_positions(fake_db):
    class FakeAdapter:
        def login(self, credentials):
            return {"success": True}

        def funds(self, user):
            return {"cash": 1000}

        def positions(self, user):
            return [{"symbol": "NIFTY", "quantity": 1}]

        def quote(self, symbol, **kwargs):
            return {"symbol": symbol, "ltp": 100}

    class FakeFactory:
        def create(self, broker):
            return FakeAdapter()

    service = BrokerReconciliationService(fake_db, adapter_factory=FakeFactory(), audit_service=AuditLogService(fake_db))
    fake_db["paper_positions"].insert_one({
        "user": "alice",
        "broker": "paper",
        "symbol": "NIFTY",
        "net_quantity": 1,
        "updated_at": datetime.datetime.now(datetime.UTC),
    })
    smoke = service.smoke_test("alice", "paper", symbol="NIFTY")
    reconciliation = service.reconcile_positions("alice", "paper")

    assert smoke["success"] is True
    assert reconciliation["success"] is True
    assert isinstance(reconciliation["checked_at"], str)
    assert isinstance(reconciliation["local_positions"][0]["_id"], str)
    assert isinstance(reconciliation["local_positions"][0]["updated_at"], str)
    assert "broker_smoke_test" in [row["event"] for row in fake_db["audit_logs"].rows]
    assert "broker_position_reconciliation" in [row["event"] for row in fake_db["audit_logs"].rows]


def test_broker_connection_test_updates_health(fake_db):
    class FakeAdapter:
        def login(self, credentials):
            return {"success": True}

        def funds(self, user):
            return {"cash": 1000}

        def positions(self, user):
            return []

    class FakeFactory:
        def create(self, broker):
            return FakeAdapter()

    service = BrokerReconciliationService(
        fake_db,
        adapter_factory=FakeFactory(),
        audit_service=AuditLogService(fake_db),
    )
    result = service.connection_test("alice", "dhan")
    health = fake_db["broker_health"].find_one({"user": "alice", "broker": "dhan"})

    assert result["success"] is True
    assert result["connection_status"] == "connected"
    assert result["checks"]["funds"] == "ok"
    assert health["login_status"] == "connected"
    assert "broker_connection_test" in [row["event"] for row in fake_db["audit_logs"].rows]


def test_broker_connection_test_records_failure(fake_db):
    class FailingAdapter:
        def login(self, credentials):
            return {"success": False, "message": "Invalid credentials"}

    class FakeFactory:
        def create(self, broker):
            return FailingAdapter()

    service = BrokerReconciliationService(
        fake_db,
        adapter_factory=FakeFactory(),
        audit_service=AuditLogService(fake_db),
    )
    result = service.connection_test("alice", "dhan")
    health = fake_db["broker_health"].find_one({"user": "alice", "broker": "dhan"})

    assert result["success"] is False
    assert result["connection_status"] == "failed"
    assert health["login_status"] == "failed"
    assert health["last_error"] == "Invalid credentials"

import datetime
import builtins
import sys
import time
import types

import pytest

from app.domain.backtesting.service import BacktestService
from app.domain.brokers.adapters import BrokerAdapterFactory, BrokerCredentials, BrokerOrder
from app.domain.brokers.adapters.aliceblue import load_trade_hub
from app.domain.brokers.adapters.aliceblue import AliceBlueBrokerAdapter
from app.domain.brokers.adapters.base import BrokerCredentials
from app.domain.brokers.aliceblue_auth import (
    AliceBlueDirectAuthError,
    AliceBlueDirectAuthenticator,
)
from app.domain.brokers.health import BrokerHealthService
from app.domain.audit.service import AuditLogService
from app.domain.orders.lifecycle import OrderLifecycleService
from app.domain.readiness.service import LiveReadinessService
from app.domain.reconciliation.service import BrokerReconciliationService
from app.domain.risk.service import RiskControlService
from app.workers.trading_worker import TradingWorker
from app.core.logging_config import sanitize_log_value
from app.core.secrets import decrypt_secret, encrypt_secret
from connectors.connector import AliceBlueTradeHubAdapter, Exchange, strategy_market_window
from models import EMA_fut_mode, EMA_mode, SSTRIKE_mode


class FakeHttpResponse:
    def __init__(self, body):
        self.body = body

    def raise_for_status(self):
        return None

    def json(self):
        return self.body


class FakeHttpSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def post(self, url, json, headers, timeout):
        self.requests.append({
            "url": url,
            "json": dict(json),
            "headers": dict(headers),
            "timeout": timeout,
        })
        return FakeHttpResponse(self.responses.pop(0))


def test_strategy_market_window_uses_india_time_on_utc_host():
    trade = {
        "StartTime": "09:15",
        "ExitTime": "15:20",
        "Intraday": True,
    }
    utc_time = datetime.datetime(
        2026, 6, 15, 5, 8, tzinfo=datetime.UTC
    )

    window = strategy_market_window(trade, now=utc_time)

    assert window["market_time"] == datetime.time(10, 38)
    assert window["intraday"] is True
    assert window["positional"] is False


def test_strategy_market_window_rejects_utc_time_after_india_close():
    trade = {
        "StartTime": "09:15",
        "ExitTime": "15:20",
        "Intraday": True,
    }
    utc_time = datetime.datetime(
        2026, 6, 15, 10, 0, tzinfo=datetime.UTC
    )

    window = strategy_market_window(trade, now=utc_time)

    assert window["market_time"] == datetime.time(15, 30)
    assert window["intraday"] is False


def test_aliceblue_sdk_mappings_match_ant_a3_values():
    assert AliceBlueBrokerAdapter.PRODUCT_MAP["NRML"] == "NORMAL"
    assert AliceBlueBrokerAdapter.PRODUCT_MAP["NORMAL"] == "NORMAL"
    assert AliceBlueBrokerAdapter.PRODUCT_MAP["CNC"] == "LONGTERM"
    assert AliceBlueBrokerAdapter.ORDER_TYPE_MAP["SL-M"] == "SLM"

    assert AliceBlueTradeHubAdapter.PRODUCT_TYPE_MAP["NRML"] == "NORMAL"
    assert AliceBlueTradeHubAdapter.PRODUCT_TYPE_MAP["NORMAL"] == "NORMAL"
    assert AliceBlueTradeHubAdapter.PRODUCT_TYPE_MAP["CNC"] == "LONGTERM"
    assert AliceBlueTradeHubAdapter.ORDER_TYPE_MAP["SL-M"] == "SLM"


def test_aliceblue_sdk_import_reports_nested_missing_dependency(monkeypatch):
    original_import = builtins.__import__

    def fail_sdk_import(name, *args, **kwargs):
        if name == "TradeMaster.TradeSync":
            raise ModuleNotFoundError("No module named 'setuptools'", name="setuptools")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fail_sdk_import)

    with pytest.raises(ImportError, match="Python module 'setuptools' is missing"):
        load_trade_hub()


def test_aliceblue_login_persists_daily_session(monkeypatch, fake_db):
    class FakeTradeHub:
        def __init__(self, **_kwargs):
            pass

        def get_session_id(self, session_id=None):
            return {"sessionID": session_id or "fresh-session"}

        def get_profile(self):
            return {
                "status": "Ok",
                "message": "Success",
                "result": [{"userId": "AB123"}],
            }

    monkeypatch.setattr(
        "app.domain.brokers.adapters.aliceblue.load_trade_hub",
        lambda: FakeTradeHub,
    )
    fake_db["apis"].insert_one({
        "user": "alice",
        "broker": "aliceblue",
        "apikey": "AB123",
        "apisecret": "secret",
        "auth_code": "auth",
    })
    adapter = AliceBlueBrokerAdapter(db=fake_db)

    result = adapter.login(BrokerCredentials(user="alice", broker="aliceblue"))
    saved = fake_db["apis"].find_one({"user": "alice", "broker": "aliceblue"})

    assert result["success"] is True
    assert saved["session_date"] == datetime.datetime.now().strftime("%Y-%m-%d")
    assert saved["user_session"] != "fresh-session"


def test_aliceblue_login_rejects_unauthorized_saved_session(monkeypatch, fake_db):
    class FakeTradeHub:
        def __init__(self, **_kwargs):
            pass

        def get_session_id(self, session_id=None):
            return {"sessionID": session_id}

        def get_profile(self):
            return {"stat": "Not_ok", "emsg": "Unauthorized"}

    monkeypatch.setattr(
        "app.domain.brokers.adapters.aliceblue.load_trade_hub",
        lambda: FakeTradeHub,
    )
    fake_db["apis"].insert_one({
        "user": "alice",
        "broker": "aliceblue",
        "apikey": "AB123",
        "apisecret": "secret",
        "user_session": "expired-session",
    })
    adapter = AliceBlueBrokerAdapter(db=fake_db)

    result = adapter.login(BrokerCredentials(user="alice", broker="aliceblue"))

    assert result["success"] is False
    assert result["status"] == "rejected"
    assert result["message"] == "Unauthorized"


def test_aliceblue_login_automatically_replaces_unauthorized_session(monkeypatch, fake_db):
    class FakeTradeHub:
        def __init__(self, session_id=None, **_kwargs):
            self.session_id = session_id

        def get_session_id(self, session_id=None):
            if session_id:
                self.session_id = session_id
                return {"sessionID": session_id}
            self.session_id = "fresh-session"
            return {"sessionID": self.session_id}

        def get_profile(self):
            if self.session_id == "expired-session":
                return {"stat": "Not_ok", "emsg": "Unauthorized"}
            return {"status": "Ok", "result": [{"userId": "AB123"}]}

    monkeypatch.setattr(
        "app.domain.brokers.adapters.aliceblue.load_trade_hub",
        lambda: FakeTradeHub,
    )
    fake_db["apis"].insert_one({
        "user": "alice",
        "broker": "aliceblue",
        "apikey": "AB123",
        "auth_code": "valid-auth-code",
        "apisecret": "secret",
        "user_session": "expired-session",
    })
    adapter = AliceBlueBrokerAdapter(db=fake_db)

    result = adapter.login(BrokerCredentials(user="alice", broker="aliceblue"))
    saved = fake_db["apis"].find_one({"user": "alice", "broker": "aliceblue"})

    assert result["success"] is True
    assert result["status"] == "connected"
    assert decrypt_secret(saved["user_session"]) == "fresh-session"


def test_aliceblue_saved_session_does_not_require_daily_browser_login(fake_db):
    exchange = Exchange.__new__(Exchange)
    exchange.apis_collection = fake_db["apis"]
    fake_db["apis"].insert_one({
        "user": "alice",
        "broker": "aliceblue",
        "user_session": encrypt_secret("still-valid-session"),
        "session_date": "2020-01-01",
    })

    assert exchange._aliceblue_user_verified_today("alice") is True


def test_aliceblue_direct_authentication_runs_without_browser(monkeypatch):
    http = FakeHttpSession([
        {"status": "Ok", "message": "Success", "result": [{"isExist": "Yes"}]},
        {"status": "Ok", "message": "Success", "result": [{"encKey": "passphrase"}]},
        {"status": "Ok", "message": "Success", "result": [{"token": "password-token"}]},
        {"status": "Ok", "message": "Success", "result": [{"token": "totp-token"}]},
        {
            "status": "Ok",
            "message": "Success",
            "result": [{
                "authorized": True,
                "redirectUrl": (
                    "http://127.0.0.1:5000?"
                    "authCode=fresh-auth-code&userId=AB123"
                ),
            }],
        },
        {"stat": "Ok", "userSession": "fresh-session"},
    ])
    monkeypatch.setattr(
        AliceBlueDirectAuthenticator,
        "_totp_value",
        staticmethod(lambda _secret: "123456"),
    )

    result = AliceBlueDirectAuthenticator(http=http).authenticate(
        user_id="AB123",
        password="password",
        totp_secret="JBSWY3DPEHPK3PXP",
        app_code="app-code",
        app_secret="app-secret",
    )

    assert result == {
        "user_id": "AB123",
        "auth_code": "fresh-auth-code",
        "session_id": "fresh-session",
    }
    assert len(http.requests) == 6
    assert http.requests[2]["json"]["userData"] != "password"
    assert http.requests[3]["json"]["totp"] == "123456"
    assert http.requests[3]["headers"]["Authorization"] == (
        "Bearer AB123 WEB password-token"
    )
    assert all("playwright" not in request["url"].lower() for request in http.requests)


def test_aliceblue_direct_authentication_reports_totp_rejection(monkeypatch):
    http = FakeHttpSession([
        {"status": "Ok", "message": "Success", "result": [{"isExist": "Yes"}]},
        {"status": "Ok", "message": "Success", "result": [{"encKey": "passphrase"}]},
        {"status": "Ok", "message": "Success", "result": [{"token": "password-token"}]},
        {"status": "Not ok", "message": "Invalid totp", "result": []},
    ])
    monkeypatch.setattr(
        AliceBlueDirectAuthenticator,
        "_totp_value",
        staticmethod(lambda _secret: "123456"),
    )

    with pytest.raises(
        AliceBlueDirectAuthError,
        match="TOTP verification rejected: Invalid totp",
    ):
        AliceBlueDirectAuthenticator(http=http).authenticate(
            user_id="AB123",
            password="password",
            totp_secret="JBSWY3DPEHPK3PXP",
            app_code="app-code",
            app_secret="app-secret",
        )

    assert len(http.requests) == 4


def test_aliceblue_refresh_persists_direct_auth_session(monkeypatch, fake_db):
    class FakeDirectAuthenticator:
        def authenticate(self, **_kwargs):
            return {
                "user_id": "AB123",
                "auth_code": "fresh-auth-code",
                "session_id": "fresh-session",
            }

    class FakeAlice:
        def __init__(self, **_kwargs):
            pass

        def get_session_id(self, session_id=None):
            return {"sessionID": session_id}

        def get_profile(self):
            return {"status": "Ok", "result": [{"userId": "AB123"}]}

    monkeypatch.setattr(
        "connectors.connector.AliceBlueDirectAuthenticator",
        FakeDirectAuthenticator,
    )
    monkeypatch.setattr(
        "connectors.connector.AliceBlueTradeHubAdapter",
        FakeAlice,
    )
    fake_db["apis"].insert_one({
        "user": "alice",
        "broker": "aliceblue",
        "apikey": "AB123",
        "apisecret": "app-secret",
        "alice_password": "password",
        "totp_key": "JBSWY3DPEHPK3PXP",
        "app_key": "app-code",
    })
    exchange = Exchange.__new__(Exchange)
    exchange.db = fake_db
    exchange.apis_collection = fake_db["apis"]

    refreshed = exchange._refresh_aliceblue_auth(
        fake_db["apis"].find_one({"user": "alice", "broker": "aliceblue"})
    )
    saved = fake_db["apis"].find_one({"user": "alice", "broker": "aliceblue"})
    health = fake_db["broker_health"].find_one({
        "user": "alice",
        "broker": "aliceblue",
    })

    assert refreshed is not None
    assert decrypt_secret(saved["auth_code"]) == "fresh-auth-code"
    assert decrypt_secret(saved["user_session"]) == "fresh-session"
    assert health["login_status"] == "connected"
    assert health["last_error"] == ""


def test_market_price_uses_fresh_aliceblue_depth_when_ltp_is_missing():
    exchange = Exchange.__new__(Exchange)
    exchange.prices = {}
    exchange.sprices = {}
    exchange.dataframes = {}
    exchange.api = None
    exchange.market_depth_max_age_seconds = 3
    exchange.market_depths = {
        "NFO|12345": {
            "bp1": 100,
            "sp1": 102,
            "_depth_time": time.time(),
        }
    }

    price = exchange._get_market_price(
        "NIFTY16JUN26C23600",
        "NFO",
        12345,
    )

    assert price == 101
    assert exchange.prices["NIFTY16JUN26C23600"] == 101


def test_option_quote_wait_allows_websocket_tick_to_arrive():
    exchange = Exchange.__new__(Exchange)
    attempts = {"count": 0}

    def get_market_price(_symbol, _exchange=None, _token=None):
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise KeyError("price unavailable")
        return 123.45

    exchange._get_market_price = get_market_price

    price = exchange._wait_for_market_price(
        "NIFTY16JUN26C23600",
        "NFO",
        12345,
        timeout_seconds=0.1,
        poll_interval=0.001,
    )

    assert price == 123.45
    assert attempts["count"] == 3


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


def test_algorithm_143_future_configuration_loads_required_fields():
    payload = _algorithm_143_payload(
        botname="Algo143FutureSmoke",
        botcode="ALG143-FUT",
        Expiry="Current Month",
        onspot="true",
    )

    strategy = EMA_fut_mode(payload)

    assert strategy.strategy == "EMA"
    assert strategy.botcode == "ALG143-FUT"
    assert strategy.onspot is True
    assert strategy.live is False
    assert strategy.position == "out"


def test_algorithm_143_sstrike_configuration_loads_required_fields():
    payload = _algorithm_143_payload(
        botname="Algo143SStrikeSmoke",
        botcode="ALG143-SSTRIKE",
        r2="19",
        k2="20",
    )

    strategy = SSTRIKE_mode(payload)

    assert strategy.strategy == "SSTRIKE"
    assert strategy.botcode == "ALG143-SSTRIKE"
    assert strategy.r1 == 19
    assert strategy.k1 == 20
    assert strategy.live is False
    assert strategy.position == "out"


@pytest.mark.parametrize(
    ("new_signal", "trends", "expected_signal", "expected_exit"),
    [
        (True, [1, 0], 1, 1),
        (True, [0, 1], -1, -1),
        (True, [0, 0], 0, 1),
        (False, [0, 0], 1, 1),
    ],
)
def test_algorithm_143_signal_evaluation_is_deterministic(
    new_signal, trends, expected_signal, expected_exit
):
    result = Exchange._evaluate_143_signal(
        {"Newsignal": new_signal, "candle1": 1, "candle2": 2},
        trends,
        list(trends),
    )

    assert result["signal"] == expected_signal
    assert result["exit_signal"] == expected_exit


def test_algorithm_143_signal_evaluation_handles_short_history():
    result = Exchange._evaluate_143_signal(
        {"Newsignal": True, "candle1": 1, "candle2": 2},
        [0],
        [0],
    )

    assert result["signal"] == 0
    assert result["exit_signal"] == 0
    assert result["reason"] == "insufficient_trend_history"


def test_algorithm_143_rejects_invalid_quantity_input():
    with pytest.raises(ValueError):
        EMA_mode(_algorithm_143_payload(lot="bad"))


def test_algorithm_143_rejects_missing_required_input():
    payload = _algorithm_143_payload()
    payload.pop("botname")

    with pytest.raises(KeyError):
        EMA_mode(payload)


def test_algorithm_143_future_rejection_does_not_create_open_position(fake_db):
    exchange = Exchange.__new__(Exchange)
    exchange.strategy_collection = fake_db["strategies"]
    exchange.opositions_collection = fake_db["Opositions"]
    exchange.broker_collection = fake_db["broker"]
    exchange.prices = {"NIFTY-I": 24500.0, "NIFTYFUT": 24505.0}
    exchange.sprices = {}
    exchange.last_order_price_context = {}
    exchange.MainFutureSelect = lambda _symbol, _expiry: (
        "NIFTYFUT",
        25,
        "2099-12-31",
        12345,
    )
    exchange._symboltransformmonthfut = lambda _expiry, _symbol: "NIFTY-I"
    exchange._make_instrument = lambda *_args: {"token": 12345}
    exchange._next_entry_id = lambda: 1
    exchange._place_aliceblue_limit_order = lambda **_kwargs: {
        "status": "Not_ok",
        "emsg": "Insufficient balance",
    }
    exchange.alice = {
        "alice": type(
            "FakeAlice",
            (),
            {"get_instrument_by_token": lambda *_args: {"token": 12345}},
        )()
    }
    fake_db["broker"].insert_one({
        "user": "alice",
        "selectedbroker": "aliceblue",
    })
    trade = dict(
        EMA_fut_mode(
            _algorithm_143_payload(
                user="alice",
                botname="Algo143FutureLiveReject",
                botcode="ALG143-FUT-REJECT",
                Expiry="Current Month",
                onspot="true",
                live="true",
                status="opened",
            )
        ).__dict__
    )
    fake_db["strategies"].insert_one(dict(trade))

    exchange.FBUY(trade, "BUY", 1)

    position = fake_db["Opositions"].find_one({
        "user": "alice",
        "botcode": "ALG143-FUT-REJECT",
    })
    saved_strategy = fake_db["strategies"].find_one({
        "user": "alice",
        "botcode": "ALG143-FUT-REJECT",
    })
    assert position["status"] == "broker_failed"
    assert position["decision"] == "broker_failed"
    assert saved_strategy["position"] == "out"
    assert saved_strategy["entry_order_state"] == "broker_failed"


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


def test_aliceblue_login_regenerates_session_from_saved_direct_credentials(fake_db, monkeypatch):
    from app.domain.brokers.adapters import aliceblue as aliceblue_module

    fake_db["apis"].insert_one({
        "user": "alice",
        "broker": "aliceblue",
        "apikey": "1775863",
        "apisecret": encrypt_secret("app-secret"),
        "auth_code": encrypt_secret("old-auth"),
        "user_session": encrypt_secret("old-session"),
        "sessionID": encrypt_secret("old-session"),
        "alice_password": encrypt_secret("password"),
        "totp_key": encrypt_secret("JBSWY3DPEHPK3PXP"),
        "app_key": "app-key",
    })

    class FakeTradeHub:
        def __init__(self, user_id, auth_code, secret_key, session_id=None):
            self.user_id = user_id
            self.auth_code = auth_code
            self.secret_key = secret_key
            self.session_id = session_id

        def get_session_id(self, session_id=None):
            candidate = session_id or self.session_id
            if candidate == "new-session":
                return {"userSession": "new-session"}
            if candidate == "old-session":
                return {"userSession": "old-session"}
            return {"stat": "Not_ok", "emsg": "Session ID not found in response."}

        def get_profile(self):
            if self.session_id == "new-session":
                return {"stat": "Ok", "result": [{"name": "Alice"}]}
            return {"stat": "Not_ok", "emsg": "401 - Unauthorized"}

    class FakeDirectAuthenticator:
        def authenticate(self, **kwargs):
            assert kwargs["user_id"] == "1775863"
            assert kwargs["password"] == "password"
            assert kwargs["totp_secret"] == "JBSWY3DPEHPK3PXP"
            assert kwargs["app_code"] == "app-key"
            assert kwargs["app_secret"] == "app-secret"
            return {"auth_code": "new-auth", "session_id": "new-session"}

    monkeypatch.setattr(aliceblue_module, "load_trade_hub", lambda: FakeTradeHub)
    monkeypatch.setattr(
        aliceblue_module,
        "AliceBlueDirectAuthenticator",
        FakeDirectAuthenticator,
    )

    adapter = AliceBlueBrokerAdapter(
        db=fake_db,
        health_service=BrokerHealthService(fake_db),
    )
    result = adapter.login(BrokerCredentials(user="alice", broker="aliceblue"))

    saved = fake_db["apis"].find_one({"user": "alice", "broker": "aliceblue"})
    assert result["success"] is True
    assert decrypt_secret(saved["auth_code"]) == "new-auth"
    assert decrypt_secret(saved["user_session"]) == "new-session"
    assert decrypt_secret(saved["sessionID"]) == "new-session"


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

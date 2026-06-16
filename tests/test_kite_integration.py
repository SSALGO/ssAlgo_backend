import datetime
import asyncio
import sys
import types

import pytest

from app.core.secrets import encrypt_secret
from app.api import fastapi_routers
from app.domain.brokers.kite import KiteService, KiteTokenExpired
from app.domain.market_data.kite_market_data import KiteMarketDataService
from app.workers.trading_worker import TradingWorker


class FakeKiteResponse:
    status_code = 200

    def __init__(self, body):
        self._body = body

    def json(self):
        return self._body


class FakeKiteHttp:
    def __init__(self, response):
        self.response = response
        self.requests = []

    def request(self, method, url, **kwargs):
        self.requests.append({"method": method, "url": url, **kwargs})
        return FakeKiteResponse(self.response)


def test_kite_login_url_sends_state_through_redirect_params(monkeypatch, fake_db):
    monkeypatch.setattr("app.domain.brokers.kite.AppConfig.KITE_API_KEY", "kite-key")
    monkeypatch.setattr("app.domain.brokers.kite.AppConfig.KITE_API_SECRET", "kite-secret")

    login_url = KiteService(fake_db).generate_login_url(state="state-123")

    assert login_url == (
        "https://kite.zerodha.com/connect/login?"
        "v=3&api_key=kite-key&redirect_params=state%3Dstate-123"
    )
    assert "&state=" not in login_url


def test_kite_order_requires_same_day_token(monkeypatch, fake_db):
    monkeypatch.setattr("app.domain.brokers.kite.AppConfig.KITE_API_KEY", "kite-key")
    monkeypatch.setattr("app.domain.brokers.kite.AppConfig.KITE_API_SECRET", "kite-secret")
    fake_db["apis"].insert_one({
        "user": "alice",
        "broker": "zerodha",
        "isConnected": True,
        "accessTokenEncrypted": encrypt_secret("old-token"),
        "tokenDate": "2026-06-15",
    })

    with pytest.raises(KiteTokenExpired):
        KiteService(fake_db).place_order("alice", {
            "exchange": "NFO",
            "tradingsymbol": "NIFTY26JUNFUT",
            "transaction_type": "BUY",
            "quantity": 1,
        })

    row = fake_db["apis"].find_one({"user": "alice", "broker": "zerodha"})
    health = fake_db["broker_health"].find_one({"user": "alice", "broker": "zerodha"})
    assert row["connectionStatus"] == "token_expired"
    assert health["token_status"] == "token_expired"


def test_kite_order_posts_from_backend_with_encrypted_token(monkeypatch, fake_db):
    monkeypatch.setattr("app.domain.brokers.kite.AppConfig.KITE_API_KEY", "kite-key")
    monkeypatch.setattr("app.domain.brokers.kite.AppConfig.KITE_API_SECRET", "kite-secret")
    fake_db["apis"].insert_one({
        "user": "alice",
        "broker": "zerodha",
        "isConnected": True,
        "accessTokenEncrypted": encrypt_secret("same-day-token"),
        "tokenDate": datetime.datetime.utcnow().date().isoformat(),
    })
    http = FakeKiteHttp({"status": "success", "data": {"order_id": "KITE123"}})

    result = KiteService(fake_db, http=http).place_order("alice", {
        "exchange": "NFO",
        "tradingsymbol": "NIFTY26JUNFUT",
        "transaction_type": "BUY",
        "quantity": 1,
        "product": "MIS",
        "order_type": "MARKET",
        "source": "MANUAL",
    })

    assert result["data"]["order_id"] == "KITE123"
    assert http.requests[0]["url"] == "https://api.kite.trade/orders/regular"
    assert http.requests[0]["headers"]["Authorization"] == "token kite-key:same-day-token"
    assert fake_db["order_logs"].find_one({"orderId": "KITE123"})["status"] == "placed"


def test_legacy_connector_loads_kite_redirect_session(monkeypatch, fake_db):
    from connectors.connector import Exchange

    class FakeKite:
        def __init__(self, api_key):
            self.api_key = api_key
            self.access_token = None

        def set_access_token(self, token):
            self.access_token = token

    monkeypatch.setitem(sys.modules, "kiteconnect", types.SimpleNamespace(KiteConnect=FakeKite))
    class TestExchange(Exchange):
        def _ensure_collection_exists(self, collection_name):
            self.ensured_collection = collection_name

        def _save_session_to_db(self, collection_name, filter_key, filter_value, session_data):
            self.saved_session = {
                "collection_name": collection_name,
                "filter_key": filter_key,
                "filter_value": filter_value,
                "session_data": session_data,
            }

    exchange = TestExchange.__new__(TestExchange)

    user, kite, session = exchange._login_zerodha({
        "user": "alice",
        "broker": "zerodha",
        "apiKey": "kite-key",
        "kiteUserId": "KITE123",
        "accessTokenEncrypted": encrypt_secret("same-day-token"),
        "tokenDate": datetime.date.today().isoformat(),
    })

    assert user == "alice"
    assert kite.api_key == "kite-key"
    assert kite.access_token == "same-day-token"
    assert session == {"access_token": "same-day-token"}
    assert exchange.ensured_collection == "zerodhaloginsess"
    assert exchange.saved_session == {
        "collection_name": "zerodhaloginsess",
        "filter_key": "user",
        "filter_value": "alice",
        "session_data": {"access_token": "same-day-token", "kite_user_id": "KITE123"},
    }
    assert not hasattr(exchange, "market_data_started")


def test_legacy_connector_skips_zerodha_websocket_start(fake_db):
    from connectors.connector import Exchange

    exchange = Exchange.__new__(Exchange)
    exchange.db = fake_db

    result = exchange._ensure_zerodha_market_data("alice", "kite-key", "same-day-token")
    health = fake_db["broker_health"].find_one({"user": "alice", "broker": "zerodha"})

    assert result == {"success": False, "status": "central_feed_only"}
    assert health is None


def test_kite_market_data_cache_tracks_latest_tick():
    service = KiteMarketDataService()
    service.subscribe_instruments(["256265"])

    saved = service.on_tick_update({
        "instrument_token": 256265,
        "last_price": 23500.5,
        "volume": 1000,
        "ohlc": {"open": 23400},
        "change": 0.25,
        "depth": {"buy": []},
    })

    assert saved["instrument_token"] == 256265
    assert service.get_latest_ltp(256265) == 23500.5


def test_kite_callback_uses_query_state_before_stale_cookie(monkeypatch, fake_db):
    now = datetime.datetime.utcnow()
    fake_db["broker_oauth_states"].insert_one({
        "state": "valid-query-state",
        "user": "alice",
        "broker": "zerodha",
        "created_at": now,
        "expires_at": now + datetime.timedelta(minutes=5),
        "used": False,
    })
    fake_db["broker_oauth_states"].insert_one({
        "state": "stale-cookie-state",
        "user": "alice",
        "broker": "zerodha",
        "created_at": now - datetime.timedelta(minutes=20),
        "expires_at": now - datetime.timedelta(minutes=10),
        "used": False,
    })
    monkeypatch.setattr(fastapi_routers, "get_database", lambda: fake_db)
    monkeypatch.setattr(
        fastapi_routers.KiteService,
        "generate_session",
        lambda self, request_token: {"access_token": "fresh-token", "user_id": "KITE123"},
    )

    request = types.SimpleNamespace(
        query_params={"status": "success", "request_token": "request-token", "state": "valid-query-state"},
        cookies={"sslago_kite_state": "stale-cookie-state"},
    )

    response = fastapi_routers.kite_callback(request)

    assert response.status_code == 303
    assert "status=connected" in response.headers["location"]
    assert fake_db["apis"].find_one({"user": "alice", "broker": "zerodha"})["kiteUserId"] == "KITE123"
    assert fake_db["broker_oauth_states"].find_one({"state": "valid-query-state"}) is None
    assert fake_db["broker_oauth_states"].find_one({"state": "stale-cookie-state"}) is not None


def test_kite_callback_missing_state_returns_controlled_error(monkeypatch, fake_db):
    monkeypatch.setattr(fastapi_routers, "get_database", lambda: fake_db)
    request = types.SimpleNamespace(
        query_params={"status": "success", "request_token": "request-token"},
        cookies={},
    )

    response = fastapi_routers.kite_callback(request)

    assert response.status_code == 303
    assert "status=failed" in response.headers["location"]
    assert "Kite+callback+state+missing" in response.headers["location"]


def test_kite_postback_updates_order_log(monkeypatch, fake_db):
    fake_db["order_logs"].insert_one({
        "user": "alice",
        "broker": "zerodha",
        "orderId": "KITE123",
        "status": "placed",
    })
    monkeypatch.setattr(fastapi_routers, "get_database", lambda: fake_db)

    class FakeRequest:
        headers = {"content-type": "application/json"}

        async def json(self):
            return {"order_id": "KITE123", "status": "COMPLETE"}

    response = asyncio.run(fastapi_routers.kite_postback(FakeRequest()))
    row = fake_db["order_logs"].find_one({"orderId": "KITE123"})

    assert response.success is True
    assert row["postbackStatus"] == "COMPLETE"
    assert row["postbackPayload"]["order_id"] == "KITE123"


def test_worker_refresh_subscriptions_starts_shared_market_feed(monkeypatch, fake_db):
    monkeypatch.setattr("app.domain.brokers.kite.AppConfig.KITE_API_KEY", "kite-key")
    fake_db["broker"].insert_one({"user": "alice", "selectedbroker": "zerodha"})
    fake_db["apis"].insert_one({
        "user": "alice",
        "broker": "zerodha",
        "isConnected": True,
        "accessTokenEncrypted": encrypt_secret("same-day-token"),
        "tokenDate": datetime.datetime.utcnow().date().isoformat(),
    })
    fake_db["strategies"].insert_one({
        "user": "alice",
        "status": "opened",
        "live": True,
        "symbol": "NIFTY26JUNFUT",
    })
    fake_db["kite_instruments"].insert_one({
        "tradingsymbol": "NIFTY26JUNFUT",
        "exchange": "NFO",
        "instrument_token": 123456,
    })
    calls = {"connect": [], "subscribe": []}

    def fake_connect(api_key, access_token, threaded=True):
        calls["connect"].append((api_key, access_token, threaded))
        return {"connected": True, "threaded": threaded}

    def fake_subscribe(tokens):
        calls["subscribe"].append(list(tokens))
        return list(tokens)

    monkeypatch.setattr("app.domain.market_data.providers.kite_market_data.connect", fake_connect)
    monkeypatch.setattr("app.domain.market_data.providers.kite_market_data.subscribe_instruments", fake_subscribe)

    result = TradingWorker(db=fake_db).refresh_subscriptions(user="alice", broker="zerodha")
    health = fake_db["market_feed_health"].find_one({"provider": "zerodha"})
    global_health = fake_db["market_feed_health"].find_one({"provider": "__global__"})

    assert calls["connect"] == [("kite-key", "same-day-token", True)]
    assert calls["subscribe"] == [[123456]]
    assert result[0]["result"]["status"] == "connected"
    assert result[0]["result"]["active_provider"] == "zerodha"
    assert health["status"] == "connected"
    assert health["connected"] is True
    assert global_health["active_provider"] == "zerodha"
    assert global_health["failed_providers"] == ["upstox", "aliceblue"]


def test_market_feed_can_warm_symbols_before_strategy_is_opened(fake_db):
    from app.domain.market_data.manager import MarketFeedManager

    calls = []

    class WorkingFeed:
        def __init__(self, db, prices):
            pass

        def connect(self):
            calls.append(("connect", None))
            return {"connected": True}

        def subscribe(self, symbols):
            calls.append(("subscribe", list(symbols)))
            return {"instrument_tokens": list(symbols), "missing_symbols": []}

        def disconnect(self):
            pass

    manager = MarketFeedManager(
        fake_db,
        provider="zerodha",
        provider_classes={"zerodha": WorkingFeed},
    )

    result = manager.ensure_symbols(["nifty26junfut"], user="alice", broker="aliceblue")
    global_health = fake_db["market_feed_health"].find_one({"provider": "__global__"})

    assert result["success"] is True
    assert result["symbols"] == ["NIFTY26JUNFUT"]
    assert calls == [("connect", None), ("subscribe", ["NIFTY26JUNFUT"])]
    assert global_health["active_provider"] == "zerodha"


def test_market_feed_falls_back_to_aliceblue_when_upstox_fails(fake_db):
    from app.domain.market_data.manager import MarketFeedManager

    fake_db["strategies"].insert_one({
        "user": "alice",
        "status": "opened",
        "live": True,
        "symbol": "NIFTY26JUNFUT",
    })
    calls = []

    class FailingUpstox:
        def __init__(self, db, prices):
            pass

        def connect(self):
            calls.append("upstox")
            raise RuntimeError("upstox down")

    class WorkingAlice:
        def __init__(self, db, prices):
            pass

        def connect(self):
            calls.append("aliceblue")
            return {"connected": True}

        def subscribe(self, symbols):
            return {"instrument_tokens": sorted(symbols), "missing_symbols": []}

        def disconnect(self):
            pass

    class NotCalledZerodha:
        def __init__(self, db, prices):
            pass

        def connect(self):
            calls.append("zerodha")
            raise AssertionError("zerodha should not be reached")

    manager = MarketFeedManager(
        fake_db,
        provider_classes={
            "upstox": FailingUpstox,
            "aliceblue": WorkingAlice,
            "zerodha": NotCalledZerodha,
        },
    )

    result = manager.refresh_subscriptions()
    global_health = fake_db["market_feed_health"].find_one({"provider": "__global__"})

    assert calls == ["upstox", "aliceblue"]
    assert result["active_provider"] == "aliceblue"
    assert global_health["active_provider"] == "aliceblue"
    assert global_health["failed_providers"] == ["upstox"]


def test_market_feed_uses_primary_upstox_when_available(fake_db):
    from app.domain.market_data.manager import MarketFeedManager

    fake_db["strategies"].insert_one({
        "user": "alice",
        "status": "opened",
        "live": True,
        "symbol": "NIFTY26JUNFUT",
    })
    calls = []

    class WorkingUpstox:
        def __init__(self, db, prices):
            pass

        def connect(self):
            calls.append("upstox")
            return {"connected": True}

        def subscribe(self, symbols):
            return {"instrument_tokens": sorted(symbols), "missing_symbols": []}

        def disconnect(self):
            pass

    class NotCalledProvider:
        def __init__(self, db, prices):
            pass

        def connect(self):
            raise AssertionError("fallback provider should not be reached")

    manager = MarketFeedManager(
        fake_db,
        provider_classes={
            "upstox": WorkingUpstox,
            "aliceblue": NotCalledProvider,
            "zerodha": NotCalledProvider,
        },
    )

    result = manager.refresh_subscriptions()
    global_health = fake_db["market_feed_health"].find_one({"provider": "__global__"})

    assert calls == ["upstox"]
    assert result["active_provider"] == "upstox"
    assert global_health["active_provider"] == "upstox"
    assert global_health["failed_providers"] == []


def test_market_feed_marks_disconnected_when_all_providers_fail(fake_db):
    from app.domain.market_data.manager import MarketFeedManager

    class FailingProvider:
        def __init__(self, db, prices):
            pass

        def connect(self):
            raise RuntimeError("feed down")

    manager = MarketFeedManager(
        fake_db,
        provider_classes={
            "upstox": FailingProvider,
            "aliceblue": FailingProvider,
            "zerodha": FailingProvider,
        },
    )

    result = manager.refresh_subscriptions()
    global_health = fake_db["market_feed_health"].find_one({"provider": "__global__"})

    assert result["success"] is False
    assert result["status"] == "disconnected"
    assert global_health["status"] == "disconnected"
    assert global_health["failed_providers"] == ["upstox", "aliceblue", "zerodha"]


def test_market_feed_tick_is_written_to_market_prices(fake_db):
    from app.domain.market_data.manager import MarketFeedManager

    fake_db["kite_instruments"].insert_one({
        "tradingsymbol": "NIFTY26JUNFUT",
        "exchange": "NFO",
        "instrument_token": 123456,
    })
    manager = MarketFeedManager(fake_db, provider="zerodha")

    row = manager.on_kite_tick({
        "instrument_token": 123456,
        "last_price": 24100.25,
        "depth": {
            "buy": [{"price": 24100.0}],
            "sell": [{"price": 24100.5}],
        },
    })
    saved = fake_db["market_prices"].find_one({"symbol": "NIFTY26JUNFUT", "provider": "zerodha"})

    assert row["ltp"] == 24100.25
    assert saved["bid"] == 24100.0
    assert saved["ask"] == 24100.5

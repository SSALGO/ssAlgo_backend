import datetime

import pytest

from app.core.secrets import encrypt_secret
from app.domain.brokers.kite import KiteService, KiteTokenExpired
from app.domain.market_data.kite_market_data import KiteMarketDataService


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


def test_kite_login_url_can_include_oauth_state(monkeypatch, fake_db):
    monkeypatch.setattr("app.domain.brokers.kite.AppConfig.KITE_API_KEY", "kite-key")
    monkeypatch.setattr("app.domain.brokers.kite.AppConfig.KITE_API_SECRET", "kite-secret")

    login_url = KiteService(fake_db).generate_login_url(state="state-123")

    assert login_url == (
        "https://kite.zerodha.com/connect/login?"
        "v=3&api_key=kite-key&state=state-123"
    )


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

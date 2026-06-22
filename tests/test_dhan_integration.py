import datetime
import asyncio

import pytest
from fastapi import HTTPException

from app.api.fastapi_schemas import BrokerCredentialsRequest
from app.api.fastapi_services import FastAPITradingServices
from app.core.logging_config import sanitize_log_value
from app.core.secrets import decrypt_secret, encrypt_secret
from app.domain.audit.service import _jsonable
from app.domain.brokers.adapters.base import BrokerCredentials, BrokerOrder
from app.domain.brokers.adapters.dhan import DhanBrokerAdapter
from app.domain.brokers.dhan import DhanError, DhanService


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self.payload = payload if payload is not None else {}

    def json(self):
        return self.payload


class FakeHttp:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append({
            "method": method,
            "url": url,
            **kwargs,
        })
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def test_dhan_profile_verification_uses_safe_headers_and_parses_expiry():
    http = FakeHttp([
        FakeResponse(payload={
            "dhanClientId": "1100000001",
            "tokenValidity": "31/12/2099 23:59",
            "activeSegment": "NSE_FNO",
        })
    ])

    result = DhanService("1100000001", "secret-token", http=http).verify_connection()

    assert result["success"] is True
    assert result["data"]["dhanClientId"] == "1100000001"
    assert result["token_expires_at"].tzinfo is not None
    call = http.calls[0]
    assert call["method"] == "GET"
    assert call["url"].endswith("/profile")
    assert call["headers"]["access-token"] == "secret-token"
    assert call["headers"]["dhanClientId"] == "1100000001"


def test_dhan_invalid_token_is_normalized():
    http = FakeHttp([
        FakeResponse(
            status_code=401,
            payload={"errorCode": "DH-901", "errorMessage": "Invalid Token"},
        )
    ])

    with pytest.raises(DhanError) as error:
        DhanService("1100000001", "bad-token", http=http).verify_connection()

    assert error.value.category == "authentication"
    assert error.value.token_invalid is True
    assert "invalid or expired" in str(error.value).lower()


@pytest.mark.parametrize(
    ("payload", "category"),
    [
        ({"errorMessage": "Insufficient balance"}, "insufficient_balance"),
        ({"errorMessage": "Static IP not allowed"}, "static_ip_restricted"),
        ({"errorCode": "DH-903", "errorMessage": "Segment not enabled"}, "segment_disabled"),
        ({"errorCode": "DH-904", "errorMessage": "Rate limit"}, "rate_limit"),
        ({"errorCode": "DH-906", "errorMessage": "Order rejected"}, "order_rejected"),
    ],
)
def test_dhan_error_normalization(payload, category):
    error = DhanService.normalize_error(payload, http_status=400)
    assert error.category == category


def test_dhan_order_payload_mapping_and_mock_placement():
    captured = {}

    class FakeService:
        def __init__(self, client_id, access_token):
            captured["credentials"] = (client_id, access_token)

        def verify_connection(self):
            return {
                "success": True,
                "message": "ok",
                "data": {"dhanClientId": "1100000001"},
                "token_expires_at": None,
            }

        def place_order(self, payload):
            captured["payload"] = payload
            return {
                "success": True,
                "broker": "dhan",
                "action": "place_order",
                "status": "TRANSIT",
                "message": "Order submitted",
                "broker_order_id": "DHAN-123",
                "data": {"orderId": "DHAN-123"},
            }

    adapter = DhanBrokerAdapter(service_class=FakeService)
    adapter.login(BrokerCredentials(
        user="alice",
        broker="dhan",
        values={
            "dhanClientId": "1100000001",
            "accessToken": "secret-token",
        },
    ))
    result = adapter.place_order(BrokerOrder(
        user="alice",
        broker="dhan",
        symbol="NIFTY",
        side="BUY",
        quantity=50,
        exchange="NFO",
        product_type="MIS",
        order_type="SL-M",
        strategy_id="S1",
        metadata={
            "security_id": "12345",
            "idempotency_key": "strategy-signal-12345678901234567890",
            "trigger_price": 101.5,
        },
    ))

    payload = captured["payload"]
    assert captured["credentials"] == ("1100000001", "secret-token")
    assert payload["exchangeSegment"] == "NSE_FNO"
    assert payload["productType"] == "INTRADAY"
    assert payload["orderType"] == "STOP_LOSS_MARKET"
    assert payload["securityId"] == "12345"
    assert payload["triggerPrice"] == 101.5
    assert len(payload["correlationId"]) <= 25
    assert result["broker_order_id"] == "DHAN-123"


def test_dhan_account_modify_and_cancel_endpoints_use_v2_paths():
    http = FakeHttp([
        FakeResponse(payload={"availabelBalance": 100000}),
        FakeResponse(payload=[{"securityId": "12345", "netQty": 50}]),
        FakeResponse(payload=[{"securityId": "67890", "totalQty": 10}]),
        FakeResponse(payload=[{"orderId": "D1", "orderStatus": "PENDING"}]),
        FakeResponse(payload={"orderId": "D1", "orderStatus": "PENDING"}),
        FakeResponse(payload={"orderId": "D1", "orderStatus": "CANCELLED"}),
    ])
    service = DhanService("1100000001", "secret-token", http=http)

    assert service.get_funds()["success"] is True
    assert service.get_positions()["success"] is True
    assert service.get_holdings()["success"] is True
    assert service.get_orderbook()["success"] is True
    assert service.modify_order("D1", {"quantity": 25})["broker_order_id"] == "D1"
    assert service.cancel_order("D1")["status"] == "CANCELLED"

    assert [
        (call["method"], call["url"].split("/v2")[-1])
        for call in http.calls
    ] == [
        ("GET", "/fundlimit"),
        ("GET", "/positions"),
        ("GET", "/holdings"),
        ("GET", "/orders"),
        ("PUT", "/orders/D1"),
        ("DELETE", "/orders/D1"),
    ]


def test_dhan_credentials_are_encrypted_and_legacy_aliases_decrypt(fake_db):
    fake_db["apis"].insert_one({
        "user": "alice",
        "broker": "dhan",
        "client_id": encrypt_secret("1100000001"),
        "access_token": encrypt_secret("legacy-token"),
    })

    class FakeService:
        def __init__(self, client_id, access_token):
            assert client_id == "1100000001"
            assert access_token == "legacy-token"

        def verify_connection(self):
            return {
                "success": True,
                "message": "ok",
                "data": {"dhanClientId": "1100000001"},
                "token_expires_at": None,
            }

    result = DhanBrokerAdapter(db=fake_db, service_class=FakeService).login(
        BrokerCredentials(user="alice", broker="dhan")
    )
    assert result["success"] is True


def test_dhan_save_verifies_before_persisting_and_encrypts(monkeypatch, fake_db):
    from app.api import fastapi_routers

    class FakeDhanService:
        def __init__(self, client_id, access_token):
            assert client_id == "1100000001"
            assert access_token == "secret-token"

        def verify_connection(self):
            return {
                "success": True,
                "message": "ok",
                "data": {"dhanClientId": "1100000001"},
                "token_expires_at": datetime.datetime(
                    2099, 12, 31, 18, 29, tzinfo=datetime.UTC
                ),
            }

    monkeypatch.setattr(fastapi_routers, "get_database", lambda: fake_db)
    monkeypatch.setattr(fastapi_routers, "DhanService", FakeDhanService)
    services = FastAPITradingServices(fake_db)

    response = fastapi_routers.save_broker_credentials(
        "dhan",
        BrokerCredentialsRequest(values={
            "dhanClientId": "1100000001",
            "accessToken": "secret-token",
        }),
        user={"username": "alice"},
        services=services,
    )

    assert response.success is True
    row = fake_db["apis"].find_one({"user": "alice", "broker": "dhan"})
    assert row["dhanClientId"] != "1100000001"
    assert row["accessToken"] != "secret-token"
    assert decrypt_secret(row["dhanClientId"]) == "1100000001"
    assert decrypt_secret(row["accessToken"]) == "secret-token"
    assert "client_id" not in row
    assert "access_token" not in row
    health = fake_db["broker_health"].find_one({"user": "alice", "broker": "dhan"})
    assert health["login_status"] == "connected"
    assert health["token_status"] == "valid"


def test_dhan_invalid_credentials_do_not_save_or_overwrite(monkeypatch, fake_db):
    from app.api import fastapi_routers

    fake_db["apis"].insert_one({
        "user": "alice",
        "broker": "dhan",
        "dhanClientId": encrypt_secret("old-client"),
        "accessToken": encrypt_secret("old-token"),
    })

    class FailingDhanService:
        def __init__(self, _client_id, _access_token):
            pass

        def verify_connection(self):
            raise DhanError(
                "authentication",
                "Dhan access token is invalid or expired.",
                code="DH-901",
                token_invalid=True,
            )

    monkeypatch.setattr(fastapi_routers, "get_database", lambda: fake_db)
    monkeypatch.setattr(fastapi_routers, "DhanService", FailingDhanService)
    services = FastAPITradingServices(fake_db)

    response = fastapi_routers.save_broker_credentials(
        "dhan",
        BrokerCredentialsRequest(values={
            "dhanClientId": "new-client",
            "accessToken": "bad-token",
        }),
        user={"username": "alice"},
        services=services,
    )

    assert response.success is False
    assert response.data["error"]["category"] == "authentication"
    row = fake_db["apis"].find_one({"user": "alice", "broker": "dhan"})
    assert decrypt_secret(row["dhanClientId"]) == "old-client"
    assert decrypt_secret(row["accessToken"]) == "old-token"


def test_dhan_sensitive_fields_are_masked_in_logs_and_audits():
    value = {
        "dhanClientId": "1100000001",
        "accessToken": "secret-token",
        "securityId": "12345",
    }
    logged = sanitize_log_value(value)
    audited = _jsonable(value)

    assert logged["dhanClientId"] != "1100000001"
    assert logged["accessToken"] != "secret-token"
    assert audited["dhanClientId"] == "***"
    assert audited["accessToken"] == "***"
    assert logged["securityId"] == "12345"


def test_dhan_postback_updates_normalized_and_legacy_orders(monkeypatch, fake_db):
    from app.api import fastapi_routers

    fake_db["apis"].insert_one({
        "user": "alice",
        "broker": "dhan",
        "dhanClientId": encrypt_secret("1100000001"),
        "accessToken": encrypt_secret("secret-token"),
    })
    fake_db["order_logs"].insert_one({
        "user": "alice",
        "broker": "dhan",
        "orderId": "DHAN-123",
        "status": "submitted",
    })
    fake_db["normalized_orders"].insert_one({
        "user": "alice",
        "broker": "dhan",
        "broker_order_id": "DHAN-123",
        "status": "submitted",
        "events": [],
    })
    fake_db["strategies"].insert_one({
        "user": "alice",
        "botcode": "S1",
        "strategy": "FRACTALNUBIATIMEHEDGEORDER",
        "status": "paused",
        "position": "in",
    })
    fake_db["Opositions"].insert_one({
        "user": "alice",
        "botcode": "S1",
        "status": "exit_pending",
        "pos": [
            {
                "optionname": "NIFTY23JUN26C24100",
                "exit_order_id": "DHAN-123",
                "exit_order_status": "submitted",
            },
            {
                "optionname": "NIFTY23JUN26P24100",
                "exit_order_id": "DHAN-456",
                "exit_order_status": "complete",
            },
        ],
    })
    monkeypatch.setattr(fastapi_routers, "get_database", lambda: fake_db)
    monkeypatch.setattr(fastapi_routers.AppConfig, "DHAN_POSTBACK_SECRET", "")

    class FakeRequest:
        query_params = {}

        async def json(self):
            return {
                "dhanClientId": "1100000001",
                "orderId": "DHAN-123",
                "orderStatus": "TRADED",
                "filled_qty": 50,
                "averageTradedPrice": 101.25,
            }

    response = asyncio.run(fastapi_routers.dhan_postback(FakeRequest()))
    legacy = fake_db["order_logs"].find_one({"orderId": "DHAN-123"})
    normalized = fake_db["normalized_orders"].find_one({
        "broker_order_id": "DHAN-123"
    })

    assert response.success is True
    assert response.data["matched"] is True
    assert legacy["postbackStatus"] == "TRADED"
    assert legacy["status"] == "filled"
    assert normalized["status"] == "filled"
    assert normalized["events"][-1]["data"]["filled_quantity"] == 50
    legacy_position = fake_db["Opositions"].find_one({"botcode": "S1"})
    assert legacy_position["status"] == "close"
    assert {
        leg["exit_order_status"] for leg in legacy_position["pos"]
    } == {"complete"}
    assert fake_db["strategies"].find_one({"botcode": "S1"})["position"] == "out"
    assert response.data["legacy_position_closed"] is True
    assert fake_db["broker_health"].find_one({
        "user": "alice",
        "broker": "dhan",
    })["last_postback_status"] == "TRADED"
    assert fake_db["broker_postbacks"].find_one({
        "order_id": "DHAN-123"
    })["user"] == "alice"


def test_dhan_postback_rejects_unknown_client(monkeypatch, fake_db):
    from app.api import fastapi_routers

    monkeypatch.setattr(fastapi_routers, "get_database", lambda: fake_db)
    monkeypatch.setattr(fastapi_routers.AppConfig, "DHAN_POSTBACK_SECRET", "")

    class FakeRequest:
        query_params = {}

        async def json(self):
            return {
                "dhanClientId": "unknown",
                "orderId": "DHAN-123",
                "orderStatus": "PENDING",
            }

    with pytest.raises(HTTPException) as error:
        asyncio.run(fastapi_routers.dhan_postback(FakeRequest()))

    assert error.value.status_code == 401


def test_dhan_postback_requires_configured_url_secret(monkeypatch, fake_db):
    from app.api import fastapi_routers

    monkeypatch.setattr(fastapi_routers, "get_database", lambda: fake_db)
    monkeypatch.setattr(
        fastapi_routers.AppConfig,
        "DHAN_POSTBACK_SECRET",
        "postback-secret",
    )

    class FakeRequest:
        query_params = {"token": "wrong-secret"}

    with pytest.raises(HTTPException) as error:
        asyncio.run(fastapi_routers.dhan_postback(FakeRequest()))

    assert error.value.status_code == 401

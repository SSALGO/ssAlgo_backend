import json
import datetime
import asyncio
import pytest

from fastapi.testclient import TestClient

import fastapi_app
from app.core.webhook import parse_webhook_payload
from app.domain.auth.reset_service import (
    MAX_OTP_ATTEMPTS,
    create_otp,
    create_reset_token,
    hash_password,
    verify_otp_hash,
    verify_reset_token,
)
from app.api.legacy_compat import common, payments
from conftest import FakeDatabase


class FakeRequest:
    method = "POST"
    headers = {"content-type": "application/json"}

    def __init__(self, payload):
        self.payload = payload

    async def json(self):
        return self.payload


def test_protected_fastapi_route_requires_bearer_token():
    client = TestClient(fastapi_app.app)
    response = client.get("/api/brokers/status")
    assert response.status_code == 401
    assert response.json()["detail"] == "Missing bearer token"


def test_webhook_parser_accepts_json_object():
    payload = parse_webhook_payload(json.dumps({
        "alert_name": "abc",
        "stocks": "NIFTY",
        "trigger_prices": "100",
    }))
    assert payload["alert_name"] == "abc"
    assert payload["stocks"] == "NIFTY"


def test_webhook_parser_rejects_python_literal_string():
    try:
        parse_webhook_payload("{'alert_name': 'abc', 'stocks': 'NIFTY'}")
    except ValueError as exc:
        assert "valid JSON" in str(exc)
    else:
        raise AssertionError("Python literal payload should be rejected")


def test_reset_token_is_hashed_and_expires():
    token, token_hash, expiration = create_reset_token()
    user = {
        "reset_token_hash": token_hash,
        "reset_token_expiration": expiration,
        "reset_token_used": False,
    }

    assert token != token_hash
    assert verify_reset_token(user, token) is True

    user["reset_token_expiration"] = datetime.datetime.utcnow() - datetime.timedelta(seconds=1)
    assert verify_reset_token(user, token) is False


def test_otp_hash_enforces_attempt_limit_and_value():
    otp, otp_hash, expiration = create_otp()
    user = {"otp_hash": otp_hash, "otp_expiration": expiration, "otp_attempts": 0}

    ok, message = verify_otp_hash(user, otp)
    assert ok is True
    assert message == ""

    ok, message = verify_otp_hash(user, "000000")
    assert ok is False
    assert "Invalid OTP" in message

    user["otp_attempts"] = MAX_OTP_ATTEMPTS
    ok, message = verify_otp_hash(user, otp)
    assert ok is False
    assert "attempt limit" in message


def test_password_policy_rejects_weak_password():
    try:
        hash_password("12345678")
    except ValueError as exc:
        assert "mix" in str(exc)
    else:
        raise AssertionError("Weak numeric password should be rejected")


def test_payment_verification_uses_server_side_duration(monkeypatch):
    db = FakeDatabase()

    class FakeOrder:
        def create(self, data):
            return {"id": "order_1", "amount": data["amount"], "currency": data["currency"]}

    class FakeUtility:
        def verify_payment_signature(self, params):
            return True

    class FakePayment:
        def fetch(self, payment_id):
            return {"amount": 299900, "currency": "INR", "id": payment_id}

    class FakeRazorpay:
        order = FakeOrder()
        utility = FakeUtility()
        payment = FakePayment()

    monkeypatch.setattr(common, "get_database", lambda: db)
    monkeypatch.setattr(payments, "require_razorpay_client", lambda: FakeRazorpay())
    monkeypatch.setattr(payments.AppConfig, "RAZORPAY_KEY_ID", "rzp_test")

    user = {"username": "alice", "email": "alice@example.com", "mobile": "1"}
    created = asyncio.run(payments.api_pay(FakeRequest({"price": "1 Month"}), user=user))
    assert created.data["duration"] == 30

    verified = asyncio.run(payments.api_pay_verify(
        FakeRequest({
            "order_id": "order_1",
            "payment_id": "pay_1",
            "signature": "sig",
            "duration": "3650",
        }),
        user=user,
    ))
    assert verified.data["subscription"]["end"] != "None"
    assert db["payment_orders"].rows[0]["duration"] == 30

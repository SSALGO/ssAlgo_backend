import json

from fastapi.testclient import TestClient

import fastapi_app
from app.core.webhook import parse_webhook_payload


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

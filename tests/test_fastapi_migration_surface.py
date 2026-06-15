from fastapi.testclient import TestClient

import fastapi_app
from app.realtime.dashboard import DashboardConnectionManager
from app.workers.control import WorkerControlService


def test_legacy_bridge_requires_bearer_before_forwarding():
    client = TestClient(fastapi_app.app)
    response = client.post("/api_user_profile", data={"token": "alice"})
    assert response.status_code == 401
    assert response.json()["detail"] == "Missing bearer token"


def test_fastapi_exposes_migration_and_worker_routes():
    paths = {route.path for route in fastapi_app.app.routes if hasattr(route, "path")}
    assert "/api/migration/legacy-routes" in paths
    assert "/api/worker/status" in paths
    assert "/api_{legacy_path:path}" not in paths
    assert "/api_users" in paths
    assert "/api_add_ssalgo" in paths
    assert "/api_pay" in paths
    assert "/api_historicalbacktest" in paths


def test_legacy_login_token_is_real_jwt(monkeypatch):
    from app.api import fastapi_routers

    user = {"username": "alice", "password": b"hash", "email": "a@example.com"}
    monkeypatch.setattr(fastapi_routers, "get_user_by_username_or_email", lambda _identifier: user)
    monkeypatch.setattr(fastapi_routers, "verify_password", lambda _password, _hash: True)
    monkeypatch.setattr(fastapi_routers, "ensure_free_subscription", lambda _username: None)
    monkeypatch.setattr(fastapi_routers, "create_compatible_access_token", lambda username: f"jwt-for-{username}")

    client = TestClient(fastapi_app.app)
    response = client.post("/api_login", data={"username": "alice", "password": "secret"})

    assert response.status_code == 200
    body = response.json()
    assert body["username"] == "alice"
    assert body["token"] == "jwt-for-alice"
    assert body["access_token"] == "jwt-for-alice"


def test_strategy_form_templates_are_loaded_from_backend_root():
    from app.api.legacy_compat.common import strategy_forms

    strategy_forms.cache_clear()
    forms = strategy_forms()

    eqssalgo_fields = forms["add_eqssalgo_form.html"]
    assert eqssalgo_fields
    assert any(field.get("name") == "botname" for field in eqssalgo_fields)
    assert any(field.get("name") == "symbol[]" for field in eqssalgo_fields)


def test_user_profile_updates_trading_limits(fake_db, monkeypatch):
    from fastapi.testclient import TestClient

    import fastapi_app
    from app.api.fastapi_auth import get_current_user
    from app.api.legacy_compat import common

    user = {
        "username": "alice",
        "email": "alice@example.test",
        "day_profit_limit": "25000",
        "day_loss_limit": "25000",
        "trade_limit": "100",
    }
    fake_db["users"].insert_one(user)
    monkeypatch.setattr(common, "get_database", lambda: fake_db)
    fastapi_app.app.dependency_overrides[get_current_user] = lambda: user

    try:
        client = TestClient(fastapi_app.app)
        result = client.post(
            "/api_user_profile",
            data={
                "day_profit_limit": "26000",
                "day_loss_limit": "24000",
                "trade_limit": "101",
            },
        )
    finally:
        fastapi_app.app.dependency_overrides.pop(get_current_user, None)

    assert result.status_code == 200
    assert result.json()["data"]["day_profit_limit"] == "26000"
    assert fake_db["users"].find_one({"username": "alice"})["trade_limit"] == "101"


def test_legacy_dashboard_uses_active_broker_health_for_connection_status(fake_db, monkeypatch):
    from app.api.legacy_compat import dashboard

    fake_db["subscriptionperiod"].insert_one({"user": "alice", "end": "2099-12-31"})
    fake_db["broker"].insert_one({"user": "alice", "selectedbroker": "aliceblue"})
    fake_db["broker_health"].insert_one({
        "user": "alice",
        "broker": "aliceblue",
        "login_status": "connected",
    })
    monkeypatch.setattr(dashboard, "get_database", lambda: fake_db)

    connected = dashboard.api_index(user={"username": "alice", "admin": False})

    assert connected.data["userlog"] is True
    assert connected.data["broker"] == "aliceblue"
    assert connected.data["broker_health"]["login_status"] == "connected"

    fake_db["broker_health"].update_one(
        {"user": "alice", "broker": "aliceblue"},
        {"$set": {"login_status": "disconnected"}},
    )
    disconnected = dashboard.api_index(user={"username": "alice", "admin": False})

    assert disconnected.data["userlog"] is False
    assert disconnected.data["broker_health"]["login_status"] == "disconnected"


def test_broker_secret_reveal_is_owner_scoped_audited_and_not_cached(fake_db, monkeypatch):
    from app.api import fastapi_routers
    from app.api.fastapi_auth import get_current_user
    from app.core.secrets import encrypt_secret

    fake_db["apis"].insert_one({
        "user": "alice",
        "broker": "aliceblue",
        "auth_code": encrypt_secret("alice-auth-code"),
    })
    fake_db["apis"].insert_one({
        "user": "bob",
        "broker": "aliceblue",
        "auth_code": encrypt_secret("bob-auth-code"),
    })
    monkeypatch.setattr(fastapi_routers, "get_database", lambda: fake_db)
    fastapi_app.app.dependency_overrides[get_current_user] = lambda: {"username": "alice"}

    try:
        client = TestClient(fastapi_app.app)
        response = client.post(
            "/api/brokers/aliceblue/credentials/reveal",
            json={"field": "AUTH_CODE"},
        )
    finally:
        fastapi_app.app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 200
    assert response.json()["data"] == {
        "field": "auth_code",
        "value": "alice-auth-code",
    }
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"
    audit = fake_db["audit_logs"].find_one({"event": "broker_credential_revealed"})
    assert audit["user"] == "alice"
    assert audit["details"] == {"broker": "aliceblue", "field": "auth_code"}


def test_worker_control_queue_and_status(fake_db):
    control = WorkerControlService(fake_db)
    queued = control.enqueue("stop", "admin")
    pending = control.next_pending()
    assert str(pending["_id"]) == queued["_id"]
    control.complete(pending["_id"], {"ok": True})
    status = control.heartbeat(state="running")
    assert status["state"] == "running"
    assert control.get_status()["state"] == "running"


def test_dashboard_message_parser():
    manager = DashboardConnectionManager()
    assert manager.parse_message("ping") == {"type": "ping"}
    assert manager.parse_message('{"type":"worker_status"}') == {"type": "worker_status"}
    assert manager.parse_message("not-json")["type"] == "unknown"

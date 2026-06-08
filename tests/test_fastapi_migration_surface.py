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

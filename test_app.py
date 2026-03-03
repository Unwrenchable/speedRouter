"""Tests for speedRouter Flask application."""

import json
import pytest

from app import app as flask_app


@pytest.fixture
def client():
    flask_app.config["TESTING"] = True
    flask_app.config["SECRET_KEY"] = "test-secret"
    with flask_app.test_client() as c:
        yield c


# ── Index ─────────────────────────────────────────────────────────────────────

def test_index(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"speedRouter" in resp.data


# ── /api/connect validation ───────────────────────────────────────────────────

def test_connect_missing_fields(client):
    resp = client.post("/api/connect", json={})
    assert resp.status_code == 400
    data = resp.get_json()
    assert data["ok"] is False
    assert "required" in data["error"].lower()


def test_connect_invalid_ip(client):
    resp = client.post(
        "/api/connect",
        json={"gateway": "not-an-ip", "username": "admin", "password": "pass"},
    )
    assert resp.status_code == 400
    data = resp.get_json()
    assert data["ok"] is False
    assert "Invalid" in data["error"]


def test_connect_unreachable_modem(client):
    # 192.0.2.x is reserved TEST-NET – guaranteed unreachable
    resp = client.post(
        "/api/connect",
        json={"gateway": "192.0.2.1", "username": "admin", "password": "pass"},
    )
    # Must get an error response (502 or 504), never 200 ok
    data = resp.get_json()
    assert data["ok"] is False


# ── /api/optimize without session ────────────────────────────────────────────

def test_optimize_requires_connection(client):
    resp = client.post("/api/optimize", json={})
    assert resp.status_code == 401
    data = resp.get_json()
    assert data["ok"] is False


# ── /api/speedtest ────────────────────────────────────────────────────────────

def test_speedtest_returns_json(client, monkeypatch):
    """Monkeypatch speedtest.Speedtest so the test is fast and offline."""

    class FakeResults:
        ping = 12.5
        server = {"host": "test.server.net"}

    class FakeSpeedtest:
        results = FakeResults()

        def get_best_server(self):
            pass

        def download(self):
            return 100_000_000  # 100 Mbps

        def upload(self):
            return 50_000_000   # 50 Mbps

    import speedtest as st_module
    monkeypatch.setattr(st_module, "Speedtest", FakeSpeedtest)

    resp = client.post("/api/speedtest", json={})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["download_mbps"] == 100.0
    assert data["upload_mbps"] == 50.0
    assert data["ping_ms"] == 12.5
    assert data["server"] == "test.server.net"


# ── /api/vpn/config without session ──────────────────────────────────────────

def test_vpn_requires_connection(client):
    resp = client.post("/api/vpn/config", json={
        "endpoint": "1.2.3.4:51820",
        "public_key": "abc",
        "private_key": "xyz",
    })
    assert resp.status_code == 401
    data = resp.get_json()
    assert data["ok"] is False


def test_vpn_missing_fields(client):
    """Simulate a connected session then send incomplete VPN payload."""
    with client.session_transaction() as sess:
        sess["gateway"] = "192.0.2.1"
        sess["username"] = "admin"
        sess["password"] = "pass"

    resp = client.post("/api/vpn/config", json={"endpoint": "1.2.3.4:51820"})
    assert resp.status_code == 400
    data = resp.get_json()
    assert data["ok"] is False


# ── /api/disconnect ───────────────────────────────────────────────────────────

def test_disconnect(client):
    with client.session_transaction() as sess:
        sess["gateway"] = "192.168.1.1"

    resp = client.post("/api/disconnect", json={})
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True

    # Session should be cleared
    with client.session_transaction() as sess:
        assert "gateway" not in sess

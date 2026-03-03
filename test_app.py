"""Tests for speedRouter Flask application."""

import json
import pytest
import requests as req_module

from app import app as flask_app
import app as app_module
from agent_tools.registry import load_agents, load_profiles, find_agents, assess_agent_access, recommend_profile


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


def test_csp_header_present(client):
    """Every response must include a Content-Security-Policy header."""
    resp = client.get("/")
    assert "Content-Security-Policy" in resp.headers
    csp = resp.headers["Content-Security-Policy"]
    # eval must NOT be explicitly allowed
    assert "unsafe-eval" not in csp
    # scripts only from self
    assert "script-src 'self'" in csp


def test_x_frame_options_header(client):
    """X-Frame-Options: DENY must be set to prevent clickjacking."""
    resp = client.get("/")
    assert resp.headers.get("X-Frame-Options") == "DENY"


# ── /api/network/gateway ──────────────────────────────────────────────────────

def test_gateway_endpoint_success(client, monkeypatch):
    """When gateway detection succeeds, endpoint returns ok=True and the IP."""
    import app as app_module
    monkeypatch.setattr(app_module, "_detect_gateway", lambda: "192.168.1.1")
    resp = client.get("/api/network/gateway")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["gateway"] == "192.168.1.1"


def test_gateway_endpoint_failure(client, monkeypatch):
    """When gateway detection fails, endpoint returns ok=False."""
    import app as app_module
    monkeypatch.setattr(app_module, "_detect_gateway", lambda: None)
    resp = client.get("/api/network/gateway")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is False
    assert "error" in data


# ── /api/status ───────────────────────────────────────────────────────────────

def test_status_not_connected(client):
    """Without a session, /api/status returns connected=False."""
    resp = client.get("/api/status")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["connected"] is False


def test_status_connected(client):
    """With an active session, /api/status returns connected=True and the gateway."""
    with client.session_transaction() as sess:
        sess["gateway"] = "192.168.1.1"
        sess["username"] = "admin"
        sess["password"] = "pass"

    resp = client.get("/api/status")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["connected"] is True
    assert data["gateway"] == "192.168.1.1"


# ── /api/connect ──────────────────────────────────────────────────────────────

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


def test_connect_succeeds_when_modem_root_returns_401(client, monkeypatch):
    """Connection should succeed even if the modem root URL returns 401.

    Many routers return 401 on their homepage; the old code called
    probe.raise_for_status() which would incorrectly treat this as a failure.
    """
    class _FakeResponse:
        status_code = 401
        ok = False
        def raise_for_status(self):
            raise req_module.HTTPError("401 Unauthorized")

    class _FakeSession:
        def post(self, *args, **kwargs):
            r = _FakeResponse()
            r.status_code = 200
            r.ok = True
            r.raise_for_status = lambda: None
            return r
        def get(self, *args, **kwargs):
            return _FakeResponse()

    monkeypatch.setattr(app_module, "_modem_session", lambda *a, **kw: _FakeSession())

    resp = client.post(
        "/api/connect",
        json={"gateway": "192.168.1.1", "username": "admin", "password": "pass"},
    )
    data = resp.get_json()
    assert data["ok"] is True
    assert data["gateway"] == "192.168.1.1"


def test_connect_falls_back_to_https(client, monkeypatch):
    """HTTP probe failure triggers an HTTPS retry; connection succeeds over HTTPS."""
    class _FakeSession:
        verify = True
        auth = None
        def post(self, *a, **kw):
            class R:
                ok = True
                status_code = 200
                def raise_for_status(self): pass
            return R()
        def get(self, url, **kw):
            if url.startswith("http://"):
                raise req_module.ConnectionError("port 80 closed")
            class R:
                status_code = 200
                ok = True
            return R()

    monkeypatch.setattr(app_module, "_modem_session", lambda *a, **kw: _FakeSession())

    resp = client.post(
        "/api/connect",
        json={"gateway": "192.168.0.1", "username": "admin", "password": "pass"},
    )
    data = resp.get_json()
    assert data["ok"] is True
    assert data["gateway"] == "192.168.0.1"


def test_connect_timeout_on_http_falls_back_to_https(client, monkeypatch):
    """HTTP timeout also triggers the HTTPS fallback (router accepts TCP/80 but hangs)."""
    class _FakeSession:
        verify = True
        auth = None
        def post(self, *a, **kw):
            class R:
                ok = True
                status_code = 200
                def raise_for_status(self): pass
            return R()
        def get(self, url, **kw):
            if url.startswith("http://"):
                raise req_module.Timeout("HTTP timed out")
            class R:
                status_code = 200
                ok = True
            return R()

    monkeypatch.setattr(app_module, "_modem_session", lambda *a, **kw: _FakeSession())

    resp = client.post(
        "/api/connect",
        json={"gateway": "192.168.0.1", "username": "admin", "password": "pass"},
    )
    data = resp.get_json()
    assert data["ok"] is True
    assert data["gateway"] == "192.168.0.1"


def test_connect_https_scheme_stored_in_session(client, monkeypatch):
    """When the HTTPS fallback succeeds, 'https' is stored in the Flask session."""
    class _FakeSession:
        verify = True
        auth = None
        def post(self, *a, **kw):
            class R:
                ok = True
                status_code = 200
                def raise_for_status(self): pass
            return R()
        def get(self, url, **kw):
            if url.startswith("http://"):
                raise req_module.ConnectionError()
            class R:
                status_code = 200
                ok = True
            return R()

    monkeypatch.setattr(app_module, "_modem_session", lambda *a, **kw: _FakeSession())
    client.post(
        "/api/connect",
        json={"gateway": "192.168.0.1", "username": "admin", "password": "pass"},
    )
    with client.session_transaction() as sess:
        assert sess.get("scheme") == "https"


def test_connect_http_scheme_stored_in_session(client, monkeypatch):
    """When the HTTP probe succeeds, 'http' is stored in the Flask session."""
    class _FakeSession:
        def post(self, *a, **kw):
            class R:
                ok = True
                status_code = 200
                def raise_for_status(self): pass
            return R()
        def get(self, url, **kw):
            class R:
                status_code = 200
                ok = True
            return R()

    monkeypatch.setattr(app_module, "_modem_session", lambda *a, **kw: _FakeSession())
    client.post(
        "/api/connect",
        json={"gateway": "192.168.1.1", "username": "admin", "password": "pass"},
    )
    with client.session_transaction() as sess:
        assert sess.get("scheme") == "http"


def test_connect_both_schemes_fail(client, monkeypatch):
    """If both HTTP and HTTPS probes fail, a 502 with a clear error is returned."""
    class _FakeSession:
        verify = True
        auth = None
        def post(self, *a, **kw):
            class R:
                ok = True
                status_code = 200
                def raise_for_status(self): pass
            return R()
        def get(self, url, **kw):
            raise req_module.ConnectionError("unreachable")

    monkeypatch.setattr(app_module, "_modem_session", lambda *a, **kw: _FakeSession())
    resp = client.post(
        "/api/connect",
        json={"gateway": "192.168.0.1", "username": "admin", "password": "pass"},
    )
    data = resp.get_json()
    assert data["ok"] is False
    assert resp.status_code == 502


def test_connect_both_schemes_timeout_returns_504(client, monkeypatch):
    """If both HTTP and HTTPS probes time out, a 504 is returned."""
    class _FakeSession:
        verify = True
        auth = None
        def post(self, *a, **kw):
            class R:
                ok = True
                status_code = 200
                def raise_for_status(self): pass
            return R()
        def get(self, url, **kw):
            raise req_module.Timeout("timed out")

    monkeypatch.setattr(app_module, "_modem_session", lambda *a, **kw: _FakeSession())
    resp = client.post(
        "/api/connect",
        json={"gateway": "192.168.0.1", "username": "admin", "password": "pass"},
    )
    data = resp.get_json()
    assert data["ok"] is False
    assert resp.status_code == 504


def test_connect_succeeds_on_alt_port(client, monkeypatch):
    """When port 80 is closed but port 8080 responds, connection succeeds on port 8080."""
    class _FakeSession:
        verify = True
        auth = None
        def post(self, *a, **kw):
            class R:
                ok = True
                status_code = 200
                def raise_for_status(self): pass
            return R()
        def get(self, url, **kw):
            if ":8080" not in url:
                raise req_module.ConnectionError("port closed")
            class R:
                status_code = 200
                ok = True
            return R()

    monkeypatch.setattr(app_module, "_modem_session", lambda *a, **kw: _FakeSession())
    resp = client.post(
        "/api/connect",
        json={"gateway": "192.168.0.1", "username": "admin", "password": "pass"},
    )
    data = resp.get_json()
    assert data["ok"] is True
    assert data["gateway"] == "192.168.0.1"
    with client.session_transaction() as sess:
        assert sess.get("port") == 8080


def test_connect_port_stored_in_session(client, monkeypatch):
    """When a connection succeeds on port 80, port=80 is stored in the Flask session."""
    class _FakeSession:
        def post(self, *a, **kw):
            class R:
                ok = True
                status_code = 200
                def raise_for_status(self): pass
            return R()
        def get(self, url, **kw):
            class R:
                status_code = 200
                ok = True
            return R()

    monkeypatch.setattr(app_module, "_modem_session", lambda *a, **kw: _FakeSession())
    client.post(
        "/api/connect",
        json={"gateway": "192.168.1.1", "username": "admin", "password": "pass"},
    )
    with client.session_transaction() as sess:
        assert sess.get("port") == 80


def test_gateway_base_url_standard_ports():
    """_gateway_base_url omits the port for standard HTTP/HTTPS ports."""
    assert app_module._gateway_base_url("http", "192.168.0.1", 80) == "http://192.168.0.1"
    assert app_module._gateway_base_url("https", "192.168.0.1", 443) == "https://192.168.0.1"


def test_gateway_base_url_alt_ports():
    """_gateway_base_url includes the port for non-standard ports."""
    assert app_module._gateway_base_url("http", "192.168.0.1", 8080) == "http://192.168.0.1:8080"
    assert app_module._gateway_base_url("https", "192.168.0.1", 8443) == "https://192.168.0.1:8443"


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


# ── /api/robocall/* ───────────────────────────────────────────────────────────

@pytest.fixture
def isolated_blocklist(tmp_path, monkeypatch):
    """Redirect blocklist I/O to a temporary file so tests don't touch blocklist.json."""
    bl_path = tmp_path / "blocklist.json"
    monkeypatch.setattr(app_module, "_BLOCKLIST_PATH", bl_path)
    yield bl_path


def test_robocall_list_empty(client, isolated_blocklist):
    resp = client.get("/api/robocall/list")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["entries"] == []


def test_robocall_block_adds_entry(client, isolated_blocklist):
    resp = client.post("/api/robocall/block", json={"cidr": "1.2.3.0/24", "label": "Spam Inc"})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert len(data["entries"]) == 1
    assert data["entries"][0]["cidr"] == "1.2.3.0/24"
    assert data["entries"][0]["label"] == "Spam Inc"


def test_robocall_block_bare_ip(client, isolated_blocklist):
    """A bare IP address should be normalised to a /32 CIDR."""
    resp = client.post("/api/robocall/block", json={"cidr": "5.6.7.8"})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["entries"][0]["cidr"] == "5.6.7.8/32"


def test_robocall_block_invalid_cidr(client, isolated_blocklist):
    resp = client.post("/api/robocall/block", json={"cidr": "not-an-ip"})
    assert resp.status_code == 400
    assert resp.get_json()["ok"] is False


def test_robocall_block_missing_cidr(client, isolated_blocklist):
    resp = client.post("/api/robocall/block", json={})
    assert resp.status_code == 400
    assert resp.get_json()["ok"] is False


def test_robocall_block_duplicate(client, isolated_blocklist):
    """Adding the same CIDR twice returns ok=True without adding a duplicate."""
    client.post("/api/robocall/block", json={"cidr": "9.9.9.0/24", "label": "First"})
    resp = client.post("/api/robocall/block", json={"cidr": "9.9.9.0/24", "label": "Second"})
    data = resp.get_json()
    assert data["ok"] is True
    assert len(data["entries"]) == 1


def test_robocall_unblock_removes_entry(client, isolated_blocklist):
    client.post("/api/robocall/block", json={"cidr": "10.0.0.0/8"})
    resp = client.post("/api/robocall/unblock", json={"cidr": "10.0.0.0/8"})
    data = resp.get_json()
    assert data["ok"] is True
    assert data["removed"] == 1
    assert data["entries"] == []


def test_robocall_unblock_nonexistent(client, isolated_blocklist):
    """Removing a CIDR not in the list returns ok=True with removed=0."""
    resp = client.post("/api/robocall/unblock", json={"cidr": "99.99.99.0/24"})
    data = resp.get_json()
    assert data["ok"] is True
    assert data["removed"] == 0


def test_robocall_push_requires_connection(client, isolated_blocklist):
    resp = client.post("/api/robocall/push", json={})
    assert resp.status_code == 401
    assert resp.get_json()["ok"] is False


def test_robocall_push_empty_blocklist(client, isolated_blocklist):
    """Push with an empty blocklist returns ok=True with an informative message."""
    with client.session_transaction() as sess:
        sess["gateway"] = "192.0.2.1"
        sess["username"] = "admin"
        sess["password"] = "pass"

    resp = client.post("/api/robocall/push", json={})
    data = resp.get_json()
    assert data["ok"] is True
    assert "message" in data


def test_robocall_push_with_entries(client, isolated_blocklist, monkeypatch):
    """Push calls the modem CGI for each blocklist entry and reports results."""
    client.post("/api/robocall/block", json={"cidr": "1.2.3.0/24", "label": "Spam Inc"})

    class _FakeResp:
        ok = True
        status_code = 200

    class _FakeSession:
        def post(self, *args, **kwargs):
            return _FakeResp()

    monkeypatch.setattr(app_module, "_modem_session", lambda *a, **kw: _FakeSession())

    with client.session_transaction() as sess:
        sess["gateway"] = "192.168.1.1"
        sess["username"] = "admin"
        sess["password"] = "pass"

    resp = client.post("/api/robocall/push", json={})
    data = resp.get_json()
    assert data["ok"] is True
    assert len(data["results"]) == 1
    assert data["results"][0]["status"] == "pushed"


# ── Agent toolkit ─────────────────────────────────────────────────────────────

def test_agent_registry_loads():
    agents = load_agents()
    assert len(agents) > 0
    assert "speedrouter-implementation-pilot" in agents
    assert "speedrouter-security-auditor" in agents


def test_profiles_load():
    profiles = load_profiles()
    assert "safe" in profiles
    assert "balanced" in profiles
    assert "power" in profiles


def test_find_agents_speedrouter():
    agents = load_agents()
    matches = list(find_agents(agents, "speedrouter"))
    ids = [a.id for a in matches]
    assert "speedrouter-orchestrator" in ids
    assert "speedrouter-vpn-specialist" in ids


def test_find_agents_no_match():
    agents = load_agents()
    matches = list(find_agents(agents, "zzz-no-match-zzz"))
    assert matches == []


def test_assess_agent_access_pass():
    agents = load_agents()
    profiles = load_profiles()
    report = assess_agent_access(agents["speedrouter-security-auditor"], profiles["safe"])
    assert report["pass"] is True
    assert report["missing_tools"] == []


def test_assess_agent_access_fail():
    agents = load_agents()
    profiles = load_profiles()
    # implementation pilot needs apply_patch/create_file which safe profile lacks
    report = assess_agent_access(agents["speedrouter-implementation-pilot"], profiles["safe"])
    assert report["pass"] is False
    assert "apply_patch" in report["missing_tools"]


def test_recommend_profile():
    agents = load_agents()
    profiles = load_profiles()
    profile = recommend_profile(agents["speedrouter-orchestrator"], profiles)
    assert profile.name == "power"


# ── CLI entry point ───────────────────────────────────────────────────────────

def test_main_exists():
    """main() must be importable and callable without errors (no-start test)."""
    from app import main
    assert callable(main)


def test_main_default_host_is_localhost(monkeypatch):
    """main() passes host=127.0.0.1 to app.run() when no env var is set."""
    import app as app_module

    monkeypatch.delenv("SPEEDROUTER_HOST", raising=False)
    monkeypatch.delenv("SPEEDROUTER_PORT", raising=False)

    captured = {}

    def fake_run(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(app_module.app, "run", fake_run)
    monkeypatch.setattr("sys.argv", ["speedrouter"])

    app_module.main()

    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 5000


def test_main_env_override(monkeypatch):
    """SPEEDROUTER_HOST and SPEEDROUTER_PORT env vars are passed to app.run()."""
    import app as app_module

    monkeypatch.setenv("SPEEDROUTER_HOST", "0.0.0.0")
    monkeypatch.setenv("SPEEDROUTER_PORT", "8080")

    captured = {}

    def fake_run(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(app_module.app, "run", fake_run)
    monkeypatch.setattr("sys.argv", ["speedrouter"])

    app_module.main()

    assert captured["host"] == "0.0.0.0"
    assert captured["port"] == 8080


def test_main_cli_args_override(monkeypatch):
    """--host and --port CLI flags are passed to app.run()."""
    import app as app_module

    monkeypatch.delenv("SPEEDROUTER_HOST", raising=False)
    monkeypatch.delenv("SPEEDROUTER_PORT", raising=False)

    captured = {}

    def fake_run(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(app_module.app, "run", fake_run)
    monkeypatch.setattr("sys.argv", ["speedrouter", "--host", "0.0.0.0", "--port", "9000"])

    app_module.main()

    assert captured["host"] == "0.0.0.0"
    assert captured["port"] == 9000

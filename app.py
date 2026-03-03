"""
speedRouter – modem management UI
Provides: modem login, security/performance optimiser, ISP-proofing,
          VPN configuration and internet speed test.
"""

import argparse
import ipaddress
import json
import os
import platform
import re
import secrets
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import requests
import speedtest
import urllib3
from flask import Flask, jsonify, render_template, request, session

# Suppress InsecureRequestWarning when connecting to routers that use self-signed
# HTTPS certificates on the LAN.  Users are connecting to their own devices.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)

# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _safe_ip(value: str) -> str:
    """Validate and return a canonical IP address string, raise ValueError otherwise."""
    return str(ipaddress.ip_address(value.strip()))


def _safe_network(value: str) -> str:
    """Validate and return a canonical network CIDR string, raise ValueError otherwise.

    Accepts both a bare IP (normalised to /32 or /128) and CIDR notation.
    strict=False allows host bits to be set (e.g. 192.168.1.5/24 → 192.168.1.0/24).
    """
    return str(ipaddress.ip_network(value.strip(), strict=False))


def _detect_gateway() -> str | None:
    """Return the default gateway IP string, or None if detection fails."""
    system = platform.system()
    try:
        if system == "Windows":
            out = subprocess.check_output(["ipconfig"], timeout=5, text=True, stderr=subprocess.DEVNULL)
            for line in out.splitlines():
                m = re.search(r"Default Gateway[.\s]+:\s*([\d.]+)", line)
                if m and m.group(1) != "0.0.0.0":
                    return m.group(1)
        elif system == "Darwin":
            out = subprocess.check_output(
                ["route", "-n", "get", "default"], timeout=5, text=True, stderr=subprocess.DEVNULL
            )
            for line in out.splitlines():
                m = re.search(r"gateway:\s*([\d.]+)", line)
                if m:
                    return m.group(1)
        else:
            # Linux
            out = subprocess.check_output(
                ["ip", "route", "show", "default"], timeout=5, text=True, stderr=subprocess.DEVNULL
            )
            m = re.search(r"default via ([\d.]+)", out)
            if m:
                return m.group(1)
    except Exception:  # noqa: BLE001
        pass
    return None


# ── Robocall blocklist ────────────────────────────────────────────────────────

_BLOCKLIST_PATH = Path(
    os.environ.get(
        "BLOCKLIST_PATH",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "blocklist.json"),
    )
)


def _load_blocklist() -> list[dict]:
    """Load the robocall blocklist from disk; return an empty list if absent/corrupt."""
    if _BLOCKLIST_PATH.exists():
        try:
            data = json.loads(_BLOCKLIST_PATH.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
        except (json.JSONDecodeError, OSError):
            pass
    return []


def _save_blocklist(entries: list[dict]) -> None:
    """Persist the blocklist to disk."""
    _BLOCKLIST_PATH.write_text(json.dumps(entries, indent=2), encoding="utf-8")


# ── Router login strategies ───────────────────────────────────────────────────

# CenturyLink C4000BZ preset – configurable via env vars or request params
_C4000BZ_LOGIN_URL = os.environ.get("ROUTER_LOGIN_URL", "")
_C4000BZ_USER_FIELD = os.environ.get("ROUTER_USER_FIELD", "username")
_C4000BZ_PASS_FIELD = os.environ.get("ROUTER_PASS_FIELD", "password")

# Known router presets: name → (login_path, user_field, pass_field)
ROUTER_PRESETS = {
    "centurylink_c4000bz": ("/login.cgi", "username", "password"),
    "generic_form": ("/login.cgi", "username", "password"),
    "generic_basic": None,  # HTTP Basic Auth only
}


def _modem_session(
    gateway: str,
    username: str,
    password: str,
    login_path: str | None = None,
    user_field: str = "username",
    pass_field: str = "password",
    scheme: str = "http",
) -> requests.Session:
    """Return an authenticated requests.Session for the modem admin panel.

    Tries form-based login first (using configurable endpoint/field names),
    then falls back to HTTP Basic Auth.  Pass scheme='https' for routers that
    serve their admin panel over HTTPS with a self-signed certificate;
    certificate verification is intentionally disabled for LAN-only devices.
    """
    s = requests.Session()
    if scheme == "https":
        s.verify = False  # router self-signed certs are expected on the LAN

    # Allow per-request or env-var override of login URL
    path = login_path or _C4000BZ_LOGIN_URL or "/login.cgi"
    login_url = f"{scheme}://{gateway}{path}"

    try:
        resp = s.post(
            login_url,
            data={user_field: username, pass_field: password},
            timeout=5,
            allow_redirects=True,
        )
        resp.raise_for_status()
    except requests.RequestException:
        # Fallback: HTTP Basic Auth (used by many modems)
        s.auth = (username, password)
    return s


# ──────────────────────────────────────────────────────────────────────────────
# Routes
# ──────────────────────────────────────────────────────────────────────────────

@app.after_request
def set_security_headers(response):
    """Apply security headers, including a Content-Security-Policy, to every response."""
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "font-src 'self'; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "form-action 'self'"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    return response


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/network/gateway")
def api_network_gateway():
    """Return the host machine's default gateway IP address."""
    gateway = _detect_gateway()
    if gateway:
        return jsonify({"ok": True, "gateway": gateway})
    return jsonify({"ok": False, "error": "Could not detect gateway."}), 200


@app.route("/api/status")
def api_status():
    """Return current modem connection state so the UI can restore itself after a page reload."""
    if "gateway" in session:
        return jsonify({"ok": True, "connected": True, "gateway": session["gateway"]})
    return jsonify({"ok": True, "connected": False})


@app.route("/api/connect", methods=["POST"])
def api_connect():
    """Authenticate with the modem admin panel and persist session info."""
    data = request.get_json(silent=True) or {}
    gateway = data.get("gateway", "").strip()
    username = data.get("username", "").strip()
    password = data.get("password", "")
    # Optional: router preset name (e.g. "centurylink_c4000bz")
    preset = data.get("preset", "").strip().lower()

    if not gateway or not username or not password:
        return jsonify({"ok": False, "error": "All fields are required."}), 400

    try:
        gateway = _safe_ip(gateway)
    except ValueError:
        return jsonify({"ok": False, "error": "Invalid gateway IP address."}), 400

    # Resolve login parameters from preset (if provided)
    login_path = None
    user_field = _C4000BZ_USER_FIELD
    pass_field = _C4000BZ_PASS_FIELD
    if preset and preset in ROUTER_PRESETS:
        preset_cfg = ROUTER_PRESETS[preset]
        if preset_cfg is not None:
            login_path, user_field, pass_field = preset_cfg

    # Probe the modem: try HTTP first, then HTTPS.  Many modern routers serve
    # their admin panel exclusively over HTTPS with a self-signed certificate;
    # any HTTP response (including 401/403) confirms the modem is reachable.
    scheme = "http"
    for try_scheme in ("http", "https"):
        try:
            s = _modem_session(gateway, username, password, login_path=login_path,
                               user_field=user_field, pass_field=pass_field,
                               scheme=try_scheme)
            _ = s.get(f"{try_scheme}://{gateway}/", timeout=5)
            scheme = try_scheme
            break  # modem reachable on this scheme
        except requests.RequestException as exc:
            if try_scheme == "https":
                # Both HTTP and HTTPS failed
                if isinstance(exc, requests.Timeout):
                    return jsonify({"ok": False, "error": "Modem timed out."}), 504
                return jsonify({"ok": False, "error": "Cannot reach modem. Check the IP address."}), 502
            # HTTP failed (any reason) — try HTTPS next

    session["gateway"] = gateway
    session["username"] = username
    session["password"] = password
    session["scheme"] = scheme
    return jsonify({"ok": True, "gateway": gateway})


@app.route("/api/optimize", methods=["POST"])
def api_optimize():
    """
    Apply recommended security and performance settings.

    Settings applied (where the modem exposes a CGI endpoint):
      - DNS → Cloudflare 1.1.1.1 / Google 8.8.8.8
      - Disable TR-069 / CWMP (ISP remote management)
      - Enable firewall / SPI
      - Disable UPnP
      - Set MTU to 1500
      - Disable WPS (Wi-Fi Protected Setup)
    """
    if "gateway" not in session:
        return jsonify({"ok": False, "error": "Not connected to a modem."}), 401

    gateway = session["gateway"]
    username = session["username"]
    password = session["password"]
    scheme = session.get("scheme", "http")

    results = []

    try:
        s = _modem_session(gateway, username, password, scheme=scheme)

        settings = [
            # (description, endpoint, payload)
            (
                "DNS set to 1.1.1.1 / 8.8.8.8",
                f"{scheme}://{gateway}/wan_dns.cgi",
                {"dns1": "1.1.1.1", "dns2": "8.8.8.8"},
            ),
            (
                "TR-069 / CWMP (ISP remote management) disabled",
                f"{scheme}://{gateway}/cwmp.cgi",
                {"cwmp_enable": "0"},
            ),
            (
                "Firewall / SPI enabled",
                f"{scheme}://{gateway}/firewall.cgi",
                {"firewall_enable": "1", "spi_enable": "1"},
            ),
            (
                "UPnP disabled",
                f"{scheme}://{gateway}/upnp.cgi",
                {"upnp_enable": "0"},
            ),
            (
                "MTU set to 1500",
                f"{scheme}://{gateway}/wan_mtu.cgi",
                {"mtu": "1500"},
            ),
            (
                "WPS disabled",
                f"{scheme}://{gateway}/wps.cgi",
                {"wps_enable": "0"},
            ),
        ]

        for description, url, payload in settings:
            try:
                resp = s.post(url, data=payload, timeout=5)
                status = "applied" if resp.ok else f"skipped (HTTP {resp.status_code})"
            except requests.RequestException:
                status = "skipped (endpoint not available on this modem)"
            results.append({"setting": description, "status": status})

    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500

    return jsonify({"ok": True, "results": results})


@app.route("/api/speedtest", methods=["POST"])
def api_speedtest():
    """Run an internet speed test and return download/upload/ping."""
    try:
        st = speedtest.Speedtest()
        st.get_best_server()
        download_bps = st.download()
        upload_bps = st.upload()
        ping_ms = st.results.ping

        return jsonify(
            {
                "ok": True,
                "download_mbps": round(download_bps / 1_000_000, 2),
                "upload_mbps": round(upload_bps / 1_000_000, 2),
                "ping_ms": round(ping_ms, 1),
                "server": st.results.server.get("host", "unknown"),
            }
        )
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/vpn/config", methods=["POST"])
def api_vpn_config():
    """
    Push a WireGuard VPN configuration to the modem.

    Expects JSON body:
      endpoint     – VPN server endpoint (IP:port)
      public_key   – WireGuard server public key
      private_key  – WireGuard client private key
      allowed_ips  – comma-separated CIDRs (default: 0.0.0.0/0)
      dns          – VPN DNS server (default: 1.1.1.1)
    """
    if "gateway" not in session:
        return jsonify({"ok": False, "error": "Not connected to a modem."}), 401

    gateway = session["gateway"]
    username = session["username"]
    password = session["password"]
    scheme = session.get("scheme", "http")

    data = request.get_json(silent=True) or {}
    endpoint = data.get("endpoint", "").strip()
    public_key = data.get("public_key", "").strip()
    private_key = data.get("private_key", "").strip()
    allowed_ips = data.get("allowed_ips", "0.0.0.0/0").strip()
    dns = data.get("dns", "1.1.1.1").strip()

    if not endpoint or not public_key or not private_key:
        return jsonify({"ok": False, "error": "endpoint, public_key and private_key are required."}), 400

    try:
        s = _modem_session(gateway, username, password, scheme=scheme)
        resp = s.post(
            f"{scheme}://{gateway}/vpn_wireguard.cgi",
            data={
                "wg_enable": "1",
                "wg_endpoint": endpoint,
                "wg_pubkey": public_key,
                "wg_privkey": private_key,
                "wg_allowed_ips": allowed_ips,
                "wg_dns": dns,
            },
            timeout=10,
        )
        if resp.ok:
            return jsonify({"ok": True, "message": "WireGuard VPN configuration pushed to modem."})
        return jsonify(
            {"ok": False, "error": f"Modem returned HTTP {resp.status_code}. "
             "Your modem may not support WireGuard via this endpoint."}
        ), 502
    except requests.RequestException as exc:
        return jsonify(
            {"ok": False, "error": f"Could not reach modem VPN endpoint: {exc}"}
        ), 502


@app.route("/api/robocall/list")
def api_robocall_list():
    """Return the current robocall blocklist."""
    return jsonify({"ok": True, "entries": _load_blocklist()})


@app.route("/api/robocall/block", methods=["POST"])
def api_robocall_block():
    """Add an IP address or CIDR block to the robocall blocklist."""
    data = request.get_json(silent=True) or {}
    label = data.get("label", "").strip()
    cidr_raw = data.get("cidr", "").strip()

    if not cidr_raw:
        return jsonify({"ok": False, "error": "cidr is required."}), 400

    try:
        cidr = _safe_network(cidr_raw)
    except ValueError:
        return jsonify({"ok": False, "error": "Invalid IP address or CIDR."}), 400

    entries = _load_blocklist()
    if any(e["cidr"] == cidr for e in entries):
        return jsonify({"ok": True, "message": f"{cidr} is already in the blocklist.", "entries": entries})

    entries.append({
        "label": label or cidr,
        "cidr": cidr,
        "added": datetime.now(tz=timezone.utc).strftime("%Y-%m-%d"),
    })
    _save_blocklist(entries)
    return jsonify({"ok": True, "entries": entries})


@app.route("/api/robocall/unblock", methods=["POST"])
def api_robocall_unblock():
    """Remove an IP address or CIDR block from the robocall blocklist."""
    data = request.get_json(silent=True) or {}
    cidr_raw = data.get("cidr", "").strip()

    if not cidr_raw:
        return jsonify({"ok": False, "error": "cidr is required."}), 400

    try:
        cidr = _safe_network(cidr_raw)
    except ValueError:
        return jsonify({"ok": False, "error": "Invalid IP address or CIDR."}), 400

    entries = _load_blocklist()
    before = len(entries)
    entries = [e for e in entries if e["cidr"] != cidr]
    _save_blocklist(entries)
    return jsonify({"ok": True, "removed": before - len(entries), "entries": entries})


@app.route("/api/robocall/push", methods=["POST"])
def api_robocall_push():
    """Push the full blocklist as firewall rules to the connected modem."""
    if "gateway" not in session:
        return jsonify({"ok": False, "error": "Not connected to a modem."}), 401

    gateway = session["gateway"]
    username = session["username"]
    password = session["password"]
    scheme = session.get("scheme", "http")

    entries = _load_blocklist()
    if not entries:
        return jsonify({"ok": True, "results": [], "message": "Blocklist is empty — nothing to push."})

    results = []
    try:
        s = _modem_session(gateway, username, password, scheme=scheme)
        for entry in entries:
            network = ipaddress.ip_network(entry["cidr"])
            try:
                resp = s.post(
                    f"{scheme}://{gateway}/firewall_block.cgi",
                    data={
                        "ip_block": str(network.network_address),
                        "ip_mask": str(network.netmask),
                        "action": "block",
                    },
                    timeout=5,
                )
                status = "pushed" if resp.ok else f"skipped (HTTP {resp.status_code})"
            except requests.RequestException:
                status = "skipped (endpoint not available on this modem)"
            results.append({"entry": entry["label"], "cidr": entry["cidr"], "status": status})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500

    return jsonify({"ok": True, "results": results})


@app.route("/api/disconnect", methods=["POST"])
def api_disconnect():
    """Clear the modem session."""
    session.clear()
    return jsonify({"ok": True})


def main():
    """CLI entry point: start the speedRouter web UI."""
    parser = argparse.ArgumentParser(
        prog="speedrouter",
        description="speedRouter – modem management UI (connect, optimise, VPN, speed test)",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("SPEEDROUTER_HOST", "127.0.0.1"),
        help="Host address to bind (default: 127.0.0.1; use 0.0.0.0 to expose on the network)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("SPEEDROUTER_PORT", "5000")),
        help="Port to listen on (default: 5000)",
    )
    args = parser.parse_args()
    app.run(debug=False, host=args.host, port=args.port)


if __name__ == "__main__":
    main()

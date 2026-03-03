"""
speedRouter – modem management UI
Provides: modem login, security/performance optimiser, ISP-proofing,
          VPN configuration and internet speed test.
"""

import argparse
import ipaddress
import os
import platform
import re
import secrets
import subprocess

import requests
import speedtest
from flask import Flask, jsonify, render_template, request, session

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)

# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _safe_ip(value: str) -> str:
    """Validate and return a canonical IP address string, raise ValueError otherwise."""
    return str(ipaddress.ip_address(value.strip()))


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
) -> requests.Session:
    """Return an authenticated requests.Session for the modem admin panel.

    Tries form-based login first (using configurable endpoint/field names),
    then falls back to HTTP Basic Auth.
    """
    s = requests.Session()

    # Allow per-request or env-var override of login URL
    path = login_path or _C4000BZ_LOGIN_URL or "/login.cgi"
    login_url = f"http://{gateway}{path}"

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

    try:
        s = _modem_session(gateway, username, password, login_path=login_path,
                           user_field=user_field, pass_field=pass_field)
        # Quick reachability probe
        probe = s.get(f"http://{gateway}/", timeout=5)
        probe.raise_for_status()
    except requests.ConnectionError:
        return jsonify({"ok": False, "error": "Cannot reach modem. Check the IP address."}), 502
    except requests.Timeout:
        return jsonify({"ok": False, "error": "Modem timed out."}), 504
    except requests.RequestException as exc:
        return jsonify({"ok": False, "error": str(exc)}), 502

    session["gateway"] = gateway
    session["username"] = username
    session["password"] = password
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

    results = []

    try:
        s = _modem_session(gateway, username, password)

        settings = [
            # (description, endpoint, payload)
            (
                "DNS set to 1.1.1.1 / 8.8.8.8",
                f"http://{gateway}/wan_dns.cgi",
                {"dns1": "1.1.1.1", "dns2": "8.8.8.8"},
            ),
            (
                "TR-069 / CWMP (ISP remote management) disabled",
                f"http://{gateway}/cwmp.cgi",
                {"cwmp_enable": "0"},
            ),
            (
                "Firewall / SPI enabled",
                f"http://{gateway}/firewall.cgi",
                {"firewall_enable": "1", "spi_enable": "1"},
            ),
            (
                "UPnP disabled",
                f"http://{gateway}/upnp.cgi",
                {"upnp_enable": "0"},
            ),
            (
                "MTU set to 1500",
                f"http://{gateway}/wan_mtu.cgi",
                {"mtu": "1500"},
            ),
            (
                "WPS disabled",
                f"http://{gateway}/wps.cgi",
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

    data = request.get_json(silent=True) or {}
    endpoint = data.get("endpoint", "").strip()
    public_key = data.get("public_key", "").strip()
    private_key = data.get("private_key", "").strip()
    allowed_ips = data.get("allowed_ips", "0.0.0.0/0").strip()
    dns = data.get("dns", "1.1.1.1").strip()

    if not endpoint or not public_key or not private_key:
        return jsonify({"ok": False, "error": "endpoint, public_key and private_key are required."}), 400

    try:
        s = _modem_session(gateway, username, password)
        resp = s.post(
            f"http://{gateway}/vpn_wireguard.cgi",
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

"""
speedRouter – modem management UI
Provides: modem login, security/performance optimiser, ISP-proofing,
          VPN configuration and internet speed test.
"""

import argparse
import base64
import ipaddress
import json
import os
import platform
import re
import secrets
import socket
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import requests
import speedtest
import urllib3
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)
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


# ── Built-in WireGuard VPN server ────────────────────────────────────────────

_VPN_SERVER_PATH = Path(
    os.environ.get(
        "VPN_SERVER_PATH",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "vpn_server.json"),
    )
)

_VPN_PEERS_PATH = Path(
    os.environ.get(
        "VPN_PEERS_PATH",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "vpn_peers.json"),
    )
)


def _wg_generate_keypair() -> tuple[str, str]:
    """Generate a WireGuard-compatible Curve25519 key pair.

    Returns (private_key_b64, public_key_b64) as base64 strings compatible
    with the WireGuard configuration format.
    """
    priv = X25519PrivateKey.generate()
    priv_bytes = priv.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    pub_bytes = priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return base64.b64encode(priv_bytes).decode(), base64.b64encode(pub_bytes).decode()


def _load_vpn_server() -> dict:
    """Load the VPN server configuration from disk; return {} if absent/corrupt."""
    if _VPN_SERVER_PATH.exists():
        try:
            data = json.loads(_VPN_SERVER_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_vpn_server(data: dict) -> None:
    """Persist the VPN server configuration to disk."""
    _VPN_SERVER_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _load_vpn_peers() -> list[dict]:
    """Load the VPN peer list from disk; return [] if absent/corrupt."""
    if _VPN_PEERS_PATH.exists():
        try:
            data = json.loads(_VPN_PEERS_PATH.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
        except (json.JSONDecodeError, OSError):
            pass
    return []


def _save_vpn_peers(peers: list[dict]) -> None:
    """Persist the VPN peer list to disk."""
    _VPN_PEERS_PATH.write_text(json.dumps(peers, indent=2), encoding="utf-8")


def _build_server_wg_config(server: dict, peers: list[dict]) -> str:
    """Return a wg0.conf-compatible string for the VPN server."""
    subnet = ipaddress.ip_network(server["subnet"], strict=False)
    server_addr = str(next(iter(subnet.hosts())))
    lines = [
        "[Interface]",
        f"PrivateKey = {server['private_key']}",
        f"Address = {server_addr}/{subnet.prefixlen}",
        f"ListenPort = {server.get('port', 51820)}",
        "",
    ]
    for peer in peers:
        lines += [
            "[Peer]",
            f"PublicKey = {peer['public_key']}",
            f"AllowedIPs = {peer['address']}/32",
            "",
        ]
    return "\n".join(lines)


def _build_peer_wg_config(server: dict, peer: dict) -> str:
    """Return a wg-client.conf-compatible string for a VPN peer."""
    endpoint = server.get("endpoint") or f"<YOUR_SERVER_IP>:{server.get('port', 51820)}"
    lines = [
        "[Interface]",
        f"PrivateKey = {peer['private_key']}",
        f"Address = {peer['address']}/32",
        f"DNS = {server.get('dns', '1.1.1.1')}",
        "",
        "[Peer]",
        f"PublicKey = {server['public_key']}",
        f"Endpoint = {endpoint}",
        "AllowedIPs = 0.0.0.0/0",
        "PersistentKeepalive = 25",
        "",
    ]
    return "\n".join(lines)


# ── Router login strategies ───────────────────────────────────────────────────

# CenturyLink C4000BZ preset – configurable via env vars or request params
_C4000BZ_LOGIN_URL = os.environ.get("ROUTER_LOGIN_URL", "")
_C4000BZ_USER_FIELD = os.environ.get("ROUTER_USER_FIELD", "username")
_C4000BZ_PASS_FIELD = os.environ.get("ROUTER_PASS_FIELD", "password")

# Known router presets: name → (login_path, user_field, pass_field)
# None value means HTTP Basic Auth only (no form POST login).
# "auto" is handled specially in api_connect: it triggers the default
# auto-detect behaviour (try form login, fall back to Basic Auth).
ROUTER_PRESETS = {
    "auto": None,                # See api_connect – auto-detect login method
    "centurylink_c4000bz": ("/login.cgi", "username", "password"),
    "netgear": ("/index.htm", "username", "password"),
    "asus": ("/login.cgi", "group_id", "passwd"),
    "tp_link": ("/cgi-bin/luci/rpc/auth", "username", "password"),
    "arris_surfboard": ("/goform/login", "loginUsername", "loginPassword"),
    "motorola": ("/login.asp", "loginUsername", "loginPassword"),
    "generic_form": ("/login.cgi", "username", "password"),
    "generic_basic": None,  # HTTP Basic Auth only
}


def _gateway_base_url(scheme: str, gateway: str, port: int) -> str:
    """Return the base URL for the gateway, omitting the port when it is the scheme default."""
    standard = {"http": 80, "https": 443}
    if port == standard.get(scheme):
        return f"{scheme}://{gateway}"
    return f"{scheme}://{gateway}:{port}"


# Ordered list of (scheme, port) combinations to probe when connecting.
# Many consumer routers serve their admin panel on 8080 or 8443.
_PROBE_SEQUENCE = [("http", 80), ("http", 8080), ("https", 443), ("https", 8443)]


def _modem_session(
    gateway: str,
    username: str,
    password: str,
    login_path: str | None = None,
    user_field: str = "username",
    pass_field: str = "password",
    scheme: str = "http",
    port: int | None = None,
) -> requests.Session:
    """Return an authenticated requests.Session for the modem admin panel.

    Tries form-based login first (using configurable endpoint/field names),
    then falls back to HTTP Basic Auth.  Pass scheme='https' for routers that
    serve their admin panel over HTTPS with a self-signed certificate;
    certificate verification is intentionally disabled for LAN-only devices.
    """
    if port is None:
        port = 443 if scheme == "https" else 80

    s = requests.Session()
    if scheme == "https":
        s.verify = False  # router self-signed certs are expected on the LAN

    # Allow per-request or env-var override of login URL
    path = login_path or _C4000BZ_LOGIN_URL or "/login.cgi"
    login_url = _gateway_base_url(scheme, gateway, port) + path

    try:
        resp = s.post(
            login_url,
            data={user_field: username, pass_field: password},
            timeout=3,
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


@app.route("/api/network/internet")
def api_network_internet():
    """Check whether the host running speedRouter has internet access.

    Uses a lightweight TCP handshake to Cloudflare's public DNS (1.1.1.1:53)
    and Google's (8.8.8.8:53) as a fallback.  No DNS lookup is performed, so
    this works even when DNS is broken.  Returns {"ok": true, "online": true/false}.
    """
    for host, port in [("1.1.1.1", 53), ("8.8.8.8", 53)]:
        try:
            sock = socket.create_connection((host, port), timeout=3)
            sock.close()
            return jsonify({"ok": True, "online": True})
        except OSError:
            continue
    return jsonify({"ok": True, "online": False})


@app.route("/api/status")
def api_status():
    """Return current modem connection state so the UI can restore itself after a page reload."""
    if "gateway" in session:
        return jsonify({
            "ok": True,
            "connected": True,
            "gateway": session["gateway"],
            "username": session.get("username", ""),
            "preset": session.get("preset", ""),
        })
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

    # Resolve login parameters from preset (if provided).
    # "auto" and unknown presets use the default auto-detect behaviour.
    login_path = None
    user_field = _C4000BZ_USER_FIELD
    pass_field = _C4000BZ_PASS_FIELD
    if preset and preset in ROUTER_PRESETS and preset != "auto":
        preset_cfg = ROUTER_PRESETS[preset]
        if preset_cfg is not None:
            login_path, user_field, pass_field = preset_cfg

    # Probe the modem across common (scheme, port) combinations used by consumer
    # routers.  Any HTTP response (including 401/403) confirms reachability.
    # The probe is best-effort: when speedRouter runs on a cloud host it cannot
    # reach a private LAN modem (192.168.x.x), so all probes will fail.  In
    # that case we still store the credentials with sensible defaults so the
    # user can open the UI — modem operations will report an error if the host
    # truly cannot reach the device.
    scheme = "http"
    port = 80
    verified = False
    for try_scheme, try_port in _PROBE_SEQUENCE:
        try:
            s = _modem_session(gateway, username, password, login_path=login_path,
                               user_field=user_field, pass_field=pass_field,
                               scheme=try_scheme, port=try_port)
            probe_url = _gateway_base_url(try_scheme, gateway, try_port) + "/"
            _ = s.get(probe_url, timeout=2)
            scheme = try_scheme
            port = try_port
            verified = True
            break  # modem reachable on this scheme/port
        except requests.RequestException:
            continue

    session["gateway"] = gateway
    session["username"] = username
    session["password"] = password
    session["scheme"] = scheme
    session["port"] = port
    if preset:
        session["preset"] = preset
    return jsonify({"ok": True, "gateway": gateway, "verified": verified})


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
        return jsonify({"ok": False, "error": "Not connected to a modem."}), 403

    gateway = session["gateway"]
    username = session["username"]
    password = session["password"]
    scheme = session.get("scheme", "http")
    port = session.get("port", 443 if scheme == "https" else 80)

    results = []

    try:
        s = _modem_session(gateway, username, password, scheme=scheme, port=port)
        base = _gateway_base_url(scheme, gateway, port)

        settings = [
            # (description, endpoint, payload)
            (
                "DNS set to 1.1.1.1 / 8.8.8.8",
                f"{base}/wan_dns.cgi",
                {"dns1": "1.1.1.1", "dns2": "8.8.8.8"},
            ),
            (
                "TR-069 / CWMP (ISP remote management) disabled",
                f"{base}/cwmp.cgi",
                {"cwmp_enable": "0"},
            ),
            (
                "Firewall / SPI enabled",
                f"{base}/firewall.cgi",
                {"firewall_enable": "1", "spi_enable": "1"},
            ),
            (
                "UPnP disabled",
                f"{base}/upnp.cgi",
                {"upnp_enable": "0"},
            ),
            (
                "MTU set to 1500",
                f"{base}/wan_mtu.cgi",
                {"mtu": "1500"},
            ),
            (
                "WPS disabled",
                f"{base}/wps.cgi",
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
        return jsonify({"ok": False, "error": "Not connected to a modem."}), 403

    gateway = session["gateway"]
    username = session["username"]
    password = session["password"]
    scheme = session.get("scheme", "http")
    port = session.get("port", 443 if scheme == "https" else 80)

    data = request.get_json(silent=True) or {}
    endpoint = data.get("endpoint", "").strip()
    public_key = data.get("public_key", "").strip()
    private_key = data.get("private_key", "").strip()
    allowed_ips = data.get("allowed_ips", "0.0.0.0/0").strip()
    dns = data.get("dns", "1.1.1.1").strip()

    if not endpoint or not public_key or not private_key:
        return jsonify({"ok": False, "error": "endpoint, public_key and private_key are required."}), 400

    try:
        s = _modem_session(gateway, username, password, scheme=scheme, port=port)
        base = _gateway_base_url(scheme, gateway, port)
        resp = s.post(
            f"{base}/vpn_wireguard.cgi",
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
        return jsonify({"ok": False, "error": "Not connected to a modem."}), 403

    gateway = session["gateway"]
    username = session["username"]
    password = session["password"]
    scheme = session.get("scheme", "http")
    port = session.get("port", 443 if scheme == "https" else 80)

    entries = _load_blocklist()
    if not entries:
        return jsonify({"ok": True, "results": [], "message": "Blocklist is empty — nothing to push."})

    results = []
    try:
        s = _modem_session(gateway, username, password, scheme=scheme, port=port)
        base = _gateway_base_url(scheme, gateway, port)
        for entry in entries:
            network = ipaddress.ip_network(entry["cidr"])
            try:
                resp = s.post(
                    f"{base}/firewall_block.cgi",
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


# ── Built-in VPN server routes ────────────────────────────────────────────────

@app.route("/api/vpn/keygen")
def api_vpn_keygen():
    """Generate a fresh WireGuard key pair without persisting anything."""
    private_key, public_key = _wg_generate_keypair()
    return jsonify({"ok": True, "private_key": private_key, "public_key": public_key})


@app.route("/api/vpn/server/status")
def api_vpn_server_status():
    """Return VPN server initialisation state and (where possible) running state."""
    server = _load_vpn_server()
    if not server:
        return jsonify({"ok": True, "initialized": False})
    running = False
    try:
        result = subprocess.run(
            ["wg", "show", "wg0"], capture_output=True, timeout=5
        )
        running = result.returncode == 0
    except (FileNotFoundError, subprocess.SubprocessError):
        pass
    return jsonify({
        "ok": True,
        "initialized": True,
        "public_key": server["public_key"],
        "port": server.get("port", 51820),
        "subnet": server.get("subnet", "10.8.0.0/24"),
        "endpoint": server.get("endpoint", ""),
        "peer_count": len(_load_vpn_peers()),
        "running": running,
    })


@app.route("/api/vpn/server/init", methods=["POST"])
def api_vpn_server_init():
    """Initialise (or re-key) the VPN server — generates a fresh Curve25519 key pair."""
    data = request.get_json(silent=True) or {}
    try:
        port = int(data.get("port", 51820))
        if not (1 <= port <= 65535):
            raise ValueError
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "port must be an integer between 1 and 65535."}), 400
    subnet_raw = data.get("subnet", "10.8.0.0/24").strip()
    try:
        subnet_str = str(ipaddress.ip_network(subnet_raw, strict=False))
    except ValueError:
        return jsonify({"ok": False, "error": "Invalid subnet CIDR."}), 400
    endpoint = data.get("endpoint", "").strip()
    dns_raw = data.get("dns", "1.1.1.1").strip()
    try:
        dns = _safe_ip(dns_raw)
    except ValueError:
        return jsonify({"ok": False, "error": "Invalid DNS IP address."}), 400
    private_key, public_key = _wg_generate_keypair()
    server = {
        "private_key": private_key,
        "public_key": public_key,
        "port": port,
        "subnet": subnet_str,
        "endpoint": endpoint,
        "dns": dns,
    }
    _save_vpn_server(server)
    return jsonify({"ok": True, "public_key": public_key, "port": port, "subnet": subnet_str})


@app.route("/api/vpn/server/config")
def api_vpn_server_config():
    """Return the WireGuard server configuration file content (wg0.conf)."""
    server = _load_vpn_server()
    if not server:
        return jsonify({"ok": False, "error": "VPN server not initialised."}), 400
    peers = _load_vpn_peers()
    return jsonify({"ok": True, "config": _build_server_wg_config(server, peers)})


@app.route("/api/vpn/server/apply", methods=["POST"])
def api_vpn_server_apply():
    """Write wg0.conf and bring up the interface (Linux with WireGuard + root required)."""
    server = _load_vpn_server()
    if not server:
        return jsonify({"ok": False, "error": "VPN server not initialised."}), 400
    peers = _load_vpn_peers()
    config = _build_server_wg_config(server, peers)
    wg_conf = Path("/etc/wireguard/wg0.conf")
    try:
        wg_conf.write_text(config, encoding="utf-8")
        subprocess.run(["wg-quick", "up", "wg0"], check=True, timeout=30, capture_output=True)
        return jsonify({"ok": True, "message": "WireGuard VPN server started on wg0."})
    except PermissionError:
        return jsonify({"ok": False, "error": "Permission denied — run speedRouter as root or with sudo."}), 403
    except FileNotFoundError:
        return jsonify({"ok": False, "error": "WireGuard (wg-quick) is not installed on this system."}), 501
    except subprocess.CalledProcessError as exc:
        return jsonify({"ok": False, "error": f"wg-quick failed: {exc.stderr.decode()[:300]}"}), 500


@app.route("/api/vpn/peers")
def api_vpn_peers_list():
    """Return VPN peer list (public keys only — private keys are never exposed here)."""
    peers = _load_vpn_peers()
    safe = [
        {
            "id": p["id"],
            "name": p["name"],
            "public_key": p["public_key"],
            "address": p["address"],
            "added": p.get("added", ""),
        }
        for p in peers
    ]
    return jsonify({"ok": True, "peers": safe})


@app.route("/api/vpn/peers/add", methods=["POST"])
def api_vpn_peers_add():
    """Create a new peer with auto-generated keys and the next available tunnel IP."""
    server = _load_vpn_server()
    if not server:
        return jsonify({"ok": False, "error": "Initialise the VPN server first."}), 400
    data = request.get_json(silent=True) or {}
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"ok": False, "error": "Peer name is required."}), 400
    peers = _load_vpn_peers()
    subnet = ipaddress.ip_network(server["subnet"], strict=False)
    hosts = list(subnet.hosts())
    server_ip = str(hosts[0])  # server always takes the first host
    used = {server_ip} | {p["address"] for p in peers}
    next_ip = next((str(h) for h in hosts if str(h) not in used), None)
    if not next_ip:
        return jsonify({"ok": False, "error": "No IP addresses available in subnet."}), 400
    private_key, public_key = _wg_generate_keypair()
    peer = {
        "id": secrets.token_hex(8),
        "name": name,
        "private_key": private_key,
        "public_key": public_key,
        "address": next_ip,
        "added": datetime.now(tz=timezone.utc).strftime("%Y-%m-%d"),
    }
    peers.append(peer)
    _save_vpn_peers(peers)
    return jsonify({
        "ok": True,
        "peer": {
            "id": peer["id"],
            "name": peer["name"],
            "public_key": peer["public_key"],
            "address": peer["address"],
        },
    })


@app.route("/api/vpn/peers/remove", methods=["POST"])
def api_vpn_peers_remove():
    """Remove a peer by its ID."""
    data = request.get_json(silent=True) or {}
    peer_id = data.get("id", "").strip()
    if not peer_id:
        return jsonify({"ok": False, "error": "Peer ID is required."}), 400
    peers = _load_vpn_peers()
    before = len(peers)
    peers = [p for p in peers if p["id"] != peer_id]
    _save_vpn_peers(peers)
    return jsonify({"ok": True, "removed": before - len(peers)})


@app.route("/api/vpn/peers/<peer_id>/config")
def api_vpn_peer_config(peer_id: str):
    """Return the complete WireGuard client config for a peer (includes private key)."""
    server = _load_vpn_server()
    if not server:
        return jsonify({"ok": False, "error": "VPN server not initialised."}), 400
    peers = _load_vpn_peers()
    peer = next((p for p in peers if p["id"] == peer_id), None)
    if not peer:
        return jsonify({"ok": False, "error": "Peer not found."}), 404
    return jsonify({
        "ok": True,
        "config": _build_peer_wg_config(server, peer),
        "name": peer["name"],
    })


@app.route("/api/dsl/status")
def api_dsl_status():
    """Fetch DSL line statistics directly from the modem admin API.

    Tries common JSON API paths used by the CenturyLink C4000BZ and generic
    DSL modems.  Returns the raw JSON payload from the first path that answers
    with a parseable JSON body, so the frontend can display whatever the modem
    provides without any model-specific parsing on the server side.
    """
    if "gateway" not in session:
        return jsonify({"ok": False, "error": "Not connected to a modem."}), 403

    gateway = session["gateway"]
    username = session["username"]
    password = session["password"]
    scheme = session.get("scheme", "http")
    port = session.get("port", 443 if scheme == "https" else 80)

    # Ordered list of DSL status paths to probe (most-specific first).
    dsl_status_paths = [
        "/api/v1/modem/dsl",          # Zyxel / CenturyLink C4000BZ REST API
        "/dsl_status.cgi",            # Generic CGI
        "/cgi-bin/status_dsl.cgi",    # Alternative generic CGI path
    ]

    try:
        s = _modem_session(gateway, username, password, scheme=scheme, port=port)
        base = _gateway_base_url(scheme, gateway, port)
        for path in dsl_status_paths:
            try:
                resp = s.get(f"{base}{path}", timeout=5)
                if resp.ok:
                    try:
                        data = resp.json()
                        return jsonify({"ok": True, "data": data})
                    except ValueError:
                        continue  # not JSON – try the next path
            except requests.RequestException:
                continue
    except Exception:  # noqa: BLE001
        return jsonify({"ok": False, "error": "Unexpected error reading DSL status."}), 500

    return jsonify({
        "ok": False,
        "error": (
            "DSL status endpoint not available on this modem. "
            "Check the modem admin panel directly for DSL line stats."
        ),
    }), 502


@app.route("/api/dsl/retrain", methods=["POST"])
def api_dsl_retrain():
    """Trigger a DSL line retrain on the modem.

    Tries common retrain / restart CGI and REST endpoints.  A retrain drops
    both DSL lines and lets them re-negotiate sync rates — the modem will be
    offline for 30–120 seconds.  No ISP involvement is required; this is
    equivalent to power-cycling the modem but preserves all settings.
    """
    if "gateway" not in session:
        return jsonify({"ok": False, "error": "Not connected to a modem."}), 403

    gateway = session["gateway"]
    username = session["username"]
    password = session["password"]
    scheme = session.get("scheme", "http")
    port = session.get("port", 443 if scheme == "https" else 80)

    # Ordered list of retrain endpoints to try.
    retrain_attempts = [
        # (method, path, payload)
        ("POST", "/api/v1/modem/restart", {"type": "dsl"}),     # C4000BZ REST API
        ("POST", "/dsl_retrain.cgi",      {"action": "retrain"}),  # Generic CGI
        ("POST", "/reboot.cgi",           {"action": "dsl_retrain"}),  # Fallback CGI
    ]

    try:
        s = _modem_session(gateway, username, password, scheme=scheme, port=port)
        base = _gateway_base_url(scheme, gateway, port)
        for method, path, payload in retrain_attempts:
            try:
                resp = s.request(method, f"{base}{path}", json=payload, timeout=5)
                if resp.ok:
                    return jsonify({
                        "ok": True,
                        "message": (
                            "DSL retrain initiated. Both lines will drop and re-sync "
                            "(expect 30–120 s of downtime). Check DSL stats again "
                            "after the modem reconnects."
                        ),
                    })
            except requests.RequestException:
                continue
    except Exception:  # noqa: BLE001
        return jsonify({"ok": False, "error": "Unexpected error sending retrain command."}), 500

    return jsonify({
        "ok": False,
        "error": (
            "Retrain endpoint not available on this modem. "
            "You can retrain lines manually by power-cycling the modem."
        ),
    }), 502


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

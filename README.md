# speedRouter

A UI that can log into your modem and fix the settings for best connection and security — without letting your ISP brick your router. VPN and speed tester included.

## Features

- 🔌 **Modem login** — connect to any router admin panel (form-based or HTTP Basic Auth)
- 🔒 **One-click optimiser** — DNS, firewall, MTU, UPnP, WPS, TR-069/CWMP
- 📶 **Speed test** — real download/upload/ping via Speedtest.net
- 🔐 **WireGuard VPN** — push a VPN config directly to your modem

---

## Run directly (Python)

```bash
pip install -r requirements.txt
python app.py
# → http://127.0.0.1:5000
```

### Expose on the local network (side-load accessible)

```bash
python app.py --host 0.0.0.0 --port 5000
# → http://<your-machine-ip>:5000  (reachable from other devices on the same network)
```

Environment variables work too:

```bash
SPEEDROUTER_HOST=0.0.0.0 SPEEDROUTER_PORT=8080 python app.py
```

---

## Install as a CLI tool (`speedrouter` command)

```bash
pip install -e .
speedrouter                          # localhost:5000
speedrouter --host 0.0.0.0          # expose to network
speedrouter --host 0.0.0.0 --port 8080
```

---

## Side-load with Docker (any device with Docker)

Build and run on any machine — Raspberry Pi, a VPS, a spare laptop, whatever:

```bash
docker build -t speedrouter .
docker run -p 5000:5000 speedrouter
# → http://<device-ip>:5000
```

### Or use Docker Compose (one command)

```bash
docker compose up -d
# → http://<device-ip>:5000
```

Stop it:

```bash
docker compose down
```

---

## Development & tests

```bash
pip install -r requirements.txt pytest
python -m pytest test_app.py -v
```

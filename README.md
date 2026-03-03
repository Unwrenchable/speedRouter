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

## Deploy to Render

> **Note:** The modem connect, optimise, and VPN features require the server to be
> on the **same LAN as your modem**. A cloud-hosted instance cannot reach private
> IP addresses such as `192.168.x.x`, so those tabs will return connection errors.
> The **Speed Test** tab works from any host.

1. Push this repo to GitHub (or fork it).
2. Create a new **Web Service** on [Render](https://render.com) and connect the repo.
   Render auto-detects `render.yaml`, so no extra configuration is needed.
3. Set a `SECRET_KEY` environment variable (Render generates one automatically if you
   use the `render.yaml` included in this repo).
4. Deploy – the service starts with Gunicorn on the port Render assigns.

One-click deploy using `render.yaml` (already included):

```bash
# Nothing to do – just connect the repo in the Render dashboard.
```

---

## Deploy to Vercel

> **Same limitation applies:** modem management features need LAN access to your
> router and will not work from Vercel's infrastructure.
> Additionally, Vercel's default function timeout (10 s on the free Hobby plan)
> may cut off a speed test before it completes; upgrade to Pro (60 s max) or use
> Render for reliable speed tests.

1. Install the [Vercel CLI](https://vercel.com/docs/cli) and log in:

   ```bash
   npm i -g vercel
   vercel login
   ```

2. Deploy from the project root:

   ```bash
   vercel
   ```

   The included `vercel.json` routes all traffic to `app.py` via the
   `@vercel/python` runtime.

3. Set `SECRET_KEY` in the Vercel dashboard (**Settings → Environment Variables**)
   so sessions survive redeployments.

---

## Development & tests

```bash
pip install -r requirements.txt pytest
python -m pytest test_app.py -v
```

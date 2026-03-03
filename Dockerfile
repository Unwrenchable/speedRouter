# ── Build stage (install deps) ────────────────────────────────────────────────
FROM python:3.11-slim AS base

WORKDIR /app

# Install Python dependencies first (layer-cached)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Runtime stage ─────────────────────────────────────────────────────────────
FROM base AS runtime

COPY . .

# Expose the UI port
EXPOSE 5000

# SPEEDROUTER_HOST=0.0.0.0 makes the server reachable from outside the container.
# Override SPEEDROUTER_PORT if you remap the port.
ENV SPEEDROUTER_HOST=0.0.0.0
ENV SPEEDROUTER_PORT=5000

CMD ["python", "app.py"]

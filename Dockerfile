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

# Number of Gunicorn worker processes; override at runtime with -e WEB_CONCURRENCY=4
ENV WEB_CONCURRENCY=2

# Use Gunicorn (production WSGI server) instead of the Flask development server.
# Bind to all interfaces so the container port is reachable from the host.
# $WEB_CONCURRENCY is expanded by the shell; use the exec-form with sh -c to support it.
CMD ["sh", "-c", "gunicorn app:app --bind 0.0.0.0:5000 --workers ${WEB_CONCURRENCY} --access-logfile - --error-logfile -"]

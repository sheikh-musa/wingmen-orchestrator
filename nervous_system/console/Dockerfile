# Fleet Console v1 — VPS-portable container (CAI-RESP-264 / portability).
#
# Lift-and-shift: the console moves WITH the orchestrator to the VPS. No Mac-
# specific paths; all config via env. Build context is the repo root so the
# nervous_system package + its tiny deps are available.
#
#   docker build -f nervous_system/console/Dockerfile -t fleet-console .
#   docker run --rm -p 8787:8787 \
#       -e CONSOLE_TOKEN=... \
#       -e CONSOLE_DB_URL=postgresql://console_readonly:...@host/db \
#       -e CONSOLE_HOST=0.0.0.0 -e CONSOLE_PORT=8787 \
#       fleet-console
#
# CONSOLE_DB_URL must be the SELECT-only role DSN — NEVER the service key.
# Front the container with the tunnel + Cloudflare Access (network layer first).

FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    CONSOLE_HOST=0.0.0.0 \
    CONSOLE_PORT=8787

WORKDIR /app

# Dependency-light: stdlib HTTP server + psycopg (v3) + python-dotenv (optional).
# No FastAPI/uvicorn/sse-starlette needed.
RUN pip install --no-cache-dir "psycopg[binary]>=3.2" "python-dotenv>=1.0"

# Only the package the console needs.
COPY nervous_system/console /app/nervous_system/console
COPY nervous_system/__init__.py /app/nervous_system/__init__.py

# Drop privileges — the console is a read-only surface; run unprivileged.
RUN useradd -r -u 10001 console && chown -R console /app
USER console

EXPOSE 8787

# No service key in the image; secrets are injected at runtime via env.
CMD ["python", "-m", "nervous_system.console"]

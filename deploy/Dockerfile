# Penumbra public replay demo (T32): one container serves the API, the WebSocket and the built frontend.
# Replay mode by default (PENUMBRA_OFFLINE=1, no keys baked in); the systemd unit may pass a model key at run time. Build from the repo root:
#   docker build -f deploy/Dockerfile -t penumbra .

# ---- frontend build
FROM node:20-slim AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---- runtime
FROM python:3.11-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PENUMBRA_OFFLINE=1 \
    PENUMBRA_REPLAY=runs/demo/live-fault-storm.jsonl \
    PENUMBRA_REPLAY_PACE_MS=700 \
    PENUMBRA_STATIC_DIR=/app/frontend/dist \
    PENUMBRA_ORBIT_CACHE=/tmp/penumbra-orbit-cache
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY contracts/ contracts/
COPY backend/ backend/
COPY data/ data/
COPY runs/demo/ runs/demo/
COPY bench/results/ bench/results/
COPY pyproject.toml ./
COPY --from=frontend /app/frontend/dist /app/frontend/dist
RUN useradd --system --no-create-home penumbra && mkdir -p /app/runs /tmp/penumbra-orbit-cache \
    && chown -R penumbra /app/runs /tmp/penumbra-orbit-cache
USER penumbra
EXPOSE 8000
CMD ["uvicorn", "backend.api.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]

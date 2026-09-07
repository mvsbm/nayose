# syntax=docker/dockerfile:1
# Nayose serving image. Data + models are RUNTIME MOUNTS, never baked in —
# the image is immutable and runs anywhere (docker, k8s, fly.io, GHCR).
#   docker build -t ghcr.io/<org>/nayose:latest .
#   NAYOSE_API_TOKEN=... NAYOSE_WARM_EMBED=1 docker compose up

# ---- builder: install the app venv (cache-friendly layer) ----
FROM python:3.13-slim AS builder
ENV PIP_NO_CACHE_DIR=1
# ARG lets CI pick the stack: matcher,serve (default) | add neural for CUDA/MPS boxes
ARG EXTRAS="matcher,serve"
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
COPY pyproject.toml README.md ./
COPY synthetic/ synthetic/
COPY api/ api/
COPY frontend/ frontend/
COPY scripts/generate_data.py scripts/train.py scripts/audit_decisions.py scripts/
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install -U pip \
    && /opt/venv/bin/pip install -e . --no-deps \
    && /opt/venv/bin/pip install ".[${EXTRAS}]"

# ---- runtime: slim, non-root, drop privileges via su-exec ----
FROM python:3.13-slim AS runtime
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    NAYOSE_DATA_DIR=/app/data/current \
    NAYOSE_MODELS_DIR=/app/models \
    NAYOSE_DASH_PORT=8000 \
    NAYOSE_BIND_HOST=0.0.0.0
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 curl su-exec \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -m -u 10001 nayose
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /src/api ./api
COPY --from=builder /src/frontend ./frontend
COPY --from=builder /src/synthetic ./synthetic
COPY --from=builder /src/pyproject.toml ./
COPY --from=builder /src/scripts ./scripts
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh
# writable runtime dirs (decisions live under models/) — volumes are mounted at runtime
RUN mkdir -p /app/data /app/models && chown -R nayose:nayose /app
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["python", "-m", "api.main"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/api/health || exit 1
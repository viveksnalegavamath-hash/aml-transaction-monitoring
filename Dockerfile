# Image of the read-only FastAPI service (fast-track step 8).
# Official images only, pinned: Python 3.12 (slim) and Astral's uv.
FROM ghcr.io/astral-sh/uv:0.12.21 AS uv

FROM python:3.12-slim

# OpenMP runtime, needed by XGBoost.
RUN apt-get update \
    && apt-get install --yes --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=uv /uv /usr/local/bin/uv

ENV UV_PYTHON_DOWNLOADS=never \
    UV_NO_CACHE=1 \
    VIRTUAL_ENV=/app/.venv
WORKDIR /app

# Dependencies first, so code changes reuse this layer. Exact versions and
# hashes from uv.lock, minus what only training, tracking or other pipeline
# steps use (pruned with the dependencies only they need) and NVIDIA's NCCL
# (multi-GPU XGBoost only). This keeps the image about 3x smaller.
COPY pyproject.toml uv.lock ./
RUN uv export --frozen --no-dev --no-emit-project --output-file /tmp/requirements.txt \
        --prune mlflow --prune lightgbm --prune pyvis --prune networkx \
        --prune anthropic --prune faker --prune rapidfuzz --prune pyarrow \
        --prune sqlalchemy --prune nvidia-nccl-cu12 \
    && uv venv "$VIRTUAL_ENV" \
    && uv pip install --require-hashes --requirements /tmp/requirements.txt \
    && rm /tmp/requirements.txt

# Then the code, its settings and the rule specs. No data and no secrets:
# the model is mounted read-only at run time, secrets come from Compose.
COPY src ./src
COPY config ./config
COPY rules ./rules
RUN uv pip install --no-deps --editable .

RUN useradd --system --uid 10001 --no-create-home api
USER api

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    MODEL_DIR=/app/model \
    NUMBA_CACHE_DIR=/tmp/numba \
    MPLCONFIGDIR=/tmp/matplotlib
EXPOSE 8000

# 0.0.0.0 inside the container only, so Docker can forward to it; Compose
# publishes the port on 127.0.0.1 of the host. No access log: it would print
# raw paths with account IDs (the app logs route templates instead).
CMD ["uvicorn", "--factory", "aml_transaction_monitoring.api.app:create_app", \
     "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--no-server-header"]

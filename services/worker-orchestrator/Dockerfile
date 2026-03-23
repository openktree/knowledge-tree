FROM python:3.12-slim

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock ./
COPY libs/ libs/
COPY services/worker-orchestrator/ services/worker-orchestrator/

RUN cd services/worker-orchestrator && uv sync --frozen --no-dev --package kt-worker-orchestrator

CMD ["uv", "run", "--project", "services/worker-orchestrator", "-m", "kt_worker_orchestrator"]

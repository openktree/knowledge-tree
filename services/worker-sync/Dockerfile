FROM python:3.12-slim

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock ./
COPY libs/ libs/
COPY services/worker-sync/ services/worker-sync/

RUN cd services/worker-sync && uv sync --frozen --no-dev

CMD ["uv", "run", "--project", "services/worker-sync", "-m", "kt_worker_sync"]

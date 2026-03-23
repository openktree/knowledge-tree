FROM python:3.12-slim

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock ./
COPY libs/ libs/
COPY services/worker-nodes/ services/worker-nodes/

RUN cd services/worker-nodes && uv sync --frozen --no-dev --package kt-worker-nodes

CMD ["uv", "run", "--project", "services/worker-nodes", "-m", "kt_worker_nodes"]

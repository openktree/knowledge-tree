FROM python:3.12-slim

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock ./
COPY libs/ libs/
COPY services/worker-ingest/ services/worker-ingest/

RUN cd services/worker-ingest && uv sync --frozen --no-dev --package kt-worker-ingest

CMD ["uv", "run", "--project", "services/worker-ingest", "-m", "kt_worker_ingest"]

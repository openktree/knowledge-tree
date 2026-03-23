FROM python:3.12-slim

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock ./
COPY libs/ libs/
COPY services/worker-search/ services/worker-search/

RUN cd services/worker-search && uv sync --frozen --no-dev --package kt-worker-search

CMD ["uv", "run", "--project", "services/worker-search", "-m", "kt_worker_search"]

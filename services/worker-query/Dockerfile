FROM python:3.12-slim

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock ./
COPY libs/ libs/
COPY services/worker-query/ services/worker-query/

RUN cd services/worker-query && uv sync --frozen --no-dev

CMD ["uv", "run", "--project", "services/worker-query", "-m", "kt_worker_query"]

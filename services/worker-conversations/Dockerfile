FROM python:3.12-slim

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock ./
COPY libs/ libs/
COPY services/worker-conversations/ services/worker-conversations/

RUN cd services/worker-conversations && uv sync --frozen --no-dev

CMD ["uv", "run", "--project", "services/worker-conversations", "-m", "kt_worker_conv"]

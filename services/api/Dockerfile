FROM python:3.12-slim

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy workspace root
COPY pyproject.toml uv.lock ./

# Copy all workspace member pyproject.toml files (uv needs them to resolve workspace)
COPY libs/ libs/
COPY services/api/pyproject.toml services/api/pyproject.toml
COPY services/mcp/pyproject.toml services/mcp/pyproject.toml
COPY services/worker-all/pyproject.toml services/worker-all/pyproject.toml
COPY services/worker-bottomup/pyproject.toml services/worker-bottomup/pyproject.toml
COPY services/worker-ingest/pyproject.toml services/worker-ingest/pyproject.toml
COPY services/worker-nodes/pyproject.toml services/worker-nodes/pyproject.toml
COPY services/worker-search/pyproject.toml services/worker-search/pyproject.toml
COPY services/worker-sync/pyproject.toml services/worker-sync/pyproject.toml
COPY services/worker-synthesis/pyproject.toml services/worker-synthesis/pyproject.toml

# Copy this service's source + worker packages imported by the API
# (research.py imports kt_worker_ingest; prompt_transparency.py imports kt_worker_synthesis, kt_worker_nodes)
COPY services/api/ services/api/
COPY services/worker-ingest/ services/worker-ingest/
COPY services/worker-synthesis/ services/worker-synthesis/
COPY services/worker-nodes/ services/worker-nodes/

# Install dependencies
RUN uv sync --frozen --no-dev --package kt-api

EXPOSE 8000

CMD ["uv", "run", "--project", "services/api", "uvicorn", "kt_api.main:app", "--host", "0.0.0.0", "--port", "8000"]

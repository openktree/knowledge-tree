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
COPY services/worker-conversations/pyproject.toml services/worker-conversations/pyproject.toml
COPY services/worker-ingest/pyproject.toml services/worker-ingest/pyproject.toml
COPY services/worker-nodes/pyproject.toml services/worker-nodes/pyproject.toml
COPY services/worker-orchestrator/pyproject.toml services/worker-orchestrator/pyproject.toml
COPY services/worker-query/pyproject.toml services/worker-query/pyproject.toml
COPY services/worker-search/pyproject.toml services/worker-search/pyproject.toml
COPY services/worker-sync/pyproject.toml services/worker-sync/pyproject.toml

# Copy this service's source
COPY services/mcp/ services/mcp/

# Install dependencies
RUN uv sync --frozen --no-dev --package kt-mcp

EXPOSE 8001

CMD ["uv", "run", "--project", "services/mcp", "uvicorn", "kt_mcp.server:app", "--host", "0.0.0.0", "--port", "8001"]

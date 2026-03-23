FROM python:3.12-slim

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy workspace root
COPY pyproject.toml uv.lock ./

# Copy libs
COPY libs/ libs/

# Copy this service
COPY services/mcp/ services/mcp/

# Install dependencies
RUN cd services/mcp && uv sync --frozen --no-dev

EXPOSE 8001

CMD ["uv", "run", "--project", "services/mcp", "uvicorn", "kt_mcp.server:app", "--host", "0.0.0.0", "--port", "8001"]

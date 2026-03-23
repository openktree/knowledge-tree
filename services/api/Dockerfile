FROM python:3.12-slim

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy workspace root
COPY pyproject.toml uv.lock ./

# Copy libs
COPY libs/ libs/

# Copy this service
COPY services/api/ services/api/

# Install dependencies
RUN cd services/api && uv sync --frozen --no-dev --package kt-api

EXPOSE 8000

CMD ["uv", "run", "--project", "services/api", "uvicorn", "kt_api.main:app", "--host", "0.0.0.0", "--port", "8000"]

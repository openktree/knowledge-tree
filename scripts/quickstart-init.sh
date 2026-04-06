#!/bin/sh
# Knowledge Tree — Quickstart initialization script
#
# Generates a Hatchet API token and writes it to .env, then starts
# all services. Run this once after copying .env.quickstart to .env.
#
# Usage:
#   ./scripts/quickstart-init.sh

set -e

COMPOSE_FILE="docker-compose.quickstart.yml"

if [ ! -f .env ]; then
    echo "ERROR: .env not found."
    echo "  Run 'cp .env.example .env' and add your API keys first."
    echo "  At minimum you need OPENROUTER_API_KEY."
    exit 1
fi

echo "==> Starting infrastructure..."
docker compose -f "$COMPOSE_FILE" up -d postgres postgres-write pgbouncer-write redis qdrant hatchet-db hatchet

echo "==> Waiting for Hatchet to be ready..."
RETRIES=0
MAX_RETRIES=30
until docker compose -f "$COMPOSE_FILE" exec -T hatchet wget -q --spider http://localhost:8080/api/live 2>/dev/null; do
    RETRIES=$((RETRIES + 1))
    if [ "$RETRIES" -ge "$MAX_RETRIES" ]; then
        echo "ERROR: Hatchet not ready after 60 seconds"
        exit 1
    fi
    sleep 2
done

echo "==> Generating Hatchet API token..."
TOKEN="$(docker compose -f "$COMPOSE_FILE" exec -T hatchet /hatchet-admin token create \
    --config /config \
    --tenant-id 707d0855-80ab-4e1f-a156-f1c4546cbf52 2>/dev/null | tr -d '[:space:]')"

if [ -z "$TOKEN" ]; then
    echo "ERROR: Failed to generate Hatchet token"
    exit 1
fi

# Update .env — replace existing line or append
# Use a temp file for portability (sed -i behaves differently on macOS)
if grep -q '^HATCHET_CLIENT_TOKEN=' .env 2>/dev/null; then
    sed "s|^HATCHET_CLIENT_TOKEN=.*|HATCHET_CLIENT_TOKEN=${TOKEN}|" .env > .env.tmp && mv .env.tmp .env
else
    echo "HATCHET_CLIENT_TOKEN=${TOKEN}" >> .env
fi

echo "==> Hatchet token written to .env"

echo "==> Starting all services..."
docker compose -f "$COMPOSE_FILE" up -d

echo ""
echo "==> Knowledge Tree is starting up!"
echo "    Frontend:  http://localhost:3000"
echo "    API:       http://localhost:8000"
echo "    API docs:  http://localhost:8000/docs"
echo ""
echo "    Register your first account — it will be auto-promoted to admin."
echo ""
echo "    To view logs:  docker compose -f $COMPOSE_FILE logs -f"
echo "    To stop:       docker compose -f $COMPOSE_FILE down"

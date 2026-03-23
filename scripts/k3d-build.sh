#!/usr/bin/env bash
set -euo pipefail

# Build and push all Knowledge Tree images to the k3d local registry.
#
# Usage:
#   ./scripts/k3d-build.sh            # build all
#   ./scripts/k3d-build.sh api        # build just api
#   ./scripts/k3d-build.sh api worker-orchestrator  # build specific services

# Host pushes to localhost:5111, but k8s pods pull via the k3d internal name
REGISTRY_PUSH="localhost:5111"
REGISTRY_K8S="k3d-knowledge-tree-local-registry:5111"
TAG="dev"

# All buildable services (python services share a generic Dockerfile pattern)
PYTHON_SERVICES=(
  api
  mcp
  worker-orchestrator
  worker-search
  worker-nodes
  worker-query
  worker-ingest
  worker-conversations
  worker-sync
)

FRONTEND_SERVICES=(
  frontend
  wiki-frontend
)

cd "$(git rev-parse --show-toplevel)"

build_python_service() {
  local svc="$1"
  local svc_dir="services/${svc}"
  local image_name="kt-${svc}"

  if [[ ! -d "$svc_dir" ]]; then
    echo "ERROR: $svc_dir does not exist"
    return 1
  fi

  # Derive the CMD from the existing Dockerfile
  local cmd
  cmd=$(grep '^CMD ' "$svc_dir/Dockerfile" | head -1)

  echo "==> Building ${image_name}:${TAG}"
  docker build \
    -t "${REGISTRY_PUSH}/${image_name}:${TAG}" \
    -f - . <<DOCKERFILE
FROM python:3.12-slim
WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./
COPY libs/ libs/
COPY services/ services/
COPY config.yaml ./
RUN cd ${svc_dir} && uv sync --frozen --no-dev
EXPOSE 8000
${cmd}
DOCKERFILE

  echo "==> Pushing ${image_name}:${TAG}"
  docker push "${REGISTRY_PUSH}/${image_name}:${TAG}"
  echo ""
}

build_frontend() {
  echo "==> Building kt-frontend:${TAG}"
  docker build \
    -t "${REGISTRY_PUSH}/kt-frontend:${TAG}" \
    -f helm/knowledge-tree/frontend/Dockerfile .
  echo "==> Pushing kt-frontend:${TAG}"
  docker push "${REGISTRY_PUSH}/kt-frontend:${TAG}"
  echo ""
}

build_wiki_frontend() {
  echo "==> Building kt-wiki-frontend:${TAG}"
  docker build \
    -t "${REGISTRY_PUSH}/kt-wiki-frontend:${TAG}" \
    -f helm/knowledge-tree/wiki-frontend/Dockerfile .
  echo "==> Pushing kt-wiki-frontend:${TAG}"
  docker push "${REGISTRY_PUSH}/kt-wiki-frontend:${TAG}"
  echo ""
}

build_service() {
  local svc="$1"
  case "$svc" in
    frontend)
      build_frontend
      ;;
    wiki-frontend)
      build_wiki_frontend
      ;;
    *)
      build_python_service "$svc"
      ;;
  esac
}

# If specific services given as args, build only those
if [[ $# -gt 0 ]]; then
  for svc in "$@"; do
    build_service "$svc"
  done
else
  # Build all
  for svc in "${PYTHON_SERVICES[@]}"; do
    build_service "$svc"
  done
  for svc in "${FRONTEND_SERVICES[@]}"; do
    build_service "$svc"
  done
fi

echo "Done! All images pushed to ${REGISTRY_PUSH} (k8s pulls from ${REGISTRY_K8S})"

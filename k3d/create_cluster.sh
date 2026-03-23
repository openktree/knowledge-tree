#!/bin/bash

# Source the config file
source "$(dirname "$0")/cluster_config.sh"

# Delete existing cluster and registry if they exist
k3d cluster delete ${CLUSTER_NAME} || true
k3d registry delete ${REGISTRY_NAME} || true

# Create the k3d cluster with registry, port mappings (use 8080/8443 to avoid sudo),
# fixed API port, and 1 agent for workloads
k3d cluster create ${CLUSTER_NAME} \
  --registry-create ${REGISTRY_NAME}:0.0.0.0:5500 \
  --api-port 6443 \
  --port '9080:80@loadbalancer' \
  --port '9443:443@loadbalancer' \
  --agents 1 \
  --k3s-arg '--resolv-conf=/etc/resolv.conf@all' \
  --k3s-arg "--disable=traefik@server:*" \
  --wait

# Export kubeconfig for easy use
k3d kubeconfig write ${CLUSTER_NAME} > ~/.kube/${CLUSTER_NAME}.yaml
echo "Cluster ready! Kubeconfig: ~/.kube/${CLUSTER_NAME}.yaml"
echo "Registry: localhost:5500"
echo "Ingress: http://<host>.localhost:9080 (add to /etc/hosts: 127.0.0.1 <host>.localhost)"
echo "API: https://localhost:6443"

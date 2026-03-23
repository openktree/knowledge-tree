#!/bin/bash
# Verification script for centralized secrets configuration

set -e

echo "🔍 Verifying Centralized Secrets Configuration"
echo "=============================================="
echo ""

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check if kustomize is available
if ! command -v kustomize &> /dev/null; then
    echo -e "${RED}✗ kustomize not found. Please install kustomize.${NC}"
    exit 1
fi
echo -e "${GREEN}✓ kustomize found${NC}"

# Check if kubectl is available
if ! command -v kubectl &> /dev/null; then
    echo -e "${YELLOW}⚠ kubectl not found. Skipping cluster validation.${NC}"
    KUBECTL_AVAILABLE=false
else
    echo -e "${GREEN}✓ kubectl found${NC}"
    KUBECTL_AVAILABLE=true
fi

echo ""
echo "📁 Checking file structure..."
echo "------------------------------"

# Check that all required files exist
FILES=(
    "cluster-secret-store.yaml"
    "password-generators.yaml"
    "external-secrets.yaml"
    "kustomization.yaml"
    "README.md"
)

for file in "${FILES[@]}"; do
    if [ -f "$file" ]; then
        echo -e "${GREEN}✓ $file exists${NC}"
    else
        echo -e "${RED}✗ $file missing${NC}"
        exit 1
    fi
done

echo ""
echo "🔨 Validating kustomization build..."
echo "-------------------------------------"

# Build and validate the kustomization
OUTPUT=$(kustomize build . 2>&1)
if [ $? -eq 0 ]; then
    echo -e "${GREEN}✓ Kustomization builds successfully${NC}"

    # Count resources
    CLUSTERSECRETSTORES=$(echo "$OUTPUT" | grep -c "kind: ClusterSecretStore" || true)
    PASSWORDS=$(echo "$OUTPUT" | grep -c "kind: Password" || true)
    EXTERNALSECRETS=$(echo "$OUTPUT" | grep -c "kind: ExternalSecret" || true)

    echo ""
    echo "📊 Resource counts:"
    echo "  - ClusterSecretStores: $CLUSTERSECRETSTORES (expected: 1)"
    echo "  - Password generators: $PASSWORDS (expected: 4)"
    echo "  - ExternalSecrets: $EXTERNALSECRETS (expected: 6)"

    if [ "$CLUSTERSECRETSTORES" -eq 1 ] && [ "$PASSWORDS" -eq 4 ] && [ "$EXTERNALSECRETS" -eq 6 ]; then
        echo -e "${GREEN}✓ All expected resources present${NC}"
    else
        echo -e "${RED}✗ Resource count mismatch${NC}"
        exit 1
    fi
else
    echo -e "${RED}✗ Kustomization build failed${NC}"
    echo "$OUTPUT"
    exit 1
fi

# Cluster validation (if kubectl is available)
if [ "$KUBECTL_AVAILABLE" = true ]; then
    echo ""
    echo "☸️  Checking cluster resources..."
    echo "---------------------------------"

    # Check if cluster is accessible
    if ! kubectl cluster-info &> /dev/null; then
        echo -e "${YELLOW}⚠ Cannot connect to cluster. Skipping cluster validation.${NC}"
    else
        # Check ClusterSecretStore
        if kubectl get clustersecretstore password-store &> /dev/null; then
            echo -e "${GREEN}✓ ClusterSecretStore 'password-store' exists${NC}"
        else
            echo -e "${YELLOW}⚠ ClusterSecretStore 'password-store' not found (not yet applied?)${NC}"
        fi

        # Check namespace
        if kubectl get namespace share-app &> /dev/null; then
            echo -e "${GREEN}✓ Namespace 'share-app' exists${NC}"

            # Check Password generators
            CLUSTER_PASSWORDS=$(kubectl get password -n share-app --no-headers 2>/dev/null | wc -l || echo "0")
            if [ "$CLUSTER_PASSWORDS" -eq 4 ]; then
                echo -e "${GREEN}✓ All 4 Password generators exist in cluster${NC}"
            elif [ "$CLUSTER_PASSWORDS" -gt 0 ]; then
                echo -e "${YELLOW}⚠ Found $CLUSTER_PASSWORDS Password generators (expected 4)${NC}"
            else
                echo -e "${YELLOW}⚠ No Password generators found (not yet applied?)${NC}"
            fi

            # Check ExternalSecrets
            CLUSTER_EXTERNALSECRETS=$(kubectl get externalsecrets -n share-app --no-headers 2>/dev/null | wc -l || echo "0")
            if [ "$CLUSTER_EXTERNALSECRETS" -eq 6 ]; then
                echo -e "${GREEN}✓ All 6 ExternalSecrets exist in cluster${NC}"
            elif [ "$CLUSTER_EXTERNALSECRETS" -gt 0 ]; then
                echo -e "${YELLOW}⚠ Found $CLUSTER_EXTERNALSECRETS ExternalSecrets (expected 6)${NC}"
            else
                echo -e "${YELLOW}⚠ No ExternalSecrets found (not yet applied?)${NC}"
            fi

            # Check generated secrets
            SECRETS=(
                "postgres-openfga-credentials"
                "openfga-db"
                "postgres-keycloak-credentials"
                "keycloak-db"
                "keycloak-admin"
                "postgres-api-credentials"
            )

            echo ""
            echo "🔐 Checking generated secrets:"
            FOUND=0
            for secret in "${SECRETS[@]}"; do
                if kubectl get secret "$secret" -n share-app &> /dev/null; then
                    echo -e "${GREEN}  ✓ $secret${NC}"
                    ((FOUND++))
                else
                    echo -e "${YELLOW}  ⚠ $secret (not found)${NC}"
                fi
            done

            if [ "$FOUND" -eq 6 ]; then
                echo -e "${GREEN}✓ All 6 secrets generated successfully${NC}"
            elif [ "$FOUND" -gt 0 ]; then
                echo -e "${YELLOW}⚠ Only $FOUND of 6 secrets found${NC}"
            else
                echo -e "${YELLOW}⚠ No secrets generated yet (ExternalSecrets not synced?)${NC}"
            fi
        else
            echo -e "${YELLOW}⚠ Namespace 'share-app' not found${NC}"
        fi
    fi
fi

echo ""
echo "=============================================="
echo -e "${GREEN}✅ Verification complete!${NC}"
echo ""
echo "Next steps:"
echo "  1. Review the README.md for usage instructions"
echo "  2. Apply the configuration: kubectl apply -k ."
echo "  3. Check ExternalSecret status: kubectl get externalsecrets -n share-app"
echo "  4. Verify secrets: kubectl get secrets -n share-app"

# Centralized Secrets Management

This directory contains all centralized secret management configuration for the infrastructure. All password generators, external secrets, and secret stores are defined here.

## Structure

```
secrets/
├── cluster-secret-store.yaml    # Single ClusterSecretStore for all apps
├── password-generators.yaml     # All password generators in one file
├── external-secrets.yaml        # All ExternalSecrets in one file
├── kustomization.yaml          # Kustomize config
└── README.md                   # This file
```

## Files

### cluster-secret-store.yaml
Defines the single `ClusterSecretStore` named `password-store` that all ExternalSecrets reference. This store is scoped to the `share-app` namespace.

### password-generators.yaml
Contains all Password generator resources:
- `openfga-db-password` - OpenFGA database password
- `keycloak-db-password` - Keycloak database password
- `keycloak-admin-password` - Keycloak admin user password
- `postgres-api-password` - API database password

All generators use consistent configuration:
- Length: 32 characters
- Digits: 5
- Symbols: 5
- Symbol characters: `!@#$%^&*()_+-=[]{}|`

### external-secrets.yaml
Contains all ExternalSecret resources that reference the password generators:

#### OpenFGA Secrets
- `openfga-db-credentials` - Creates `postgres-openfga-credentials` secret (basic-auth)
- `openfga-db-uri` - Creates `openfga-db` secret with full PostgreSQL URI

#### Keycloak Secrets
- `keycloak-db-credentials` - Creates `postgres-keycloak-credentials` secret (basic-auth)
- `keycloak-db` - Creates `keycloak-db` secret with JDBC connection details
- `keycloak-admin` - Creates `keycloak-admin` secret for admin user

#### API Secrets
- `postgres-api-credentials` - Creates `postgres-api-credentials` secret (basic-auth)

All ExternalSecrets:
- Reference the `password-store` ClusterSecretStore
- Use `refreshInterval: "0"` (generate once, never refresh)
- Use `creationPolicy: Owner` (External Secrets owns the secret lifecycle)

## Generated Kubernetes Secrets

The following secrets are created in the `share-app` namespace:

| Secret Name | Type | Keys | Used By |
|-------------|------|------|---------|
| `postgres-openfga-credentials` | kubernetes.io/basic-auth | username, password | OpenFGA CNPG cluster |
| `openfga-db` | Opaque | uri | OpenFGA deployment |
| `postgres-keycloak-credentials` | kubernetes.io/basic-auth | username, password | Keycloak CNPG cluster |
| `keycloak-db` | Opaque | url, username, password | Keycloak Helm chart |
| `keycloak-admin` | Opaque | username, password | Keycloak Helm chart |
| `postgres-api-credentials` | kubernetes.io/basic-auth | username, password | API CNPG cluster |

## Deployment Order

The Flux Kustomization ensures proper deployment order:

1. **External Secrets Operator** - Installed first
2. **Centralized Secrets** (this directory) - Depends on External Secrets Operator
3. **CNPG Operator** - Can run in parallel with secrets
4. **App-specific resources** - Depend on both CNPG and centralized secrets

See `infrastructure/flux/communities-sync.yaml` for the full dependency chain.

## Benefits of Centralization

### Before
- 7 YAML files scattered across 3 app directories
- Duplicate secret definitions (OpenFGA and Keycloak defined twice)
- Inconsistent configuration (different digit counts, refresh strategies)
- ClusterSecretStore only partially used
- Difficult to audit all secrets

### After
- 3 YAML files in one location
- No duplicates - single source of truth
- Consistent configuration across all generators
- All ExternalSecrets use the ClusterSecretStore
- Easy to audit and modify all secrets

## Usage

### Adding a New Secret

1. Add a Password generator to `password-generators.yaml`:
```yaml
---
apiVersion: generators.external-secrets.io/v1alpha1
kind: Password
metadata:
  name: my-new-password
  namespace: share-app
spec:
  length: 32
  digits: 5
  symbols: 5
  symbolCharacters: "!@#$%^&*()_+-=[]{}|"
  noUpper: false
  allowRepeat: true
```

2. Add an ExternalSecret to `external-secrets.yaml`:
```yaml
---
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: my-new-secret
  namespace: share-app
spec:
  refreshInterval: "0"
  secretStoreRef:
    name: password-store
    kind: ClusterSecretStore
  target:
    name: my-kubernetes-secret
    creationPolicy: Owner
    template:
      type: kubernetes.io/basic-auth  # or Opaque
      data:
        username: my_user
        password: "{{ .password }}"
  dataFrom:
    - sourceRef:
        generatorRef:
          apiVersion: generators.external-secrets.io/v1alpha1
          kind: Password
          name: my-new-password
```

3. Commit and push. Flux will automatically apply the changes.

### Viewing Generated Secrets

```bash
# List all secrets in share-app namespace
kubectl get secrets -n share-app

# View a specific secret (base64 encoded)
kubectl get secret postgres-openfga-credentials -n share-app -o yaml

# Decode a password
kubectl get secret postgres-openfga-credentials -n share-app -o jsonpath='{.data.password}' | base64 -d
```

### Troubleshooting

Check External Secrets status:
```bash
# List all ExternalSecrets
kubectl get externalsecrets -n share-app

# Check specific ExternalSecret status
kubectl describe externalsecret openfga-db-credentials -n share-app

# Check ClusterSecretStore status
kubectl describe clustersecretstore password-store
```

Check Password generators:
```bash
# List all Password generators
kubectl get password -n share-app

# Check specific Password generator
kubectl describe password openfga-db-password -n share-app
```

## Migration from Old Structure

The old secret files have been removed:
- `infrastructure/apps/openfga/base/cluster-secret-store.yaml` - ❌ Removed
- `infrastructure/apps/openfga/base/secret-generator.yaml` - ❌ Removed
- `infrastructure/apps/openfga/base/external-secret.yaml` - ❌ Removed
- `infrastructure/apps/openfga/base/openfga-db-uri-secret.yaml` - ❌ Removed
- `infrastructure/apps/keycloak/overlays/production/keycloak-db-password.yaml` - ❌ Removed
- `infrastructure/apps/keycloak/overlays/production/keycloak-admin-password.yaml` - ❌ Removed
- `infrastructure/apps/communities-app/overlays/production/postgres-credentials.yaml` - ❌ Removed

All functionality now provided by this centralized directory.

## Security Considerations

- Passwords are generated with strong defaults (32 chars, mixed alphanumeric + symbols)
- Secrets are never refreshed after initial generation (`refreshInterval: "0"`)
- External Secrets operator owns secret lifecycle (`creationPolicy: Owner`)
- ClusterSecretStore is scoped to `share-app` namespace only
- All secrets are stored in Kubernetes etcd (encrypted at rest if cluster configured)
- No passwords are stored in Git - only generator configurations

## References

- [External Secrets Operator Documentation](https://external-secrets.io/)
- [Password Generator](https://external-secrets.io/latest/api/generator/password/)
- [ExternalSecret API](https://external-secrets.io/latest/api/externalsecret/)
- [ClusterSecretStore API](https://external-secrets.io/latest/api/clustersecretstore/)

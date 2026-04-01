{{/*
Expand the name of the chart.
*/}}
{{- define "knowledge-tree.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "knowledge-tree.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "knowledge-tree.labels" -}}
helm.sh/chart: {{ include "knowledge-tree.name" . }}-{{ .Chart.Version | replace "+" "_" }}
{{ include "knowledge-tree.selectorLabels" . }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "knowledge-tree.selectorLabels" -}}
app.kubernetes.io/name: {{ include "knowledge-tree.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Component selector labels (pass dict with . = root context, component = name)
*/}}
{{- define "knowledge-tree.componentLabels" -}}
{{ include "knowledge-tree.labels" .root }}
app.kubernetes.io/component: {{ .component }}
{{- end }}

{{/*
Component selector labels (minimal, for matchLabels)
*/}}
{{- define "knowledge-tree.componentSelectorLabels" -}}
{{ include "knowledge-tree.selectorLabels" .root }}
app.kubernetes.io/component: {{ .component }}
{{- end }}

{{/*
Secret name — use existing or chart-managed
*/}}
{{- define "knowledge-tree.secretName" -}}
{{- if .Values.secrets.existingSecret }}
{{- .Values.secrets.existingSecret }}
{{- else }}
{{- include "knowledge-tree.fullname" . }}
{{- end }}
{{- end }}

{{/*
Image helper — resolves <registry>/<repo>:<tag> with per-component override
Args: dict with .root (context), .image (component image block), .defaultName (e.g. "openktree-api")
*/}}
{{- define "knowledge-tree.image" -}}
{{- $registry := .root.Values.global.imageRegistry -}}
{{- $tag := default .root.Values.global.imageTag .image.tag -}}
{{- $repo := default (printf "%s%s" (ternary (printf "%s/" $registry) "" (ne $registry "")) .defaultName) .image.repository -}}
{{- if and (ne $registry "") (not .image.repository) }}
{{- printf "%s/%s:%s" $registry .defaultName $tag }}
{{- else if .image.repository }}
{{- printf "%s:%s" .image.repository $tag }}
{{- else }}
{{- printf "%s:%s" .defaultName $tag }}
{{- end }}
{{- end }}

{{/*
CNPG cluster names
*/}}
{{- define "knowledge-tree.graphDbName" -}}
{{- printf "%s-graph-db" (include "knowledge-tree.fullname" .) }}
{{- end }}

{{- define "knowledge-tree.writeDbName" -}}
{{- printf "%s-write-db" (include "knowledge-tree.fullname" .) }}
{{- end }}

{{- define "knowledge-tree.hatchetDbName" -}}
{{- printf "%s-hatchet-db" (include "knowledge-tree.fullname" .) }}
{{- end }}

{{/*
Service names for CNPG clusters (CNPG creates <cluster>-rw services)
*/}}
{{- define "knowledge-tree.graphDbHost" -}}
{{- printf "%s-rw" (include "knowledge-tree.graphDbName" .) }}
{{- end }}

{{- define "knowledge-tree.writeDbHost" -}}
{{- printf "%s-rw" (include "knowledge-tree.writeDbName" .) }}
{{- end }}

{{- define "knowledge-tree.hatchetDbHost" -}}
{{- printf "%s-rw" (include "knowledge-tree.hatchetDbName" .) }}
{{- end }}

{{/*
PgBouncer service name
*/}}
{{- define "knowledge-tree.pgbouncerName" -}}
{{- printf "%s-pgbouncer" (include "knowledge-tree.fullname" .) }}
{{- end }}

{{/*
Shared environment variables for all Python services.
Outputs a list of env var definitions.
*/}}
{{- define "knowledge-tree.sharedEnv" -}}
- name: GRAPH_DB_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ default (printf "%s-credentials" (include "knowledge-tree.graphDbName" .)) .Values.graphDb.credentialsSecret }}
      key: password
- name: WRITE_DB_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ default (printf "%s-credentials" (include "knowledge-tree.writeDbName" .)) .Values.writeDb.credentialsSecret }}
      key: password
- name: DATABASE_URL
  value: "postgresql+asyncpg://kt:$(GRAPH_DB_PASSWORD)@{{ include "knowledge-tree.graphDbHost" . }}:5432/knowledge_tree"
- name: WRITE_DATABASE_URL
  value: "postgresql+asyncpg://kt:$(WRITE_DB_PASSWORD)@{{ include "knowledge-tree.pgbouncerName" . }}:5432/knowledge_tree_write"
- name: REDIS_URL
  value: "redis://{{ include "knowledge-tree.fullname" . }}-redis:6379/0"
- name: QDRANT_URL
  value: "http://{{ .Release.Name }}-qdrant:6333"
- name: HATCHET_CLIENT_GRPC_TARGET
  value: "{{ include "knowledge-tree.fullname" . }}-hatchet:7070"
- name: HATCHET_CLIENT_TLS_STRATEGY
  value: "none"
- name: HATCHET_CLIENT_TOKEN
  valueFrom:
    secretKeyRef:
      name: {{ include "knowledge-tree.secretName" . }}
      key: hatchet-client-token
- name: OPENROUTER_API_KEY
  valueFrom:
    secretKeyRef:
      name: {{ include "knowledge-tree.secretName" . }}
      key: openrouter-api-key
- name: OPENAI_API_KEY
  valueFrom:
    secretKeyRef:
      name: {{ include "knowledge-tree.secretName" . }}
      key: openai-api-key
- name: BRAVE_KEY
  valueFrom:
    secretKeyRef:
      name: {{ include "knowledge-tree.secretName" . }}
      key: brave-key
- name: SERPER_KEY
  valueFrom:
    secretKeyRef:
      name: {{ include "knowledge-tree.secretName" . }}
      key: serper-key
- name: JWT_SECRET_KEY
  valueFrom:
    secretKeyRef:
      name: {{ include "knowledge-tree.secretName" . }}
      key: jwt-secret-key
- name: GOOGLE_OAUTH_CLIENT_ID
  valueFrom:
    secretKeyRef:
      name: {{ include "knowledge-tree.secretName" . }}
      key: google-oauth-client-id
- name: GOOGLE_OAUTH_CLIENT_SECRET
  valueFrom:
    secretKeyRef:
      name: {{ include "knowledge-tree.secretName" . }}
      key: google-oauth-client-secret
- name: RESEND_API_KEY
  valueFrom:
    secretKeyRef:
      name: {{ include "knowledge-tree.secretName" . }}
      key: resend-api-key
- name: EMAIL_ENABLED
  value: {{ .Values.email.enabled | quote }}
- name: EMAIL_FROM_ADDRESS
  value: {{ .Values.email.fromAddress | quote }}
- name: CONFIG_PATH
  value: /app/config.yaml
{{- end }}

{{/*
Config volume mount (for Python services)
*/}}
{{- define "knowledge-tree.configVolume" -}}
- name: config
  configMap:
    name: {{ include "knowledge-tree.fullname" . }}-config
{{- end }}

{{- define "knowledge-tree.configVolumeMount" -}}
- name: config
  mountPath: /app/config.yaml
  subPath: config.yaml
  readOnly: true
{{- end }}

{{/*
Init container waiting for graph-db and Hatchet
*/}}
{{- define "knowledge-tree.initWaitContainers" -}}
- name: wait-for-graph-db
  image: busybox:1.36
  command: ['sh', '-c', 'until nc -z {{ include "knowledge-tree.graphDbHost" . }} 5432; do echo "waiting for graph-db..."; sleep 2; done']
- name: wait-for-hatchet
  image: busybox:1.36
  command: ['sh', '-c', 'until nc -z {{ include "knowledge-tree.fullname" . }}-hatchet 7070; do echo "waiting for hatchet..."; sleep 2; done']
{{- end }}

{{/*
Init container that blocks until the Hatchet client token is a valid JWT.
On first install the token job may not have run yet; the init container will
CrashLoopBackOff until the secret is patched, which is the k8s-native way
to wait for a dependency.
*/}}
{{- define "knowledge-tree.initWaitForToken" -}}
- name: wait-for-hatchet-token
  image: busybox:1.36
  env:
    - name: HATCHET_CLIENT_TOKEN
      valueFrom:
        secretKeyRef:
          name: {{ include "knowledge-tree.secretName" . }}
          key: hatchet-client-token
  command:
    - sh
    - -c
    - |
      case "$HATCHET_CLIENT_TOKEN" in
        eyJ*) echo "Valid Hatchet client token found"; exit 0 ;;
        *) echo "Waiting for Hatchet token job to generate a valid token..."; exit 1 ;;
      esac
{{- end }}

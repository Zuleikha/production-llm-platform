{{/*
Expand the name of the chart.
*/}}
{{- define "production-llm-platform.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Fully qualified app name. Truncated to 63 chars for the DNS/label limit.
*/}}
{{- define "production-llm-platform.fullname" -}}
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
Chart name and version, for the helm.sh/chart label.
*/}}
{{- define "production-llm-platform.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels applied to every object.
*/}}
{{- define "production-llm-platform.labels" -}}
helm.sh/chart: {{ include "production-llm-platform.chart" . }}
{{ include "production-llm-platform.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels — the stable subset used by Deployments/Services to match pods.
*/}}
{{- define "production-llm-platform.selectorLabels" -}}
app.kubernetes.io/name: {{ include "production-llm-platform.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Resolve the effective DATABASE_URL: an explicit override wins; otherwise, when
the dev datastores are enabled, point at the in-cluster Postgres service.
*/}}
{{- define "production-llm-platform.databaseUrl" -}}
{{- if .Values.datastores.databaseUrl -}}
{{- .Values.datastores.databaseUrl -}}
{{- else if .Values.devDependencies.enabled -}}
{{- $pg := .Values.devDependencies.postgres -}}
{{- printf "postgresql://%s:%s@%s-postgres:5432/%s" $pg.user $pg.password (include "production-llm-platform.fullname" .) $pg.database -}}
{{- end -}}
{{- end }}

{{/*
Resolve the effective REDIS_URL.
*/}}
{{- define "production-llm-platform.redisUrl" -}}
{{- if .Values.datastores.redisUrl -}}
{{- .Values.datastores.redisUrl -}}
{{- else if .Values.devDependencies.enabled -}}
{{- printf "redis://%s-redis:6379/0" (include "production-llm-platform.fullname" .) -}}
{{- end -}}
{{- end }}

{{/*
Resolve the effective QDRANT_URL.
*/}}
{{- define "production-llm-platform.qdrantUrl" -}}
{{- if .Values.datastores.qdrantUrl -}}
{{- .Values.datastores.qdrantUrl -}}
{{- else if .Values.devDependencies.enabled -}}
{{- printf "http://%s-qdrant:6333" (include "production-llm-platform.fullname" .) -}}
{{- end -}}
{{- end }}

{{- define "mediaforge.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "mediaforge.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := include "mediaforge.name" . }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{- define "mediaforge.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "mediaforge.image" -}}
{{- if .Values.image.reference }}
{{- .Values.image.reference }}
{{- else if .Values.image.digest }}
{{- printf "%s@%s" .Values.image.repository .Values.image.digest }}
{{- else }}
{{- printf "%s:%s" .Values.image.repository (default .Chart.AppVersion .Values.image.tag) }}
{{- end }}
{{- end }}

{{- define "mediaforge.labels" -}}
helm.sh/chart: {{ include "mediaforge.chart" . }}
app.kubernetes.io/name: {{ include "mediaforge.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "mediaforge.selectorLabels" -}}
app.kubernetes.io/name: {{ include "mediaforge.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "mediaforge.runtimeConfigName" -}}
{{- printf "%s-runtime" (include "mediaforge.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "mediaforge.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "mediaforge.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- required "serviceAccount.name must be set when serviceAccount.create is false" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{- define "mediaforge.persistenceClaim" -}}
{{- default (printf "%s-data" (include "mediaforge.fullname" .)) .Values.persistence.existingClaim }}
{{- end }}

{{- define "mediaforge.validate" -}}
{{- if not .Values.runtime.existingSecret }}
{{- fail "runtime.existingSecret is required and must refer to a separately managed Secret" }}
{{- end }}
{{- if and .Values.image.reference (or .Values.image.digest .Values.image.tag) }}
{{- fail "image.reference cannot be combined with image.digest or image.tag" }}
{{- end }}
{{- if and (gt (int .Values.api.replicaCount) 1) (not .Values.persistence.enabled) }}
{{- fail "api.replicaCount > 1 requires persistence.enabled=true and a RWX artifact volume" }}
{{- end }}
{{- if and (gt (int .Values.api.replicaCount) 1) .Values.persistence.enabled (not (has "ReadWriteMany" .Values.persistence.accessModes)) }}
{{- fail "api.replicaCount > 1 requires ReadWriteMany persistence access" }}
{{- end }}
{{- if and .Values.worker.enabled (not .Values.persistence.enabled) }}
{{- fail "worker.enabled requires persistence.enabled=true so API and Worker share artifacts" }}
{{- end }}
{{- if and .Values.ingress.enabled (eq (len .Values.ingress.hosts) 0) }}
{{- fail "ingress.enabled requires at least one ingress.hosts entry" }}
{{- end }}
{{- if and .Values.autoscaling.enabled (gt (int .Values.autoscaling.minReplicas) (int .Values.autoscaling.maxReplicas)) }}
{{- fail "autoscaling.minReplicas cannot exceed autoscaling.maxReplicas" }}
{{- end }}
{{- if and .Values.pdb.enabled (not .Values.autoscaling.enabled) (gt (int .Values.pdb.minAvailable) (int .Values.api.replicaCount)) }}
{{- fail "pdb.minAvailable cannot exceed api.replicaCount when autoscaling is disabled" }}
{{- end }}
{{- range $key, $_ := .Values.runtime.config }}
{{- if and (regexMatch "(?i)(secret|password|api[_-]?key|token|access[_-]?key)" $key) (not (hasSuffix "_FILE" $key)) }}
{{- fail (printf "runtime.config.%s looks like a secret; use runtime.existingSecret or a mounted *_FILE instead" $key) }}
{{- end }}
{{- end }}
{{- end }}

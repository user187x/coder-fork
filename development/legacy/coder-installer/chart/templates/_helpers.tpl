{{- define "ci.labels" -}}
app.kubernetes.io/part-of: coder
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/instance: {{ .Release.Name }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end }}

{{- define "ci.accessURL" -}}
{{- printf "https://%s" (required "coderHost is required" .Values.coderHost) -}}
{{- end }}

{{/* Coder's Service and Deployment: the coder chart names both after itself. */}}
{{- define "ci.coderService" -}}
{{- (.Values.coder | default dict).nameOverride | default "coder" -}}
{{- end }}

{{- define "cp.labels" -}}
app.kubernetes.io/part-of: coder
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/instance: {{ .Release.Name }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end }}

{{/* Hostnames and URLs, derived from global.domain unless set explicitly. */}}
{{- define "cp.domain" -}}
{{- required "global.domain is required (e.g. corp.example.com)" .Values.global.domain -}}
{{- end }}
{{- define "cp.coderHost" -}}
{{- .Values.coderHost | default (printf "coder.%s" (include "cp.domain" .)) -}}
{{- end }}
{{- define "cp.wildcardHost" -}}
{{- if ne .Values.wildcardHost "-" }}{{ .Values.wildcardHost | default (printf "*.%s" (include "cp.domain" .)) }}{{ end -}}
{{- end }}
{{- define "cp.accessURL" -}}
{{- printf "https://%s" (include "cp.coderHost" .) -}}
{{- end }}
{{- define "cp.coderService" -}}
{{- if .Values.attach.enabled -}}
{{- .Values.attach.coderService | default "coder" -}}
{{- else -}}
{{- (.Values.coder | default dict).nameOverride | default "coder" -}}
{{- end -}}
{{- end }}
{{- define "cp.coderDeployment" -}}
{{- if .Values.attach.enabled }}{{ .Values.attach.coderDeployment | default "coder" }}{{ else }}{{ include "cp.coderService" . }}{{ end -}}
{{- end }}

{{/* nodeSelector block for the pods this chart renders itself (global.nodeSelector); indent with nindent. */}}
{{- define "cp.nodeSelector" -}}
{{- with .Values.global.nodeSelector }}
nodeSelector:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- end }}
{{- define "cp.coderServiceURL" -}}
{{- printf "http://%s.%s.svc.cluster.local" (include "cp.coderService" .) .Release.Namespace -}}
{{- end }}
{{- define "cp.workspaceNamespace" -}}
{{- .Values.workspaces.namespace | default .Release.Namespace -}}
{{- end }}

{{/*
Image reference for images this chart renders itself. With global.imageRegistry set, the source registry host is
replaced by it and the path is kept, which is exactly where ./install images pushes them:
  ghcr.io/cloudnative-pg/postgresql:18.4 -> <imageRegistry>/cloudnative-pg/postgresql:18.4
  docker.io/library/python:3.13-alpine   -> <imageRegistry>/library/python:3.13-alpine
Usage: include "cp.image" (list $ "repository:tag")
*/}}
{{- define "cp.image" -}}
{{- $root := index . 0 -}}
{{- $ref := index . 1 -}}
{{- $reg := $root.Values.global.imageRegistry | default "" | trimSuffix "/" -}}
{{- if $reg -}}
{{- $parts := splitList "/" $ref -}}
{{- $first := first $parts -}}
{{- if and (gt (len $parts) 1) (or (contains "." $first) (contains ":" $first) (eq $first "localhost")) -}}
{{- printf "%s/%s" $reg (join "/" (rest $parts)) -}}
{{- else if eq (len $parts) 1 -}}
{{- printf "%s/library/%s" $reg $ref -}}
{{- else -}}
{{- printf "%s/%s" $reg $ref -}}
{{- end -}}
{{- else -}}
{{- $ref -}}
{{- end -}}
{{- end }}

{{- define "cp.imagePullSecrets" -}}
{{- with .Values.global.imagePullSecrets }}
imagePullSecrets:
{{- range . }}
  - name: {{ . | quote }}
{{- end }}
{{- end }}
{{- end }}

{{/* The Coder image the chart's own Jobs run (the coder CLI + curl): the one the coder subchart runs. */}}
{{- define "cp.coderImage" -}}
{{- $img := (.Values.coder.coder | default dict).image | default dict -}}
{{- $repo := $img.repo | default "ghcr.io/coder/coder" -}}
{{- $tag := $img.tag | default (printf "v%s" .Chart.AppVersion) -}}   {{- /* appVersion = the bundled Coder version */}}
{{- printf "%s:%s" $repo $tag -}}
{{- end }}

{{- define "cp.oidcSecretName" -}}
{{- .Values.oidc.existingSecret | default "keycloaking-coder-oidc" -}}
{{- end }}

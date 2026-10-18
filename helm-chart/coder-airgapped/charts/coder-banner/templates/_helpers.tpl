{{/* Everything lives next to Coder's Service, so default to the namespace the release is installed into. */}}
{{- define "coder-banner.namespace" -}}
{{ .Values.namespace | default .Release.Namespace }}
{{- end -}}

{{/* registry/repository:tag, or registry/repository@digest when a digest is pinned. */}}
{{- define "coder-banner.image" -}}
{{- $image := .Values.image -}}
{{- $ref := ternary (printf "%s/%s" $image.registry $image.repository) $image.repository (not (empty $image.registry)) -}}
{{- if $image.digest -}}{{ printf "%s@%s" $ref $image.digest }}{{- else -}}{{ printf "%s:%s" $ref $image.tag }}{{- end -}}
{{- end -}}

{{- define "coder-banner.labels" -}}
app.kubernetes.io/name: coder-banner
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/part-of: coder
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{/* What each banner choice may be: the lists of files/server.py (LEVELS, EFFECTS, SPEEDS, FONTS, ALIGNS, PICTURE_FITS,
     OPEN_EFFECTS, CLOSE_EFFECTS), which the service also checks what admins publish against.
     development/tools/verify/chart_checks.py fails when the two differ. */}}
{{- define "coder-banner.choices" -}}
level: [info, success, warning, critical]
effect: [none, typewriter, fade, rise, wave, bounce, flip, shake, pulse, rainbow, starwars, pewpew, flap, shimmer, kitt]
speed: [slow, normal, fast]
font: [default, sans, serif, condensed, geist-mono, ibm-plex-mono, jetbrains-mono, fira-code, source-code-pro]
align: [left, center, right]
pictureFit: [center, span, tile, fill, stretch]
openEffect: [none, fade, rolldown, kitt, crt, glitch]
closeEffect: [none, pixelate, rollup, fade, wipe, matrix, gaussian, evaporate, kitt]
{{- end -}}

{{/* banner.<key>, one of its choices (empty: the default); a typo fails the install with a clear message instead of a
     banner the service would not draw.   include "coder-banner.choice" (list . "effect" "none") */}}
{{- define "coder-banner.choice" -}}
{{- $root := index . 0 -}}{{- $key := index . 1 -}}
{{- $allowed := get (include "coder-banner.choices" $root | fromYaml) $key -}}
{{- $value := get $root.Values.banner $key | default (index . 2) -}}
{{- if not (has $value $allowed) -}}
{{- fail (printf "banner.%s %q is not valid: use one of %s" $key (toString $value) (join ", " $allowed)) -}}
{{- end -}}
{{- $value -}}
{{- end -}}

{{/* banner.<key>, which must be true or false.   include "coder-banner.bool" (list . "repeat") */}}
{{- define "coder-banner.bool" -}}
{{- $value := get (index . 0).Values.banner (index . 1) -}}
{{- if not (kindIs "bool" $value) -}}
{{- fail (printf "banner.%s must be true or false, not %q" (index . 1) (toString $value)) -}}
{{- end -}}
{{- $value -}}
{{- end -}}

{{/* Stable banner id: re-shows for users who dismissed an older banner whenever the content changes. A picture or a
     countdown counts only when there is one, so banners without them keep the ids they had. */}}
{{- define "coder-banner.id" -}}
{{- $b := .Values.banner -}}
{{- $parts := list $b.level $b.title $b.message $b.linkText $b.linkUrl -}}
{{- range $key := list "picture" "countdown" -}}
{{- if get $b $key -}}{{- $parts = append $parts (printf "%s=%s" $key (get $b $key)) -}}{{- end -}}
{{- end -}}
{{- $b.id | default (join "|" $parts | sha256sum | trunc 12) -}}
{{- end -}}

{{/* banner.<key>, a whole number from low to high (empty: the default), as files/server.py takes it (CRAWL_SPEED,
     CRAWL_TILT); out of range fails the install with a clear message.
     include "coder-banner.number" (list . "crawlSpeed" 4 60 15) */}}
{{- define "coder-banner.number" -}}
{{- $root := index . 0 -}}{{- $key := index . 1 -}}{{- $low := index . 2 -}}{{- $high := index . 3 -}}
{{- $value := get $root.Values.banner $key -}}
{{- if eq (toString $value) "" -}}{{- $value = index . 4 -}}{{- end -}}
{{- if not (regexMatch "^[0-9]+$" (toString $value)) -}}
  {{- fail (printf "banner.%s must be a whole number from %d to %d (got %v)" $key $low $high $value) -}}
{{- end -}}
{{- $n := int $value -}}
{{- if or (lt $n $low) (gt $n $high) -}}
  {{- fail (printf "banner.%s must be from %d to %d (got %d)" $key $low $high $n) -}}
{{- end -}}
{{- $n -}}
{{- end -}}

#!/usr/bin/env python3
"""Applies the coder-airgapped "platform" patch to an (upstream) chart directory, in place.

The patch lets the chart's values hold templates, e.g.  host: 'coder.{{ .Values.global.domain }}'
so one set of `global:` values (set from the umbrella chart or an Argo CD Application) can drive every subchart.

  * templates/_platform.tpl defines <prefix>.platform.values: it walks the chart's values (except `global`) and renders
    every string containing "{{" with `tpl`, in place (idempotent). Maps inside lists are walked too; plain strings
    inside lists are not. Then it applies global.imagePullSecrets where the chart's own pull-secret list is empty,
    and (coder chart only) appends coder.extraEnv to coder.env.
  * every template file (*.yaml, NOTES.txt) gets  {{- include "<prefix>.platform.values" . -}}  as its first line.
  * checksum/* annotations are quoted (an all-digit hash would otherwise be read as a number and rejected).
  * Chart.yaml's version gets a "-platform.N" suffix.

Usage: apply-platform-patch.py CHART_DIR PREFIX PULL_SECRETS_PATH [--extra-env] [--suffix platform.1]
  PULL_SECRETS_PATH  dotted path of the chart's pull-secret list, e.g. coder.image.pullSecrets or imagePullSecrets
"""
import argparse, os, re, sys

WALK = r'''{{- define "%(p)s.platform.walk" -}}
{{- $node := index . 0 -}}{{- $ctx := index . 1 -}}
{{- if kindIs "map" $node -}}
{{- range $k, $v := $node -}}
{{- if and (kindIs "string" $v) (contains "{{" $v) -}}
{{- $_ := set $node $k (tpl $v $ctx) -}}
{{- else if or (kindIs "map" $v) (kindIs "slice" $v) -}}
{{- include "%(p)s.platform.walk" (list $v $ctx) -}}
{{- end -}}
{{- end -}}
{{- else if kindIs "slice" $node -}}
{{- range $node -}}
{{- if or (kindIs "map" .) (kindIs "slice" .) -}}
{{- include "%(p)s.platform.walk" (list . $ctx) -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}
'''

ENTRY = r'''{{- define "%(p)s.platform.values" -}}
{{- range $k, $v := .Values -}}
{{- if ne $k "global" -}}
{{- if and (kindIs "string" $v) (contains "{{" $v) -}}
{{- $_ := set $.Values $k (tpl $v $) -}}
{{- else if or (kindIs "map" $v) (kindIs "slice" $v) -}}
{{- include "%(p)s.platform.walk" (list $v $) -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- $g := .Values.global | default dict -}}
{{- if and $g.imagePullSecrets (not %(ps)s) -}}
{{- $_ := set %(ps_parent)s "%(ps_key)s" $g.imagePullSecrets -}}
{{- end -}}
%(extra)s{{- end -}}
'''

EXTRA_ENV = r'''{{- if and .Values.coder.extraEnv (not (hasKey .Values.coder "platformExtraEnvMerged")) -}}
{{- $_ := set .Values.coder "env" (concat (.Values.coder.env | default list) .Values.coder.extraEnv) -}}
{{- $_ := set .Values.coder "platformExtraEnvMerged" true -}}
{{- end -}}
'''

HEADER = '''{{/*
coder-airgapped platform patch (%(suffix)s), applied by development/tools/apply-platform-patch.py.
String values may hold templates, e.g. host: 'coder.{{ .Values.global.domain }}'. Every template of this chart starts
with an include of "%(p)s.platform.values", which renders those strings in place (idempotent, so the repeated calls
are harmless), then applies global.imagePullSecrets to %(ps)s when that is empty.%(extra_doc)s
*/}}
'''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("chart"); ap.add_argument("prefix"); ap.add_argument("pull_secrets")
    ap.add_argument("--extra-env", action="store_true"); ap.add_argument("--suffix", default="platform.1")
    a = ap.parse_args()
    parts = a.pull_secrets.split(".")
    ps = ".Values." + a.pull_secrets
    ps_parent = ".Values" + ("." + ".".join(parts[:-1]) if len(parts) > 1 else "")
    d = dict(p=a.prefix, ps=ps, ps_parent=ps_parent, ps_key=parts[-1], suffix=a.suffix,
             extra=EXTRA_ENV if a.extra_env else "",
             extra_doc="\nAlso appends coder.extraEnv (not an upstream value) to coder.env." if a.extra_env else "")
    tdir = os.path.join(a.chart, "templates")
    with open(os.path.join(tdir, "_platform.tpl"), "w") as f:
        f.write(HEADER % d + WALK % d + "\n" + ENTRY % d)
    line = '{{- include "%s.platform.values" . -}}\n' % a.prefix
    for name in sorted(os.listdir(tdir)):
        if not (name.endswith(".yaml") or name == "NOTES.txt"):
            continue
        p = os.path.join(tdir, name)
        s = open(p).read()
        if not s.startswith(line):
            s = line + s
        s = re.sub(r'(checksum/[\w-]+: \{\{)(.*?)(\}\})',
                   lambda m: m.group(0) if "quote" in m.group(2) else m.group(1) + m.group(2).rstrip() + " | quote " + m.group(3), s)
        open(p, "w").write(s)
    cp = os.path.join(a.chart, "Chart.yaml")
    s = open(cp).read()
    s, n = re.subn(r"^version:\s*['\"]?([0-9]+\.[0-9]+\.[0-9]+)(-[^'\"\s]+)?['\"]?\s*$",
                   lambda m: "version: %s-%s" % (m.group(1), a.suffix), s, flags=re.M)
    if n != 1:
        sys.exit("could not set the version in " + cp)
    open(cp, "w").write(s)
    print("patched", a.chart)


if __name__ == "__main__":
    main()

#!/usr/bin/env bash
# Saves what the chart can't recreate, into a directory ./install.sh --backup (or ./restore.sh) takes:
#   secrets.values.yaml   the Secrets Coder reads (certificate, CAs, OIDC client secret, first owner) as chart values
#   coder-db.dump         Coder's database (users, templates, workspaces, settings, uploaded logos): pg_dump -Fc
#   ui-updates.sqlite     the add-on service's data (receipts, classification, chat, quota, Keycloak service account)
#   configmaps.yaml       state the services keep in ConfigMaps (the banner, Keycloak view backups)
#   coder-image.tar       with --image: the forked Coder image (about 1 GB), for a cluster that can't get it otherwise
# The directory holds secrets: keep it private (it is created 0700, the secrets file 0600) and outside Git.
#
# Usage: ./backup.sh [--image] [--namespace NS] [DIR]     (default DIR: ./backups/<date-time>)
set -euo pipefail
cd "$(dirname "$0")"

NS=coder
IMAGE=0
DIR=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --image) IMAGE=1; shift ;;
    --namespace|-n) NS="$2"; shift 2 ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) DIR="$1"; shift ;;
  esac
done
DIR="${DIR:-backups/$(date +%Y%m%d-%H%M%S)}"
umask 077
mkdir -p "$DIR"
k() { kubectl -n "$NS" "$@"; }

echo "==> Secrets -> $DIR/secrets.values.yaml"
python3 - "$NS" >"$DIR/secrets.values.yaml" <<'EOF'
import base64, json, subprocess, sys
ns = sys.argv[1]
def get(name, key):
    p = subprocess.run(["kubectl", "-n", ns, "get", "secret", name, "-o", "json"], capture_output=True, text=True)
    if p.returncode:
        print("  missing: secret/%s" % name, file=sys.stderr); return ""
    v = (json.loads(p.stdout).get("data") or {}).get(key)
    return base64.b64decode(v).decode() if v else ""
vals = {
    "tlsCert": get("tls-wild-cert", "tls.crt"), "tlsKey": get("tls-wild-cert", "tls.key"),
    "caCert": get("tls-ca", "ca.crt"), "keycloakCaCert": get("keycloaking-coder-ca", "ca.crt"),
    "oidcClientSecret": get("keycloaking-coder-oidc", "client-secret"),
    "adminEmail": get("coder-init-auth", "email"), "adminPassword": get("coder-init-auth", "password"),
}
print("# The Secrets of the Coder installation, as coder-installer chart values. Keep this file private.")
print("secrets:")
for k, v in vals.items():
    if "\n" in v.strip():
        print("  %s: |" % k)
        for line in v.strip().split("\n"):
            print("    " + line)
    else:
        print("  %s: %s" % (k, json.dumps(v)))
EOF

echo "==> Coder's database -> $DIR/coder-db.dump"
primary=$(k get pod -l cnpg.io/cluster=coder-db,cnpg.io/instanceRole=primary -o name | head -1)
[[ -n "$primary" ]] || { echo "no primary instance of coder-db" >&2; exit 1; }
k exec "$primary" -c postgres -- pg_dump -Fc -d app >"$DIR/coder-db.dump"
if command -v pg_restore >/dev/null; then pg_restore -l "$DIR/coder-db.dump" >/dev/null || { echo "the dump is not readable" >&2; exit 1; }; fi
echo "   $(du -h "$DIR/coder-db.dump" | cut -f1)"

echo "==> Add-on service data -> $DIR/ui-updates.sqlite"
# A consistent copy with SQLite's backup API, next to the database on the service's volume, then streamed out.
k exec deploy/coder-ui-updates -- python3 -c '
import sqlite3; s = sqlite3.connect("/data/receipts.sqlite"); d = sqlite3.connect("/data/.backup.sqlite")
s.backup(d); d.close(); s.close()'
k exec deploy/coder-ui-updates -- cat /data/.backup.sqlite >"$DIR/ui-updates.sqlite"
k exec deploy/coder-ui-updates -- rm -f /data/.backup.sqlite
echo "   $(du -h "$DIR/ui-updates.sqlite" | cut -f1)"

echo "==> ConfigMap state -> $DIR/configmaps.yaml"
for cm in coder-banner-state coder-banner-content keycloaking-coder-backup; do
  k get configmap "$cm" -o json 2>/dev/null || true
done | python3 -c '
import json, sys, yaml
dec = json.JSONDecoder(); text = sys.stdin.read(); i = 0; out = []
while i < len(text):
    while i < len(text) and text[i].isspace(): i += 1
    if i >= len(text): break
    obj, i = dec.raw_decode(text, i)
    out.append({"apiVersion": "v1", "kind": "ConfigMap",
                "metadata": {"name": obj["metadata"]["name"], "labels": obj["metadata"].get("labels") or {}},
                **{k: obj[k] for k in ("data", "binaryData") if obj.get(k)}})
yaml.safe_dump_all(out, sys.stdout, sort_keys=False)' >"$DIR/configmaps.yaml"
echo "   $(grep -c '^kind: ConfigMap' "$DIR/configmaps.yaml") ConfigMaps"

if [[ $IMAGE -eq 1 ]]; then
  img="$(python3 -c 'import yaml; c=yaml.safe_load(open("chart/values.yaml"))["coder"]["coder"]["image"]; print(c["repo"]+":"+c["tag"])')"
  echo "==> $img -> $DIR/coder-image.tar"
  if docker image inspect "$img" >/dev/null 2>&1; then docker save "$img" -o "$DIR/coder-image.tar"
  else minikube image save "$img" "$DIR/coder-image.tar"; fi
fi

{
  echo "taken: $(date -Iseconds)"
  echo "context: $(kubectl config current-context)"
  echo "coder: $(k get deploy coder -o jsonpath='{.spec.template.spec.containers[0].image}')"
} >"$DIR/info.txt"
chmod 600 "$DIR"/*
echo
echo "Backup in $DIR. Reinstall with: ./install.sh --backup $DIR"

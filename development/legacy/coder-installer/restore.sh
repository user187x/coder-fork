#!/usr/bin/env bash
# Puts a ./backup.sh directory's data into an installed release (./install.sh --backup runs it for you):
#   coder-db.dump      replaces Coder's database (Coder is stopped meanwhile)
#   ui-updates.sqlite  replaces the add-on service's data (the service restarts)
#   configmaps.yaml    the banner's state and the Keycloak view's backups
# Everything the database had is replaced: users, templates, workspaces (their records; the workspace home volumes
# are not part of a backup), settings, uploaded logos.
#
# Usage: ./restore.sh [--namespace NS] [--yes] DIR
set -euo pipefail
cd "$(dirname "$0")"

NS=coder
YES=0
DIR=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --namespace|-n) NS="$2"; shift 2 ;;
    --yes|-y) YES=1; shift ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) DIR="$1"; shift ;;
  esac
done
[[ -n "$DIR" && -d "$DIR" ]] || { echo "usage: $0 [--namespace NS] [--yes] BACKUP_DIR" >&2; exit 2; }
k() { kubectl -n "$NS" "$@"; }
k get deploy coder >/dev/null || { echo "Coder is not installed in namespace $NS (run ./install.sh first)" >&2; exit 1; }

if [[ $YES -eq 0 && -t 0 ]]; then
  read -r -p "Replace Coder's data in $(kubectl config current-context)/$NS with $DIR? [y/N] " a
  [[ "$a" == [yY]* ]] || exit 1
fi

if [[ -f "$DIR/coder-db.dump" ]]; then
  echo "==> Coder's database <- $DIR/coder-db.dump"
  k scale deploy/coder --replicas=0
  k rollout status deploy/coder --timeout=5m
  k wait pod -l app.kubernetes.io/name=coder --for=delete --timeout=5m 2>/dev/null || true
  primary=$(k get pod -l cnpg.io/cluster=coder-db,cnpg.io/instanceRole=primary -o name | head -1)
  [[ -n "$primary" ]] || { echo "no primary instance of coder-db" >&2; exit 1; }
  # The dump's objects are all owned by app (the database owner): drop Coder's schemas and replay it as the
  # superuser, which keeps those owners. Replicas follow the primary.
  k exec "$primary" -c postgres -- psql -d app -v ON_ERROR_STOP=1 -q -c \
    'DROP SCHEMA IF EXISTS coder_ui_updates CASCADE; DROP SCHEMA public CASCADE;
     CREATE SCHEMA public AUTHORIZATION pg_database_owner; GRANT USAGE ON SCHEMA public TO PUBLIC;'
  k exec -i "$primary" -c postgres -- pg_restore -d app --exit-on-error --single-transaction <"$DIR/coder-db.dump"
  k scale deploy/coder --replicas=1
  k rollout status deploy/coder --timeout=10m
fi

if [[ -f "$DIR/ui-updates.sqlite" ]]; then
  echo "==> Add-on service data <- $DIR/ui-updates.sqlite"
  k exec -i deploy/coder-ui-updates -- sh -c 'cat > /data/.restore.sqlite' <"$DIR/ui-updates.sqlite"
  k exec deploy/coder-ui-updates -- python3 -c '
import os, sqlite3
sqlite3.connect("/data/.restore.sqlite").execute("PRAGMA integrity_check").fetchone()
for f in ("/data/receipts.sqlite-wal", "/data/receipts.sqlite-shm"):
    if os.path.exists(f): os.remove(f)
os.replace("/data/.restore.sqlite", "/data/receipts.sqlite")'
  k rollout restart deploy/coder-ui-updates
  k rollout status deploy/coder-ui-updates --timeout=5m
fi

if [[ -s "$DIR/configmaps.yaml" ]]; then
  echo "==> ConfigMaps <- $DIR/configmaps.yaml"
  k apply --server-side --force-conflicts -f "$DIR/configmaps.yaml"
  k rollout restart deploy/coder-banner >/dev/null 2>&1 || true
fi

echo "Restored $DIR."

#!/usr/bin/env bash
# Installs Coder as it runs on the minikube cluster (see README.md):
#   1. checks the prerequisites (Traefik's Gateway and middlewares, the storage class, Keycloak)
#   2. installs the CloudNativePG operator when the cluster doesn't have it (operators/, release cloudnative-pg)
#   3. makes sure minikube has the forked Coder image (from Docker, or <backup>/coder-image.tar)
#   4. creates the add-ons' default logo / avatar ConfigMaps (defaults/; too large for Helm's release record)
#   5. installs the chart (release coder) and the announcement banner (release coder-banner)
#   6. with --backup, restores the backup's data (./restore.sh)
#
# Usage: ./install.sh [--backup DIR] [-f VALUES]... [--namespace NS] [--skip-image]
#   --backup DIR    a ./backup.sh directory: its secrets.values.yaml is used, its data restored afterwards
#   -f VALUES       extra values for the chart (e.g. a secrets file); repeatable
set -euo pipefail
cd "$(dirname "$0")"

NS=coder
BACKUP=""
VALUES=()
SKIP_IMAGE=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --backup) BACKUP="$2"; shift 2 ;;
    -f|--values) VALUES+=(-f "$2"); shift 2 ;;
    --namespace|-n) NS="$2"; shift 2 ;;
    --skip-image) SKIP_IMAGE=1; shift ;;
    -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

die() { echo "ERROR: $*" >&2; exit 1; }
step() { echo; echo "==> $*"; }
val() { python3 -c 'import sys,yaml; v=yaml.safe_load(open(sys.argv[1]));
for k in sys.argv[2].split("."): v=v[k]
print(v)' chart/values.yaml "$1"; }

IMAGE="$(val coder.coder.image.repo):$(val coder.coder.image.tag)"
GW_NAME=$(val gateway.name); GW_NS=$(val gateway.namespace)
SC=$(val postgres.storage.storageClass)

if [[ -n "$BACKUP" ]]; then
  [[ -d "$BACKUP" ]] || die "no backup directory $BACKUP"
  [[ -f "$BACKUP/secrets.values.yaml" ]] && VALUES=(-f "$BACKUP/secrets.values.yaml" "${VALUES[@]}")
fi

step "Cluster: $(kubectl config current-context)"
kubectl get --raw /version >/dev/null || die "the cluster is not reachable"

step "Prerequisites"
kubectl get crd httproutes.gateway.networking.k8s.io >/dev/null 2>&1 || die "Gateway API CRDs are missing (install Traefik v3 with the Gateway API provider)"
kubectl get crd middlewares.traefik.io >/dev/null 2>&1 || die "Traefik's CRDs are missing (middlewares.traefik.io)"
kubectl -n "$GW_NS" get gateway "$GW_NAME" >/dev/null 2>&1 || die "Gateway $GW_NS/$GW_NAME does not exist"
kubectl get storageclass "$SC" >/dev/null 2>&1 || die "StorageClass $SC does not exist (postgres.storage.storageClass)"
if ! kubectl get deploy -A -o jsonpath='{range .items[*].spec.template.spec.containers[*]}{.args}{"\n"}{end}' | grep -q 'experimental.plugins.rewrite-body'; then
  echo "WARNING: Traefik's rewrite-body plugin is not enabled: the banner chart's middlewares need it"
fi
kubectl -n "$(val uiUpdates.keycloakNamespace)" get svc >/dev/null 2>&1 || echo "WARNING: Keycloak's namespace $(val uiUpdates.keycloakNamespace) is missing: OIDC sign-in needs Keycloak"
# Objects of another installation (e.g. the coder Argo CD Application) can't be taken over by Helm.
owner=$(kubectl -n "$NS" get deploy coder -o jsonpath='{.metadata.annotations.meta\.helm\.sh/release-name}' 2>/dev/null || true)
if kubectl -n "$NS" get deploy coder >/dev/null 2>&1 && [[ "$owner" != "coder" || -n "$(kubectl -n "$NS" get deploy coder -o jsonpath='{.metadata.annotations.argocd\.argoproj\.io/tracking-id}' 2>/dev/null)" ]]; then
  die "Coder in namespace $NS belongs to another installation (Argo CD app or release '$owner'). Remove it first (README.md: Reinstalling over the current cluster)."
fi
for s in tls-wild-cert tls-ca keycloaking-coder-ca keycloaking-coder-oidc coder-init-auth; do
  if ! kubectl -n "$NS" get secret "$s" >/dev/null 2>&1 && [[ ${#VALUES[@]} -eq 0 ]]; then
    die "Secret $s is missing and no values were given: pass --backup DIR (or -f secrets.values.yaml)"
  fi
done

step "CloudNativePG operator"
if kubectl get crd clusters.postgresql.cnpg.io >/dev/null 2>&1; then
  echo "already installed"
else
  helm upgrade --install cloudnative-pg operators/cloudnative-pg-0.29.0.tgz -n cnpg-system --create-namespace --wait --timeout 10m
fi

step "Coder image $IMAGE"
if [[ $SKIP_IMAGE -eq 1 ]]; then
  echo "skipped"
elif minikube image ls 2>/dev/null | grep -qx "$IMAGE"; then
  echo "minikube has it"
elif docker image inspect "$IMAGE" >/dev/null 2>&1; then
  minikube image load "$IMAGE"
elif [[ -n "$BACKUP" && -f "$BACKUP/coder-image.tar" ]]; then
  minikube image load "$BACKUP/coder-image.tar"
else
  die "$IMAGE is in neither minikube nor Docker: load it (README.md: The Coder image), or pass --skip-image when the nodes can pull it"
fi

step "Namespace $NS and default images"
kubectl create namespace "$NS" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
for kind in logo avatar; do
  f=$(ls defaults/"$kind".* 2>/dev/null | head -1 || true)
  [[ -n "$f" ]] || continue
  kubectl create configmap "coder-ui-updates-default-$kind" -n "$NS" --from-file="$f" --dry-run=client -o yaml \
    | kubectl label --local -f - -o yaml app.kubernetes.io/name=coder-ui-updates app.kubernetes.io/part-of=coder \
    | kubectl apply --server-side --force-conflicts -f - >/dev/null
  echo "coder-ui-updates-default-$kind <- $f"
done

step "Coder (release coder)"
helm upgrade --install coder chart -n "$NS" "${VALUES[@]}" --wait --timeout 20m

step "Announcement banner (release coder-banner)"
helm upgrade --install coder-banner banner/coder-banner-0.4.1.tgz -n "$NS" -f banner/values.yaml --wait --timeout 10m

if [[ -n "$BACKUP" ]]; then
  step "Restoring $BACKUP"
  ./restore.sh --namespace "$NS" "$BACKUP"
fi

step "Done"
kubectl -n "$NS" get pods
echo
echo "Coder: https://$(val coderHost)   (template push log: kubectl -n $NS logs job/coder-workspace-push-template)"

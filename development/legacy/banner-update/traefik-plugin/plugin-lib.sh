# Shared by enable-plugin / disable-plugin (sourced, not executed).
#
# AIR-GAPPED plugin (enable-plugin --airgap, disable-plugin): an ADDITIVE PATCH of the live Traefik Deployment.
#   It reads the Deployment that is running, adds exactly three things (one argument, one volumeMount, one volume;
#   plus the ConfigMap that carries the plugin source), removes a conflicting download entry for the same plugin,
#   and changes nothing else. It works with whatever Traefik / Traefik-chart version is running, needs no chart and
#   no Helm, never re-renders the Deployment (so hand-made settings are not reverted), and never touches the
#   Gateway, the Gateway API / Traefik CRDs, or any HTTPRoute, Middleware or Service. It refuses when Traefik is
#   managed by Argo CD / Flux (they would revert it) or configured from a file (CLI arguments would be ignored).
#
# DOWNLOAD plugin (enable-plugin without --airgap; connected clusters only): renders the Traefik chart with the
#   release's own values + overlay and applies that Deployment (see render / apply_and_wait below).
#
# Why not `helm upgrade`? The live Gateway can have listeners that differ from what the Traefik chart renders, and
# Helm 4 applies server-side, so a later `helm upgrade traefik` conflicts with the field manager used here and marks
# the release "failed": run it with --force-conflicts and then ./enable-plugin --airgap again.
#
# Environment:
#   TRAEFIK_NAMESPACE      namespace of Traefik                      (default: traefik)
#   TRAEFIK_RELEASE        Helm release name                         (default: traefik)
#   TRAEFIK_DEPLOYMENT     name of Traefik's Deployment              (default: the release name)
#   TRAEFIK_CONTAINER      container to patch                        (default: "traefik", else the only one)
#   TRAEFIK_ALLOW_GITOPS=1 patch even though Argo CD / Flux manages the Deployment (it will probably be reverted)
#   TRAEFIK_ALLOW_OLD=1    patch even though Traefik is older than v3
#   download variant only: TRAEFIK_CHART (chart reference / .tgz / directory), TRAEFIK_CHART_VERSION
NS="${TRAEFIK_NAMESPACE:-traefik}"
RELEASE="${TRAEFIK_RELEASE:-traefik}"
DEPLOY="${TRAEFIK_DEPLOYMENT:-${RELEASE}}"
CHART="${TRAEFIK_CHART:-traefik/traefik}"
PLUGIN_NAME="rewrite-body"   # the key under experimental.plugins / experimental.localPlugins (see the overlays)
MODULE="github.com/traefik/plugin-rewritebody"
PLUGIN_ARG="--experimental.localPlugins.${PLUGIN_NAME}.moduleName=${MODULE}"
PLUGIN_VOL="${PLUGIN_NAME}"                          # volume + mount name (the same the Traefik chart's inlinePlugin uses)
PLUGIN_MOUNT="/plugins-local/src/${MODULE}"
PLUGIN_CM="${DEPLOY}-local-plugin-${PLUGIN_NAME}"    # named like the chart names it: one and the same object on a chart-managed Traefik
WORK=$(mktemp -d)
trap 'rm -rf "${WORK}"' EXIT
. ./plugin-health.sh   # plugin_health: what Traefik itself says about its plugins (callers cd here first)

deploy_revision() { kubectl -n "${NS}" get deployment "${DEPLOY}" -o jsonpath='{.metadata.annotations.deployment\.kubernetes\.io/revision}' 2>/dev/null || true; }
kj() { kubectl -n "${NS}" get deployment "${DEPLOY}" -o jsonpath="$1"; }   # read one thing from the live Deployment

# maxUnavailable=0 (the Traefik chart's default): the old pod keeps serving until the new one is Ready, so a broken
# change never takes Traefik down - it is reverted instead.
wait_for_rollout() {
 if ! kubectl -n "${NS}" rollout status deployment/${DEPLOY} --timeout=180s; then
  echo "Traefik did not become ready - rolling back." >&2
  kubectl -n "${NS}" logs deployment/${DEPLOY} --tail=20 >&2 || true
  kubectl -n "${NS}" rollout undo deployment/${DEPLOY}
  return 1
 fi
}

# After enabling: roll back if Traefik came up but disabled its plugins (a rollout only proves it is Ready; see
# plugin-health.sh). Only a rollout this run started is undone: if nothing changed there is nothing to undo, and
# `rollout undo` would wrongly step Traefik back to an older revision.
verify_plugins_loaded() {
 local out state
 out=$(plugin_health "${NS}" "${DEPLOY}"); state=${out%%$'\n'*}
 case "${state}" in
  loaded) echo "Traefik reports: plugins loaded." ;;
  disabled)
   echo "Traefik is up but DISABLED its plugins: $(sed -n 2p <<<"${out}")" >&2
   if [[ "$(deploy_revision)" != "${REVISION_BEFORE:-}" ]]; then
    echo "Rolling back so the banner routes are not left half-working." >&2
    kubectl -n "${NS}" rollout undo deployment/${DEPLOY} >&2
   else
    echo "This run changed nothing, so nothing is rolled back: see the log line above and: kubectl -n ${NS} logs deploy/${DEPLOY}" >&2
   fi
   return 1 ;;
  *) echo "WARNING: could not confirm from Traefik's log that the plugin loaded - check: kubectl -n ${NS} logs deploy/${DEPLOY} | grep -i plugin" >&2 ;;
 esac
}

# ============================================================================================ air-gapped: additive patch

# Reads the live Deployment: which container, its arguments, volumeMounts and volumes. Sets CIDX, ARGS, MOUNTS, VOLS.
load_live() {
 local names want n
 names=$(kj '{range .spec.template.spec.containers[*]}{.name}{"\n"}{end}' 2>&1) || { echo "cannot read deployment/${DEPLOY} in namespace ${NS}: ${names}" >&2; return 1; }
 [[ -n "${names}" ]] || { echo "deployment/${DEPLOY} in namespace ${NS} has no containers?" >&2; return 1; }
 n=$(grep -c . <<<"${names}")
 if [[ -n "${TRAEFIK_CONTAINER:-}" ]]; then want=${TRAEFIK_CONTAINER}
 elif grep -qx traefik <<<"${names}"; then want=traefik
 elif (( n == 1 )); then want=${names%%$'\n'*}
 else echo "deployment/${DEPLOY} has several containers (${names//$'\n'/, }): set TRAEFIK_CONTAINER" >&2; return 1; fi
 CIDX=$(awk -v w="${want}" '$0 == w { print NR - 1; exit }' <<<"${names}")
 [[ -n "${CIDX}" ]] || { echo "container '${want}' not found in deployment/${DEPLOY}" >&2; return 1; }
 mapfile -t ARGS < <(kj "{range .spec.template.spec.containers[${CIDX}].args[*]}{@}{\"\\n\"}{end}")
 mapfile -t MOUNTS < <(kj "{range .spec.template.spec.containers[${CIDX}].volumeMounts[*]}{.name}{\"\\n\"}{end}")
 mapfile -t VOLS < <(kj '{range .spec.template.spec.volumes[*]}{.name}{"\n"}{end}')
}

# Refuses (return code != 0, reason on stderr) when patching is not the right tool for this Traefik.
guard_target() {
 local img tag major managed a
 # 1. Traefik reads static configuration from ONE source: a config file wins over command-line arguments.
 for a in ${ARGS[@]+"${ARGS[@]}"}; do
  case "${a}" in --configFile*|--configfile*|--CONFIGFILE*)
   echo "Traefik is started with ${a%%=*}: it then ignores command-line arguments, so the plugin cannot be added to the Deployment." >&2
   echo "Add the plugin to that configuration file instead (see traefik-plugin/rewrite-body-plugin-airgap.yaml for the settings)." >&2
   return 3 ;;
  esac
 done
 # 2. GitOps controllers revert changes made behind their back.
 managed=$(kj '{.metadata.labels.argocd\.argoproj\.io/instance}{"|"}{.metadata.annotations.argocd\.argoproj\.io/tracking-id}{"|"}{.metadata.labels.kustomize\.toolkit\.fluxcd\.io/name}{"|"}{.metadata.labels.helm\.toolkit\.fluxcd\.io/name}' 2>/dev/null || true)
 if [[ -n "${managed//|/}" && "${TRAEFIK_ALLOW_GITOPS:-0}" != 1 ]]; then
  echo "deployment/${DEPLOY} is managed by a GitOps controller (${managed//|/ }): it would revert a change made directly to the cluster." >&2
  echo "Give the people who manage Traefik the values file traefik-plugin/rewrite-body-plugin-airgap.yaml instead (set TRAEFIK_ALLOW_GITOPS=1 to patch anyway)." >&2
  return 4
 fi
 # 3. The plugin was tested with Traefik v3.
 img=$(kj "{.spec.template.spec.containers[${CIDX}].image}")
 tag=${img##*:}; major=${tag#v}; major=${major%%.*}
 if [[ "${img}" == *:* && "${major}" =~ ^[0-9]+$ && ${major} -lt 3 && "${TRAEFIK_ALLOW_OLD:-0}" != 1 ]]; then
  echo "This is Traefik ${tag}; the plugin was tested with Traefik v3 (set TRAEFIK_ALLOW_OLD=1 to try anyway)." >&2
  return 5
 fi
}

has_item() { local want=$1 x; shift; for x in "$@"; do [[ "${x}" == "${want}" ]] && return 0; done; return 1; }   # no pipe: safe under pipefail
json_escape() { sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'; }

# Builds OPS (JSON-patch operations) and SUMMARY (human lines) to ADD the local plugin. Only additions, plus the removal
# of a download entry of the same plugin name (Traefik refuses a plugin name used twice and then disables ALL plugins).
plan_enable() {
 OPS=(); SUMMARY=()
 local base="/spec/template/spec/containers/${CIDX}" i j have_arg=0 rm=()
 for i in ${ARGS[@]+"${!ARGS[@]}"}; do
  case "${ARGS[$i]}" in
   "${PLUGIN_ARG}") have_arg=1 ;;
   --experimental.localPlugins.${PLUGIN_NAME}.*) echo "the local plugin '${PLUGIN_NAME}' is already defined differently (${ARGS[$i]}): not touching it" >&2; return 2 ;;
   --experimental.plugins.${PLUGIN_NAME}.*) rm+=("${i}") ;;
  esac
 done
 for (( j=${#rm[@]}-1; j>=0; j-- )); do   # highest index first, so the lower ones stay valid
  OPS+=("{\"op\":\"test\",\"path\":\"${base}/args/${rm[$j]}\",\"value\":\"$(json_escape <<<"${ARGS[${rm[$j]}]}")\"}" "{\"op\":\"remove\",\"path\":\"${base}/args/${rm[$j]}\"}")
  SUMMARY+=("- arg ${ARGS[${rm[$j]}]}     (the download variant of the same plugin: Traefik refuses a plugin name used twice)")
 done
 if (( ! have_arg )); then
  if (( ${#ARGS[@]} == 0 )); then OPS+=("{\"op\":\"add\",\"path\":\"${base}/args\",\"value\":[\"${PLUGIN_ARG}\"]}")
  else OPS+=("{\"op\":\"add\",\"path\":\"${base}/args/-\",\"value\":\"${PLUGIN_ARG}\"}"); fi
  SUMMARY+=("+ arg ${PLUGIN_ARG}")
 fi
 if ! has_item "${PLUGIN_VOL}" ${MOUNTS[@]+"${MOUNTS[@]}"}; then
  local m="{\"mountPath\":\"${PLUGIN_MOUNT}\",\"name\":\"${PLUGIN_VOL}\",\"readOnly\":true}"
  if (( ${#MOUNTS[@]} == 0 )); then OPS+=("{\"op\":\"add\",\"path\":\"${base}/volumeMounts\",\"value\":[${m}]}")
  else OPS+=("{\"op\":\"add\",\"path\":\"${base}/volumeMounts/-\",\"value\":${m}}"); fi
  SUMMARY+=("+ volumeMount ${PLUGIN_VOL} -> ${PLUGIN_MOUNT} (read-only)")
 fi
 if ! has_item "${PLUGIN_VOL}" ${VOLS[@]+"${VOLS[@]}"}; then
  local v="{\"name\":\"${PLUGIN_VOL}\",\"configMap\":{\"name\":\"${PLUGIN_CM}\",\"defaultMode\":420}}"
  if (( ${#VOLS[@]} == 0 )); then OPS+=("{\"op\":\"add\",\"path\":\"/spec/template/spec/volumes\",\"value\":[${v}]}")
  else OPS+=("{\"op\":\"add\",\"path\":\"/spec/template/spec/volumes/-\",\"value\":${v}}"); fi
  SUMMARY+=("+ volume ${PLUGIN_VOL} (ConfigMap ${PLUGIN_CM})")
 fi
}

# Builds OPS / SUMMARY to REMOVE everything plan_enable adds (both variants of the plugin's arguments).
plan_disable() {
 OPS=(); SUMMARY=()
 local base="/spec/template/spec/containers/${CIDX}" i rmarg=() rmmnt=() rmvol=()
 for i in ${ARGS[@]+"${!ARGS[@]}"}; do
  case "${ARGS[$i]}" in --experimental.localPlugins.${PLUGIN_NAME}.*|--experimental.plugins.${PLUGIN_NAME}.*) rmarg+=("${i}") ;; esac
 done
 for i in ${MOUNTS[@]+"${!MOUNTS[@]}"}; do [[ "${MOUNTS[$i]}" == "${PLUGIN_VOL}" ]] && rmmnt+=("${i}"); done
 for i in ${VOLS[@]+"${!VOLS[@]}"}; do [[ "${VOLS[$i]}" == "${PLUGIN_VOL}" ]] && rmvol+=("${i}"); done
 for (( i=${#rmarg[@]}-1; i>=0; i-- )); do
  OPS+=("{\"op\":\"test\",\"path\":\"${base}/args/${rmarg[$i]}\",\"value\":\"$(json_escape <<<"${ARGS[${rmarg[$i]}]}")\"}" "{\"op\":\"remove\",\"path\":\"${base}/args/${rmarg[$i]}\"}")
  SUMMARY+=("- arg ${ARGS[${rmarg[$i]}]}")
 done
 for (( i=${#rmmnt[@]}-1; i>=0; i-- )); do OPS+=("{\"op\":\"remove\",\"path\":\"${base}/volumeMounts/${rmmnt[$i]}\"}"); SUMMARY+=("- volumeMount ${PLUGIN_VOL}"); done
 for (( i=${#rmvol[@]}-1; i>=0; i-- )); do OPS+=("{\"op\":\"remove\",\"path\":\"/spec/template/spec/volumes/${rmvol[$i]}\"}"); SUMMARY+=("- volume ${PLUGIN_VOL}"); done
}

CM_CREATED=0
# After a FAILED enable: remove the plugin ConfigMap again if (and only if) this run created it.
drop_new_cm() { if (( CM_CREATED )); then kubectl -n "${NS}" delete configmap "${PLUGIN_CM}" --ignore-not-found >/dev/null; CM_CREATED=0; fi; }

patch_json() { local IFS=,; printf '[%s]' "${OPS[*]}"; }

# The ConfigMap that carries the plugin's source code (Traefik compiles it at start-up: nothing is downloaded).
plugin_cm_yaml() {
 kubectl -n "${NS}" create configmap "${PLUGIN_CM}" --dry-run=client -o yaml \
  --from-file=.traefik.yml=plugin-src/.traefik.yml --from-file=go.mod=plugin-src/go.mod --from-file=rewritebody.go=plugin-src/rewritebody.go
}

# true when the ConfigMap exists and already carries exactly this plugin source (labels etc. are ignored on purpose:
# re-applying only to change those would needlessly modify an object that already works).
plugin_cm_current() {
 local f key live
 for f in .traefik.yml go.mod rewritebody.go; do
  key=${f//./\\.}   # jsonpath wants the dots in a key escaped
  live=$(kubectl -n "${NS}" get configmap "${PLUGIN_CM}" -o jsonpath="{.data.${key}}" 2>/dev/null) || return 1
  [[ "${live}" == "$(cat "plugin-src/${f}")" ]] || return 1
 done
}

# Prints what would change: the patch lines, the ConfigMap state, and whether the API server accepts the patch (dry run).
show_plugin_patch() { # $1 = enable | disable
 local img chart cmstate
 img=$(kj "{.spec.template.spec.containers[${CIDX}].image}"); chart=$(kj '{.metadata.labels.helm\.sh/chart}' 2>/dev/null || true)
 if [[ -n "${chart}" ]]; then chart="Helm chart ${chart#traefik-} (read from the running Deployment)"; else chart="not labelled as installed by a Helm chart"; fi
 echo "Traefik: deployment/${DEPLOY} in namespace ${NS}, image ${img}, ${chart}"
 # How Traefik restarts matters for "will anything be unreachable?": say what THIS Deployment does, from its own spec.
 local stype smax srep
 read -r stype smax srep <<<"$(kj '{.spec.strategy.type}{" "}{.spec.strategy.rollingUpdate.maxUnavailable}{" "}{.spec.replicas}' 2>/dev/null)"
 if (( ${#OPS[@]} )); then
  if [[ "${stype:-RollingUpdate}" == RollingUpdate && ( "${smax}" == 0 || "${smax}" == "0%" ) ]]; then
   echo "Restart: rolling update with maxUnavailable ${smax}, ${srep:-1} replica(s): the running pod keeps serving until the new one is Ready."
  else
   echo "Restart: this Deployment's strategy is ${stype:-RollingUpdate} (maxUnavailable ${smax:-default}, ${srep:-1} replica(s)): Traefik may be briefly unreachable while the pod is replaced - pick a quiet moment."
  fi
 fi
 echo "Changes to deployment/${DEPLOY} (nothing else on the cluster is touched):"
 if (( ${#SUMMARY[@]} )); then printf '  %s\n' "${SUMMARY[@]}"; else echo "  (none)"; fi
 if [[ "$1" == enable ]]; then
  if plugin_cm_current; then cmstate="already carries this plugin source: left as it is"
  elif kubectl -n "${NS}" get configmap "${PLUGIN_CM}" >/dev/null 2>&1; then cmstate="present with different content: it will be refreshed (the plugin source code)"
  else cmstate="new (the plugin source code)"; fi
  echo "ConfigMap ${PLUGIN_CM}: ${cmstate}"
 fi
 if (( ${#OPS[@]} )); then
  if kubectl -n "${NS}" patch deployment "${DEPLOY}" --type=json -p "$(patch_json)" --dry-run=server -o name >/dev/null 2>"${WORK}/dry.err"; then echo "The API server accepts this change (dry run)."
  else echo "The API server REJECTS this change: $(cat "${WORK}/dry.err")" >&2; return 1; fi
 fi
}

# Applies the plan: the ConfigMap first (so the new pod can mount it), then the patch, then waits for the rollout.
apply_plugin_patch() { # $1 = enable | disable
 if [[ "$1" == enable ]] && ! plugin_cm_current; then
  kubectl -n "${NS}" get configmap "${PLUGIN_CM}" >/dev/null 2>&1 || CM_CREATED=1   # remember: a failed run removes only what it created
  plugin_cm_yaml | kubectl apply --server-side --force-conflicts --field-manager=traefik-plugin -f - >/dev/null || return 1
 fi
 if (( ${#OPS[@]} == 0 )); then echo "Nothing to change on deployment/${DEPLOY}."; else
  REVISION_BEFORE=$(deploy_revision)   # so verify_plugins_loaded only ever undoes a rollout THIS run caused
  kubectl -n "${NS}" patch deployment "${DEPLOY}" --type=json -p "$(patch_json)" --field-manager=traefik-plugin || return 1
  wait_for_rollout || return 1
 fi
 [[ "$1" == disable ]] && kubectl -n "${NS}" delete configmap "${PLUGIN_CM}" --ignore-not-found
 return 0
}

# ============================================================================================ download variant (connected)

# Render the chart with the release's own values (+ overlay) and keep only the objects we manage:
# the Deployment, and the plugin ConfigMap that exists only when the overlay defines a local plugin.
# Needs only helm and kubectl (no jq / PyYAML).
render() { # $1 = optional values overlay
 local version="${TRAEFIK_CHART_VERSION:-$(helm list -n "${NS}" --filter "^${RELEASE}\$" -o yaml | sed -n 's/^ *chart: traefik-//p' | head -1)}"
 local args=(template "${RELEASE}" "${CHART}" -n "${NS}" -f "${WORK}/values.yaml")
 [[ -n "${1:-}" ]] && args+=(-f "$1")
 [[ -f "${CHART}" || -d "${CHART}" ]] || args+=(--version "${version}")
 helm get values "${RELEASE}" -n "${NS}" -o yaml > "${WORK}/values.yaml"
 helm "${args[@]}" --show-only templates/deployment.yaml > "${WORK}/rendered.yaml"
 grep -q '^kind: Deployment' "${WORK}/rendered.yaml" || { echo "could not render the Traefik Deployment from ${CHART}" >&2; return 1; }
 helm "${args[@]}" --show-only templates/local-plugins-cm.yaml >> "${WORK}/rendered.yaml" 2>/dev/null || true
}

show_changes() {
 local raw changes
 raw=$(kubectl diff --server-side --force-conflicts --field-manager=traefik-plugin -f "${WORK}/rendered.yaml" || true)  # exit 1 = differences
 # Show the Deployment's changes line by line; for a ConfigMap (the plugin's source code) just say what it is.
 changes=$(awk '
  function flush() { if (cm && cnt > 0) { sub(/^[^.]*\./, "", obj); print "+ ConfigMap " obj " (" cnt " lines: the plugin source code)" } cm = 0; cnt = 0 }
  /^diff / { flush(); n = split($NF, p, "/"); obj = p[n]; cm = (obj ~ /ConfigMap/); sub(/^.*ConfigMap\./, "", obj); next }
  /^[-+] / { if ($0 ~ /generation:|resourceVersion:|managedFields|time:|manager:|operation:|fieldsType|fieldsV1|f:/) next; if (cm) { cnt++; next } print; next }
  END { flush() }' <<<"${raw}")
 echo "Changes to deployment/${DEPLOY} (+ plugin ConfigMap):"
 if [[ -n "${changes}" ]]; then echo "${changes}"; else echo "  (none)"; fi
}

apply_and_wait() {
 REVISION_BEFORE=$(deploy_revision)
 kubectl apply --server-side --force-conflicts --field-manager=traefik-plugin -f "${WORK}/rendered.yaml"
 wait_for_rollout
}

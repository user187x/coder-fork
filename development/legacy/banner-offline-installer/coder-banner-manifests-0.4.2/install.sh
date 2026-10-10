#!/usr/bin/env bash
# Installs the Coder banner from the plain manifests in this directory, without Helm.
#
# It asks for what it cannot know (Coder's hostname, the banner's URL path, the image in your registry, ...), suggests an
# answer for each from what it finds on the cluster (press Enter to accept), renders the manifests into ./rendered/, then:
#   1. checks the cluster (read-only) and has the API server dry-run every object
#   2. optionally loads the bundled image into your registry
#   3. enables Traefik's rewrite-body plugin (the one change to an existing object: 1 arg, 1 volumeMount, 1 volume)
#   4. applies the banner's own objects (all named coder-banner*, in Coder's namespace)
#   5. waits until they are running and checks through Traefik that the banner is injected
#
# Usage:
#   ./install.sh                 interactive install
#   ./install.sh --dry-run       ask, render, validate on the API server; change nothing
#   ./install.sh --render-only   ask, render into ./rendered/; no cluster access at all
#   ./install.sh -y              accept every suggested answer and do not ask for the final go-ahead
#
# Every answer can be preset in the environment (then it is not asked), e.g. for a re-run or an upgrade:
#   set -a; . rendered/answers.env; set +a; ./install.sh -y
#   CODER_NAMESPACE CODER_SERVICE CODER_SERVICE_PORT CODER_HOST BANNER_PATH GATEWAY_NAMESPACE GATEWAY_NAME
#   GATEWAY_SECTION BANNER_IMAGE IMAGE_PULL_SECRET ADMIN_ROLES MENU_LABEL TRAEFIK_NAMESPACE TRAEFIK_DEPLOYMENT
#   TRAEFIK_CONTAINER ENABLE_PLUGIN (yes|no) LOAD_IMAGE (yes|no) IMAGE_TAR
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
SRC="${HERE}/coder-banner"
PLUGIN_SRC="${HERE}/traefik-plugin/01-configmap-rewrite-body-plugin.yaml"
OUT="${HERE}/rendered"        # what is installed: replaced only after a successful apply (or by --render-only)
W="${HERE}/rendered.new"      # this run's render; a failed run leaves it here for inspection and OUT untouched
FIELD_MANAGER=coder-banner
PLUGIN_MANAGER=traefik-plugin
PLUGIN_NAME=rewrite-body
PLUGIN_MODULE=github.com/traefik/plugin-rewritebody
PLUGIN_ARG="--experimental.localPlugins.${PLUGIN_NAME}.moduleName=${PLUGIN_MODULE}"
PLUGIN_MOUNT="/plugins-local/src/${PLUGIN_MODULE}"
BUNDLED_IMAGE=python:3.13-alpine

MODE=install; ASSUME_YES=0
for arg in "$@"; do
 case "${arg}" in
  --dry-run) MODE=dry-run ;;
  --render-only) MODE=render ;;
  -y|--yes) ASSUME_YES=1 ;;
  -h|--help) sed -n '2,/^set -euo/p' "$0" | sed '$d; s/^# \{0,1\}//'; exit 0 ;;
  *) echo "unknown option: ${arg} (see --help)" >&2; exit 2 ;;
 esac
done

# ------------------------------------------------------------------------------------------------------------ output
if [[ -t 1 ]]; then B=$'\e[1m'; G=$'\e[32m'; Y=$'\e[33m'; R=$'\e[31m'; D=$'\e[2m'; N=$'\e[0m'; else B='' G='' Y='' R='' D='' N=''; fi
STEP=0
step() { STEP=$((STEP + 1)); printf '\n%s== %d. %s%s\n' "${B}" "${STEP}" "$*" "${N}"; }
ok()   { printf '  %sOK%s    %s\n' "${G}" "${N}" "$*"; }
WARNINGS=0
warn() { WARNINGS=$((WARNINGS + 1)); printf '  %sWARN%s  %s\n' "${Y}" "${N}" "$*"; }
info() { printf '        %s\n' "$*"; }
die()  { printf '\n%sFAILED%s %s\n' "${R}" "${N}" "$*" >&2; exit 1; }
run()  { printf '  %s$ %s%s\n' "${D}" "$*" "${N}"; "$@"; }

# ------------------------------------------------------------------------------------------------------------ prompts
# ask VAR "question" "suggestion" [validator]: a preset $VAR wins, -y takes the suggestion, otherwise the user answers.
ask() {
 local var=$1 question=$2 suggestion=$3 check=${4:-} answer
 if [[ -n "${!var+x}" ]]; then
  answer=${!var}; printf '  %s: %s%s%s (preset)\n' "${question}" "${B}" "${answer:-<none>}" "${N}"
 elif (( ASSUME_YES )); then
  answer=${suggestion}; printf '  %s: %s%s%s\n' "${question}" "${B}" "${answer:-<none>}" "${N}"
 else
  [[ -t 0 ]] || die "no terminal to ask '${question}': set ${var} or use -y"
  while :; do
   read -r -p "  ${question} [${suggestion:-none}]: " answer || die "input closed"
   answer=${answer:-${suggestion}}
   [[ "${answer}" == "-" ]] && answer=""   # "-" = explicitly empty where a suggestion exists
   if [[ -z "${check}" ]] || "${check}" "${answer}"; then break; fi
  done
 fi
 if [[ -n "${check}" ]] && ! "${check}" "${answer}"; then die "invalid ${var}: '${answer}'"; fi
 printf -v "${var}" '%s' "${answer}"
}
yesno() { # yesno VAR "question" default(yes|no)
 local var=$1 question=$2 def=$3 answer
 if [[ -n "${!var:-}" ]]; then answer=${!var}
 elif (( ASSUME_YES )); then answer=${def}
 else
  [[ -t 0 ]] || die "no terminal to ask '${question}': set ${var} or use -y"
  read -r -p "  ${question} [$([[ ${def} == yes ]] && echo Y/n || echo y/N)]: " answer || die "input closed"
  answer=${answer:-${def}}
 fi
 case "${answer,,}" in y|yes) printf -v "${var}" yes ;; n|no) printf -v "${var}" no ;; *) die "answer yes or no for ${var}" ;; esac
 printf '  %s: %s%s%s\n' "${question}" "${B}" "${!var}" "${N}"
}

# Validators print why an answer is refused (to stderr) and return non-zero.
v_dns()     { [[ "$1" =~ ^[a-z0-9]([-a-z0-9]*[a-z0-9])?(\.[a-z0-9]([-a-z0-9]*[a-z0-9])?)*$ && ${#1} -le 253 ]] || { echo "    a lower-case DNS name, e.g. coder.example.com" >&2; return 1; }; }
v_host()    { v_dns "$1" || return 1; [[ "$1" != coder.example.com ]] || { echo "    coder.example.com is only a placeholder: enter the hostname your users open Coder on" >&2; return 1; }; }
v_name()    { [[ "$1" =~ ^[a-z0-9]([-a-z0-9]*[a-z0-9])?$ && ${#1} -le 63 ]] || { echo "    a Kubernetes name (lower case, digits, '-')" >&2; return 1; }; }
v_optname() { [[ -z "$1" ]] || v_name "$1"; }
v_port()    { [[ "$1" =~ ^[0-9]+$ && $1 -ge 1 && $1 -le 65535 ]] || { echo "    a port number" >&2; return 1; }; }
v_image()   { [[ "$1" =~ ^[A-Za-z0-9._:/@-]+$ && "$1" == *[:@]* ]] || { echo "    a full image reference with a tag, e.g. registry.internal:5000/library/python:3.13-alpine" >&2; return 1; }; }
v_label()   { [[ "$1" =~ ^[A-Za-z0-9\ _-]{1,30}$ ]] || { echo "    letters, digits, spaces, '_' and '-' (max 30)" >&2; return 1; }; }
v_roles()   { [[ "$1" =~ ^[a-z0-9-]+(,[a-z0-9-]+)*$ ]] || { echo "    comma-separated Coder role names, e.g. owner or owner,user-admin" >&2; return 1; }; }
# Paths Coder itself serves: the banner's prefix must not take any of them over.
CODER_PATHS=" api assets bin icon icons emojis healthz derp login logout setup cli-auth oauth2 external-auth gitauth
 workspaces templates users deployment organizations audit health settings terminal apps connectionlog tasks
 notifications licenses groups install favicon.ico favicon.svg manifest.json robots.txt ai-bridge "
v_path() {
 [[ "$1" =~ ^/[A-Za-z0-9_-]{2,40}$ ]] || { echo "    one path segment starting with '/', e.g. /__banner or /announcements" >&2; return 1; }
 [[ "${CODER_PATHS}" != *" ${1#/} "* && "$1" != /@* ]] || { echo "    ${1} is used by Coder itself; pick another (the default /__banner never collides)" >&2; return 1; }
}

# ------------------------------------------------------------------------------------------------------------ cluster helpers
k() { kubectl "$@"; }
exists() { kubectl get "$@" -o name >/dev/null 2>&1; }
# First line of a command's output, without SIGPIPE surprises under pipefail.
first() { local out; out=$("$@" 2>/dev/null) || true; printf '%s' "${out%%$'\n'*}"; }

# replace_literal FILE OLD NEW: every occurrence, taken literally (no regex, no escape processing).
replace_literal() {
 OLD=$2 NEW=$3 awk 'BEGIN { o = ENVIRON["OLD"]; n = ENVIRON["NEW"] }
  { out = ""; s = $0; while ((i = index(s, o)) > 0) { out = out substr(s, 1, i - 1) n; s = substr(s, i + length(o)) } print out s }' "$1" > "$1.new"
 mv "$1.new" "$1"
}
need() { command -v "$1" >/dev/null 2>&1 || die "'$1' is required but not installed"; }
need sed; need awk; need grep; need sha256sum
[[ -d "${SRC}" && -f "${PLUGIN_SRC}" ]] || die "run this from the unpacked coder-banner-manifests directory (missing ${SRC} or ${PLUGIN_SRC})"

cat <<EOF
${B}Coder banner installer (plain manifests, no Helm)${N}
Adds an announcement banner and an admin page to the Coder dashboard. Coder itself is not modified; nothing that already
exists is changed except Traefik's Deployment (one plugin argument, mount and volume). Mode: ${B}${MODE}${N}.
Press Enter to accept a suggestion in [brackets].
EOF

# ============================================================================================================ 1. cluster
ONLINE=0
if [[ "${MODE}" != render ]]; then
 need kubectl; need curl
 step "Cluster"
 CTX=$(kubectl config current-context 2>/dev/null) || die "kubectl has no current context"
 kubectl version --request-timeout=10s >/dev/null 2>&1 || die "cannot reach the API server of context '${CTX}'"
 info "kubectl context: ${B}${CTX}${N}   ($(first kubectl config view --minify -o 'jsonpath={.clusters[0].cluster.server}'))"
 yesno CONFIRM_CONTEXT "Install into this cluster?" yes
 [[ "${CONFIRM_CONTEXT}" == yes ]] || die "stopped: switch context with 'kubectl config use-context <name>' and run again"
 for crd in httproutes.gateway.networking.k8s.io gateways.gateway.networking.k8s.io middlewares.traefik.io; do
  exists crd "${crd}" || die "CRD ${crd} is missing: this cluster has no Gateway API / Traefik CRDs (the banner never installs CRDs)"
 done
 ok "Gateway API and Traefik CRDs present"
 ONLINE=1
fi

# ============================================================================================================ 2. questions
step "Coder"
sug_ns=coder
if (( ONLINE )); then
 found=$(first kubectl get svc -A -l app.kubernetes.io/name=coder -o 'jsonpath={range .items[*]}{.metadata.namespace}{"\n"}{end}')
 [[ -n "${found}" ]] && sug_ns=${found}
fi
ask CODER_NAMESPACE "Coder's namespace" "${sug_ns}" v_name
if (( ONLINE )); then
 exists namespace "${CODER_NAMESPACE}" || die "namespace ${CODER_NAMESPACE} does not exist"
fi

sug_svc=coder; sug_port=80
if (( ONLINE )); then
 found=$(first kubectl -n "${CODER_NAMESPACE}" get svc -l app.kubernetes.io/name=coder -o 'jsonpath={range .items[*]}{.metadata.name}{"\n"}{end}')
 [[ -n "${found}" ]] && sug_svc=${found}
fi
ask CODER_SERVICE "Coder's Service" "${sug_svc}" v_name
if (( ONLINE )); then
 exists -n "${CODER_NAMESPACE}" svc "${CODER_SERVICE}" || die "Service ${CODER_NAMESPACE}/${CODER_SERVICE} does not exist"
 found=$(first kubectl -n "${CODER_NAMESPACE}" get svc "${CODER_SERVICE}" -o 'jsonpath={.spec.ports[?(@.name=="http")].port}')
 [[ -n "${found}" ]] || found=$(first kubectl -n "${CODER_NAMESPACE}" get svc "${CODER_SERVICE}" -o 'jsonpath={.spec.ports[0].port}')
 [[ -n "${found}" ]] && sug_port=${found}
fi
ask CODER_SERVICE_PORT "Coder's Service port (plain HTTP)" "${sug_port}" v_port

# Coder's own HTTPRoute tells us the host, the Gateway and the listener it uses.
sug_host=coder.example.com; sug_gw_ns=traefik; sug_gw=traefik-gateway; sug_section=https; coder_route=""
(( ONLINE )) && sug_section=""   # on a cluster: taken from Coder's route or the Gateway's only HTTPS listener
if (( ONLINE )); then
 routes=$(kubectl -n "${CODER_NAMESPACE}" get httproute -o 'jsonpath={range .items[*]}{.metadata.name}{"|"}{.spec.hostnames[0]}{"|"}{.spec.parentRefs[0].namespace}{"|"}{.spec.parentRefs[0].name}{"|"}{.spec.parentRefs[0].sectionName}{"|"}{.spec.rules[*].backendRefs[*].name}{"\n"}{end}' 2>/dev/null || true)
 while IFS='|' read -r rname rhost rgwns rgw rsec rbackends; do
  [[ -z "${rname}" || "${rname}" == coder-banner ]] && continue
  [[ " ${rbackends} " == *" ${CODER_SERVICE} "* ]] || continue
  coder_route=${rname}; [[ -n "${rhost}" ]] && sug_host=${rhost}
  sug_gw_ns=${rgwns:-${CODER_NAMESPACE}}; sug_gw=${rgw}; sug_section=${rsec}; break
 done <<<"${routes}"
 if [[ -n "${coder_route}" ]]; then info "found Coder's own route: HTTPRoute/${coder_route}"
 else
  warn "no HTTPRoute in ${CODER_NAMESPACE} sends traffic to Service ${CODER_SERVICE}; the suggestions below are defaults"
  url=$(first kubectl -n "${CODER_NAMESPACE}" get deploy -l app.kubernetes.io/name=coder -o 'jsonpath={range .items[0].spec.template.spec.containers[0].env[?(@.name=="CODER_ACCESS_URL")]}{.value}{end}')
  url=${url#*://}; url=${url%%/*}; url=${url%%:*}
  [[ -n "${url}" ]] && sug_host=${url}
 fi
fi
ask CODER_HOST "Coder's hostname (the one users open)" "${sug_host}" v_host

step "Banner"
info "Everything the banner serves lives under one path on Coder's host: the loader script, banner.json, the live"
info "WebSocket and the admin page (<path>/admin). /__banner is chosen so it can never collide with Coder's own pages."
ask BANNER_PATH "URL path prefix for the banner" "/__banner" v_path
info "admin page will be: ${B}https://${CODER_HOST}${BANNER_PATH}/admin${N}"
ask ADMIN_ROLES "Coder roles allowed to publish banners (comma-separated)" "owner" v_roles
ask MENU_LABEL "Label of the admin link in Coder's user menu (replaces 'Codernauts')" "Banner" v_label

step "Gateway"
ask GATEWAY_NAMESPACE "Gateway namespace" "${sug_gw_ns}" v_name
ask GATEWAY_NAME "Gateway name" "${sug_gw}" v_name
listeners=""
if (( ONLINE )); then
 exists -n "${GATEWAY_NAMESPACE}" gateway "${GATEWAY_NAME}" || die "Gateway ${GATEWAY_NAMESPACE}/${GATEWAY_NAME} does not exist"
 listeners=$(kubectl -n "${GATEWAY_NAMESPACE}" get gateway "${GATEWAY_NAME}" -o 'jsonpath={range .spec.listeners[*]}{.name}{"|"}{.protocol}{"|"}{.port}{"|"}{.hostname}{"|"}{.allowedRoutes.namespaces.from}{"\n"}{end}')
 info "listeners (name, protocol, port, hostname, routes from):"
 while IFS= read -r l; do [[ -n "${l}" ]] && info "  ${l//|/  }"; done <<<"${listeners}"
 if [[ -z "${sug_section}" ]]; then
  https=$(awk -F'|' '$2 == "HTTPS" { print $1 }' <<<"${listeners}")
  [[ -n "${https}" && "${https}" != *$'\n'* ]] && sug_section=${https}
 fi
fi
info "The banner route should use the HTTPS listener, so it can never answer instead of an HTTP->HTTPS redirect."
info "Enter '-' for no listener name (= attach to every listener, like an unscoped route)."
ask GATEWAY_SECTION "Gateway listener for the banner route" "${sug_section}" v_optname
if (( ONLINE )) && [[ -n "${GATEWAY_SECTION}" ]]; then
 line=$(awk -F'|' -v s="${GATEWAY_SECTION}" '$1 == s' <<<"${listeners}")
 [[ -n "${line}" ]] || die "Gateway ${GATEWAY_NAME} has no listener named '${GATEWAY_SECTION}'"
 IFS='|' read -r _ lproto _ lhost lfrom <<<"${line}"
 [[ "${lproto}" == HTTPS ]] || warn "listener ${GATEWAY_SECTION} is ${lproto}, not HTTPS"
 if [[ "${lfrom:-Same}" == Same && "${GATEWAY_NAMESPACE}" != "${CODER_NAMESPACE}" ]]; then
  die "listener ${GATEWAY_SECTION} only accepts routes from namespace ${GATEWAY_NAMESPACE}, not ${CODER_NAMESPACE}"
 fi
 [[ "${lfrom}" == Selector ]] && warn "listener ${GATEWAY_SECTION} accepts routes by namespace selector: make sure ${CODER_NAMESPACE} matches"
 if [[ -n "${lhost}" && "${lhost}" != "${CODER_HOST}" && ! ( "${lhost}" == \*.* && "${CODER_HOST}" == *."${lhost#\*.}" ) ]]; then
  die "listener ${GATEWAY_SECTION} only serves hostname ${lhost}, not ${CODER_HOST}"
 fi
fi

step "Image"
info "The banner service is a small Python program mounted from a ConfigMap: any image with python3 (3.8+) runs it."
info "The airgap bundle ships ${BUNDLED_IMAGE} (linux/amd64) in images/images.tar."
ask BANNER_IMAGE "Image in your registry" "registry.internal:5000/library/${BUNDLED_IMAGE}" v_image
ask IMAGE_PULL_SECRET "Pull secret for that registry in ${CODER_NAMESPACE} ('-' = none)" "" v_optname
if (( ONLINE )) && [[ -n "${IMAGE_PULL_SECRET}" ]]; then
 exists -n "${CODER_NAMESPACE}" secret "${IMAGE_PULL_SECRET}" || die "Secret ${CODER_NAMESPACE}/${IMAGE_PULL_SECRET} does not exist"
fi
if [[ "${MODE}" == install ]]; then
 yesno LOAD_IMAGE "Push the bundled image to ${BANNER_IMAGE} now (needs docker, podman or skopeo)?" no
 if [[ "${LOAD_IMAGE}" == yes ]]; then
  sug_tar=""
  mapfile -t cands < <(printf '%s\n' "${HERE}/images/images.tar"; printf '%s\n' "${HERE}"/../coder-banner-airgap-*/images/images.tar | sort -rV)
  for c in "${cands[@]}"; do [[ -f "${c}" ]] && { sug_tar=$(realpath "${c}"); break; }; done   # newest bundle first
  ask IMAGE_TAR "Path to images.tar from the airgap bundle" "${sug_tar}"
  [[ -f "${IMAGE_TAR}" ]] || die "no such file: ${IMAGE_TAR}"
 fi
fi
if (( ONLINE )); then
 archs=$(kubectl get nodes -o 'jsonpath={range .items[*]}{.status.nodeInfo.architecture}{"\n"}{end}' | sort -u | tr '\n' ' ')
 [[ "${archs}" == "amd64 " ]] || warn "node architectures: ${archs}- the bundled image is amd64 only; use an image built for your nodes"
fi

step "Traefik"
sug_tns=${GATEWAY_NAMESPACE}; sug_tdep=traefik
if (( ONLINE )); then
 found=$(kubectl -n "${sug_tns}" get deploy -o 'jsonpath={range .items[*]}{.metadata.name}{"\t"}{.spec.template.spec.containers[*].image}{"\n"}{end}' 2>/dev/null | awk -F'\t' '$2 ~ /traefik/ { print $1 }' || true)
 [[ -n "${found}" ]] && sug_tdep=${found%%$'\n'*}
fi
ask TRAEFIK_NAMESPACE "Traefik's namespace" "${sug_tns}" v_name
ask TRAEFIK_DEPLOYMENT "Traefik's Deployment" "${sug_tdep}" v_name
PLUGIN_CM="${TRAEFIK_DEPLOYMENT}-local-plugin-${PLUGIN_NAME}"

# Look at Traefik's live Deployment to decide whether (and how) to add the plugin.
T_CIDX=""; T_ARGS=(); T_MOUNTS=(); T_VOLS=(); PLUGIN_PLAN=()
if (( ONLINE )); then
 exists -n "${TRAEFIK_NAMESPACE}" deploy "${TRAEFIK_DEPLOYMENT}" || die "Deployment ${TRAEFIK_NAMESPACE}/${TRAEFIK_DEPLOYMENT} does not exist"
 tj() { kubectl -n "${TRAEFIK_NAMESPACE}" get deploy "${TRAEFIK_DEPLOYMENT}" -o "jsonpath=$1"; }
 names=$(tj '{range .spec.template.spec.containers[*]}{.name}{"\n"}{end}')
 sug_c=$(awk '$0 == "traefik"' <<<"${names}"); [[ -n "${sug_c}" ]] || sug_c=${names%%$'\n'*}
 if [[ "${names}" == *$'\n'?* && -z "${TRAEFIK_CONTAINER+x}" && "${sug_c}" != traefik ]]; then
  ask TRAEFIK_CONTAINER "Traefik's container (${names//$'\n'/, })" "${sug_c}" v_name
 else TRAEFIK_CONTAINER=${TRAEFIK_CONTAINER:-${sug_c}}; fi
 T_CIDX=$(awk -v w="${TRAEFIK_CONTAINER}" '$0 == w { print NR - 1; exit }' <<<"${names}")
 [[ -n "${T_CIDX}" ]] || die "container ${TRAEFIK_CONTAINER} not found in deployment/${TRAEFIK_DEPLOYMENT}"
 mapfile -t T_ARGS < <(tj "{range .spec.template.spec.containers[${T_CIDX}].args[*]}{@}{\"\\n\"}{end}")
 mapfile -t T_MOUNTS < <(tj "{range .spec.template.spec.containers[${T_CIDX}].volumeMounts[*]}{.name}{\"\\n\"}{end}")
 mapfile -t T_VOLS < <(tj '{range .spec.template.spec.volumes[*]}{.name}{"\n"}{end}')
 T_IMAGE=$(tj "{.spec.template.spec.containers[${T_CIDX}].image}")
 info "Traefik: deployment/${TRAEFIK_DEPLOYMENT}, container ${TRAEFIK_CONTAINER}, image ${T_IMAGE}"

 blocker=""; gitops=""
 for a in ${T_ARGS[@]+"${T_ARGS[@]}"}; do
  case "${a}" in --configFile*|--configfile*) blocker="Traefik is started with ${a%%=*}, so it ignores command-line arguments: add the plugin to that file (settings: rewrite-body-plugin-airgap.yaml in the bundle)" ;; esac
  case "${a}" in --experimental.localPlugins.${PLUGIN_NAME}.*) [[ "${a}" == "${PLUGIN_ARG}" ]] || blocker="a different local plugin '${PLUGIN_NAME}' is already configured (${a})" ;; esac
 done
 gitops=$(tj '{.metadata.labels.argocd\.argoproj\.io/instance}{.metadata.annotations.argocd\.argoproj\.io/tracking-id}{.metadata.labels.kustomize\.toolkit\.fluxcd\.io/name}{.metadata.labels.helm\.toolkit\.fluxcd\.io/name}' 2>/dev/null || true)
 tag=${T_IMAGE##*:}; major=${tag#v}; major=${major%%.*}
 if [[ "${T_IMAGE}" == *:* && "${major}" =~ ^[0-9]+$ && ${major} -lt 3 ]]; then blocker="Traefik ${tag} is older than v3; the plugin is only tested with v3.3-v3.7"; fi

 # The plan: only additions, plus removing a DOWNLOAD entry of the same plugin (Traefik refuses a name defined twice
 # and then disables ALL plugins while still reporting Ready).
 base="/spec/template/spec/containers/${T_CIDX}"; have_arg=0; rm_idx=()
 for i in ${T_ARGS[@]+"${!T_ARGS[@]}"}; do
  [[ "${T_ARGS[$i]}" == "${PLUGIN_ARG}" ]] && have_arg=1
  [[ "${T_ARGS[$i]}" == --experimental.plugins.${PLUGIN_NAME}.* ]] && rm_idx+=("${i}")
 done
 jesc() { local s=${1//\\/\\\\}; printf '%s' "${s//\"/\\\"}"; }
 PATCH_OPS=("{\"op\":\"test\",\"path\":\"${base}/name\",\"value\":\"${TRAEFIK_CONTAINER}\"}")
 for (( j=${#rm_idx[@]}-1; j>=0; j-- )); do
  PATCH_OPS+=("{\"op\":\"test\",\"path\":\"${base}/args/${rm_idx[$j]}\",\"value\":\"$(jesc "${T_ARGS[${rm_idx[$j]}]}")\"}" "{\"op\":\"remove\",\"path\":\"${base}/args/${rm_idx[$j]}\"}")
  PLUGIN_PLAN+=("- arg ${T_ARGS[${rm_idx[$j]}]}   (download variant of the same plugin)")
 done
 has() { local w=$1 x; shift; for x in "$@"; do [[ "${x}" == "${w}" ]] && return 0; done; return 1; }
 if (( ! have_arg )); then
  if (( ${#T_ARGS[@]} )); then PATCH_OPS+=("{\"op\":\"add\",\"path\":\"${base}/args/-\",\"value\":\"${PLUGIN_ARG}\"}")
  else PATCH_OPS+=("{\"op\":\"add\",\"path\":\"${base}/args\",\"value\":[\"${PLUGIN_ARG}\"]}"); fi
  PLUGIN_PLAN+=("+ arg ${PLUGIN_ARG}")
 fi
 if ! has "${PLUGIN_NAME}" ${T_MOUNTS[@]+"${T_MOUNTS[@]}"}; then
  m="{\"name\":\"${PLUGIN_NAME}\",\"mountPath\":\"${PLUGIN_MOUNT}\",\"readOnly\":true}"
  if (( ${#T_MOUNTS[@]} )); then PATCH_OPS+=("{\"op\":\"add\",\"path\":\"${base}/volumeMounts/-\",\"value\":${m}}")
  else PATCH_OPS+=("{\"op\":\"add\",\"path\":\"${base}/volumeMounts\",\"value\":[${m}]}"); fi
  PLUGIN_PLAN+=("+ volumeMount ${PLUGIN_NAME} -> ${PLUGIN_MOUNT} (read-only)")
 fi
 if ! has "${PLUGIN_NAME}" ${T_VOLS[@]+"${T_VOLS[@]}"}; then
  v="{\"name\":\"${PLUGIN_NAME}\",\"configMap\":{\"name\":\"${PLUGIN_CM}\",\"defaultMode\":420}}"
  if (( ${#T_VOLS[@]} )); then PATCH_OPS+=("{\"op\":\"add\",\"path\":\"/spec/template/spec/volumes/-\",\"value\":${v}}")
  else PATCH_OPS+=("{\"op\":\"add\",\"path\":\"/spec/template/spec/volumes\",\"value\":[${v}]}"); fi
  PLUGIN_PLAN+=("+ volume ${PLUGIN_NAME} (ConfigMap ${PLUGIN_CM})")
 fi
 PATCH_JSON="[$(IFS=,; printf '%s' "${PATCH_OPS[*]}")]"

 if [[ -n "${blocker}" ]]; then
  warn "${blocker}"
  info "The plugin cannot be added to this Traefik by a patch. The banner's objects can still be installed; Coder keeps"
  info "working, but no banner appears until Traefik's owners load the '${PLUGIN_NAME}' plugin."
  ENABLE_PLUGIN=no
 elif (( ${#PLUGIN_PLAN[@]} == 0 )); then
  ok "the ${PLUGIN_NAME} plugin is already configured in Traefik"
  ENABLE_PLUGIN=already
 else
  if [[ -n "${gitops}" ]]; then
   warn "deployment/${TRAEFIK_DEPLOYMENT} is managed by Argo CD or Flux (${gitops}): it will probably revert a direct patch."
   info "Better: have its owners add the plugin to Traefik's values (rewrite-body-plugin-airgap.yaml in the bundle)."
   def=no
  else def=yes; fi
  info "Traefik reads plugins only at start-up, so this patch starts a new Traefik pod:"
  for p in "${PLUGIN_PLAN[@]}"; do info "  ${p}"; done
  read -r stype smax srep <<<"$(tj '{.spec.strategy.type}{" "}{.spec.strategy.rollingUpdate.maxUnavailable}{" "}{.spec.replicas}')"
  if [[ "${stype:-RollingUpdate}" == RollingUpdate && ( "${smax}" == 0 || "${smax}" == 0% ) ]]; then
   info "  rolling update with maxUnavailable 0: the old pod serves until the new one is Ready (terminals reconnect once)"
  else
   warn "strategy ${stype:-RollingUpdate}, maxUnavailable ${smax:-default}, ${srep:-1} replica(s): sites behind Traefik may be unreachable for a few seconds"
  fi
  yesno ENABLE_PLUGIN "Patch Traefik to load the ${PLUGIN_NAME} plugin?" "${def}"
 fi
fi

# ============================================================================================================ 3. render
step "Render manifests"
rm -rf "${W}"; mkdir -p "${W}/coder-banner" "${W}/traefik-plugin"
T="${W}/coder-banner"
cp "${SRC}"/*.yaml "${T}/"

# Namespace (every object, and the RoleBinding's subject), then the per-file values.
sed -i "s/^\(\s*\)namespace: coder$/\1namespace: ${CODER_NAMESPACE}/" "${T}"/*.yaml
for f in "${T}"/06-configmap-app.yaml "${T}"/12-middleware-inject.yaml "${T}"/15-middleware-menu-rewrite.yaml "${T}"/17-httproute.yaml; do
 replace_literal "${f}" "%2F__banner%2F" "${BANNER_PATH//\//%2F}%2F"
 replace_literal "${f}" "/__banner" "${BANNER_PATH}"
done
replace_literal "${T}/09-deployment.yaml" "image: registry.internal:5000/library/python:3.13-alpine" "image: ${BANNER_IMAGE}"
replace_literal "${T}/09-deployment.yaml" '"http://coder.coder.svc.cluster.local:80"' "\"http://${CODER_SERVICE}.${CODER_NAMESPACE}.svc.cluster.local:${CODER_SERVICE_PORT}\""
replace_literal "${T}/09-deployment.yaml" '{ name: ADMIN_ROLES, value: "owner" }' "{ name: ADMIN_ROLES, value: \"${ADMIN_ROLES}\" }"
if [[ -n "${IMAGE_PULL_SECRET}" ]]; then
 sed -i "s#^      serviceAccountName: coder-banner\$#&\n      imagePullSecrets:\n        - name: ${IMAGE_PULL_SECRET}#" "${T}/09-deployment.yaml"
fi
# In the menu Middleware the roles are a JS array inside a JSON string: [\"owner\",\"user-admin\"]
replace_literal "${T}/15-middleware-menu-rewrite.yaml" '[\"owner\"]' '[\"'"${ADMIN_ROLES//,/'\",\"'}"'\"]'
replace_literal "${T}/15-middleware-menu-rewrite.yaml" 'children:`Banner`' "children:\`${MENU_LABEL}\`"
awk -v host="${CODER_HOST}" -v gwns="${GATEWAY_NAMESPACE}" -v gw="${GATEWAY_NAME}" -v sec="${GATEWAY_SECTION}" \
    -v svc="${CODER_SERVICE}" -v port="${CODER_SERVICE_PORT}" '
 $0 == "    - \"coder.example.com\"" { print "    - \"" host "\""; next }
 $0 == "      name: traefik-gateway"   { print "      name: " gw; next }
 $0 == "      namespace: traefik"      { print "      namespace: " gwns; next }
 $0 == "      sectionName: \"https\""  { if (sec != "") print "      sectionName: \"" sec "\""; next }
 $0 == "        - name: coder"         { print "        - name: " svc; coder = 1; next }
 coder && $0 == "          port: 80"    { print "          port: " port; coder = 0; next }
 { coder = 0; print }' "${T}/17-httproute.yaml" > "${T}/17-httproute.yaml.new" && mv "${T}/17-httproute.yaml.new" "${T}/17-httproute.yaml"
# Pods restart only when their spec changes: tie the checksum to the code ConfigMaps as rendered now.
sum=$(cat "${T}"/06-configmap-app.yaml "${T}"/07-configmap-vendor.yaml "${T}"/08-configmap-emoji.yaml | sha256sum | cut -c1-16)
sed -i "s/^\(\s*checksum\/app:\).*/\1 ${sum}/" "${T}/09-deployment.yaml"

sed -e "s/^  name: traefik-local-plugin-rewrite-body\$/  name: ${PLUGIN_CM}/" -e "s/^  namespace: traefik\$/  namespace: ${TRAEFIK_NAMESPACE}/" \
 "${PLUGIN_SRC}" > "${W}/traefik-plugin/01-configmap-rewrite-body-plugin.yaml"
[[ -n "${PATCH_JSON:-}" ]] && printf '%s\n' "${PATCH_JSON}" > "${W}/traefik-plugin/02-traefik-deployment-patch.json"

# Every placeholder must be gone, and every value must have landed where it belongs.
left=$(grep -l 'coder\.example\.com' "${T}"/*.yaml || true)
[[ -z "${left}" ]] || die "placeholders left in: ${left}"
if [[ "${BANNER_PATH}" != /__banner ]]; then
 left=$(grep -l '/__banner' "${T}"/*.yaml || true); [[ -z "${left}" ]] || die "/__banner left in: ${left}"
fi
grep -q "value: ${BANNER_PATH}\$" "${T}/17-httproute.yaml" || die "banner path did not reach the HTTPRoute"
grep -q "^    BASE = \"${BANNER_PATH}\"" "${T}/06-configmap-app.yaml" || die "banner path did not reach server.py"
grep -q "^    - \"${CODER_HOST}\"" "${T}/17-httproute.yaml" || die "hostname did not reach the HTTPRoute"
[[ $(grep -c "name: ${CODER_SERVICE}\$" "${T}/17-httproute.yaml") -ge 3 ]] || die "Coder Service did not reach the HTTPRoute"
grep -q "image: ${BANNER_IMAGE}\$" "${T}/09-deployment.yaml" || die "image did not reach the Deployment"
grep -q "ADMIN_ROLES, value: \"${ADMIN_ROLES}\"" "${T}/09-deployment.yaml" || die "admin roles did not reach the Deployment"
grep -qF '[\"'"${ADMIN_ROLES//,/'\",\"'}"'\"]' "${T}/15-middleware-menu-rewrite.yaml" || die "admin roles did not reach the menu Middleware"
grep -q "children:\`${MENU_LABEL}\`" "${T}/15-middleware-menu-rewrite.yaml" || die "menu label did not reach the menu Middleware"

# The answers, so a re-run or an upgrade can reuse them:  set -a; . rendered/answers.env; set +a; ./install.sh -y
{
 echo "# Answers used for this render ($(date -u +%Y-%m-%dT%H:%M:%SZ)). Re-use: set -a; . rendered/answers.env; set +a; ./install.sh -y"
 for v in CODER_NAMESPACE CODER_SERVICE CODER_SERVICE_PORT CODER_HOST BANNER_PATH ADMIN_ROLES MENU_LABEL GATEWAY_NAMESPACE \
          GATEWAY_NAME GATEWAY_SECTION BANNER_IMAGE IMAGE_PULL_SECRET TRAEFIK_NAMESPACE TRAEFIK_DEPLOYMENT; do
  printf '%s=%q\n' "${v}" "${!v}"
 done
 echo "LOAD_IMAGE=no"
} > "${W}/answers.env"
ok "rendered $(find "${W}/coder-banner" -name '*.yaml' | wc -l) banner manifests + Traefik plugin files"

if [[ "${MODE}" == render ]]; then
 rm -rf "${OUT}"; mv "${W}" "${OUT}"
 cat <<EOF

Rendered only; nothing on any cluster was read or changed. To install these files by hand, see README.md
(steps 3 and 4) using ${OUT}/ instead of the template directories. The Traefik patch is not rendered in this mode
because it depends on the running Deployment: run ./install.sh (or --dry-run) against the cluster for it.
EOF
 exit 0
fi

# ============================================================================================================ 4. preflight
step "Pre-flight (read-only)"
helm_owned=""
while IFS= read -r f; do
 kind=$(awk '/^kind:/ { print $2; exit }' "${f}")
 name=$(awk '/^metadata:/ { m = 1; next } m && /^  name:/ { print $2; exit }' "${f}")
 res=${kind,,}; [[ "${kind}" == Middleware ]] && res=middlewares.traefik.io; [[ "${kind}" == HTTPRoute ]] && res=httproutes.gateway.networking.k8s.io
 owner=$(kubectl -n "${CODER_NAMESPACE}" get "${res}" "${name}" -o 'jsonpath={.metadata.labels.app\.kubernetes\.io/name}{"|"}{.metadata.annotations.meta\.helm\.sh/release-name}' 2>/dev/null) || continue
 [[ "${owner%%|*}" == coder-banner ]] || die "${kind} ${CODER_NAMESPACE}/${name} already exists and does NOT belong to the banner; it would be overwritten. Not touching it."
 [[ -n "${owner#*|}" ]] && helm_owned=${owner#*|}
done < <(find "${W}/coder-banner" -name '*.yaml' | sort)
ok "no existing object of anyone else would be overwritten"
if [[ -n "${helm_owned}" ]]; then
 warn "the banner is currently installed by the Helm release '${helm_owned}'. Continuing takes its objects over with kubectl;"
 info "afterwards do NOT run 'helm uninstall ${helm_owned}' (it would delete them). Drop the release record with:"
 info "  kubectl -n ${CODER_NAMESPACE} delete secret -l owner=helm,name=${helm_owned}"
 yesno CONTINUE_HELM "Take over the Helm-installed banner?" no
 [[ "${CONTINUE_HELM}" == yes ]] || die "stopped: nothing was changed"
fi
# An HTTP->HTTPS redirect route for this host would be shadowed on a listener the banner route also attaches to.
redirects=$(kubectl get httproute -A -o 'jsonpath={range .items[*]}{.metadata.namespace}/{.metadata.name}{"\t"}{.spec.hostnames[*]}{"\t"}{.spec.rules[*].filters[*].type}{"\n"}{end}' 2>/dev/null | awk -F'\t' -v h="${CODER_HOST}" '$3 ~ /RequestRedirect/ && ($2 == "" || index(" " $2 " ", " " h " ")) { print $1 }' || true)
if [[ -n "${redirects}" && -z "${GATEWAY_SECTION}" ]]; then
 warn "redirect route(s) ${redirects//$'\n'/ } serve this host, and the banner route attaches to every listener: page loads over"
 info "plain HTTP would get the banner instead of the redirect. Re-run with the HTTPS listener as the Gateway listener."
fi

info "asking the API server to validate everything (server-side dry run) ..."
kubectl apply --server-side --force-conflicts --field-manager="${FIELD_MANAGER}" --dry-run=server -f "${W}/coder-banner/" >/dev/null \
 || die "the API server rejects the banner manifests (see above)"
ok "banner manifests accepted (dry run)"
if [[ "${ENABLE_PLUGIN:-no}" == yes ]]; then
 kubectl apply --server-side --force-conflicts --field-manager="${PLUGIN_MANAGER}" --dry-run=server -f "${W}/traefik-plugin/01-configmap-rewrite-body-plugin.yaml" >/dev/null \
  || die "the API server rejects the plugin ConfigMap"
 kubectl -n "${TRAEFIK_NAMESPACE}" patch deploy "${TRAEFIK_DEPLOYMENT}" --type=json -p "${PATCH_JSON}" --dry-run=server -o name >/dev/null \
  || die "the API server rejects the Traefik patch"
 ok "Traefik plugin ConfigMap and patch accepted (dry run)"
fi

# ============================================================================================================ 5. summary
step "Summary"
cat <<EOF
  Coder            https://${CODER_HOST}  ->  Service ${CODER_NAMESPACE}/${CODER_SERVICE}:${CODER_SERVICE_PORT}
  Admin page       ${B}https://${CODER_HOST}${BANNER_PATH}/admin${N}   (Coder roles: ${ADMIN_ROLES}; menu item "${MENU_LABEL}")
  Route            HTTPRoute ${CODER_NAMESPACE}/coder-banner on Gateway ${GATEWAY_NAMESPACE}/${GATEWAY_NAME}${GATEWAY_SECTION:+, listener ${GATEWAY_SECTION}}
  Image            ${BANNER_IMAGE}${IMAGE_PULL_SECRET:+ (pull secret ${IMAGE_PULL_SECRET})}$( [[ "${LOAD_IMAGE:-no}" == yes ]] && echo "  <- pushed from ${IMAGE_TAR}")
  Traefik plugin   $(case "${ENABLE_PLUGIN:-no}" in yes) echo "patch deployment/${TRAEFIK_DEPLOYMENT} in ${TRAEFIK_NAMESPACE} (new Traefik pod)" ;; already) echo "already enabled: Traefik untouched" ;; *) echo "${Y}NOT enabled${N}: the banner stays inactive until Traefik loads the plugin" ;; esac)
  Starts           hidden, with an empty message, until an admin publishes one
EOF
if [[ "${MODE}" == dry-run ]]; then
 rm -rf "${HERE}/rendered.dry-run"; mv "${W}" "${HERE}/rendered.dry-run"
 printf '\nDry run: everything above was validated by the API server; nothing was changed. Files: %s\n' "${HERE}/rendered.dry-run"
 exit 0
fi
yesno GO "Install now?" yes
[[ "${GO}" == yes ]] || die "stopped: nothing was changed (rendered files are in ${W})"

# ============================================================================================================ 6. image
if [[ "${LOAD_IMAGE:-no}" == yes ]]; then
 step "Push the image"
 if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then eng=docker
 elif command -v podman >/dev/null 2>&1; then eng=podman
 elif command -v skopeo >/dev/null 2>&1; then eng=skopeo
 else die "none of docker, podman or skopeo can be used here: push ${IMAGE_TAR} to ${BANNER_IMAGE} another way and re-run with LOAD_IMAGE=no"; fi
 if [[ "${eng}" == skopeo ]]; then
  run skopeo copy "docker-archive:${IMAGE_TAR}:${BUNDLED_IMAGE}" "docker://${BANNER_IMAGE}"
 else
  run "${eng}" load -i "${IMAGE_TAR}"
  run "${eng}" tag "${BUNDLED_IMAGE}" "${BANNER_IMAGE}"
  run "${eng}" push "${BANNER_IMAGE}" || die "push failed (registry login? '${eng} login <registry>'; plain-HTTP registry? configure it as insecure)"
 fi
 ok "pushed ${BANNER_IMAGE}"
fi

# ============================================================================================================ 7. traefik
plugin_health() { # prints loaded | disabled | unknown (+ Traefik's reason on a second line)
 local log=""
 for _ in 1 2 3 4 5 6 7 8 9 10; do
  log=$(kubectl -n "${TRAEFIK_NAMESPACE}" logs "deployment/${TRAEFIK_DEPLOYMENT}" -c "${TRAEFIK_CONTAINER}" --limit-bytes=200000 2>/dev/null || true)
  grep -qE 'Plugins loaded|Plugins are disabled' <<<"${log}" && break
  sleep 2
 done
 if grep -q 'Plugins are disabled' <<<"${log}"; then echo disabled; awk '/Plugins are disabled/ && !d { gsub(/\x1b\[[0-9;]*m/, ""); print substr($0, 1, 240); d = 1 }' <<<"${log}"
 elif grep -q 'Plugins loaded' <<<"${log}"; then echo loaded
 else echo unknown; fi
}
if [[ "${ENABLE_PLUGIN:-no}" == yes ]]; then
 step "Enable the ${PLUGIN_NAME} plugin in Traefik"
 cm_created=0; exists -n "${TRAEFIK_NAMESPACE}" configmap "${PLUGIN_CM}" || cm_created=1
 run kubectl apply --server-side --force-conflicts --field-manager="${PLUGIN_MANAGER}" -f "${W}/traefik-plugin/01-configmap-rewrite-body-plugin.yaml"
 undo_plugin() {
  warn "rolling Traefik back to its previous configuration"
  kubectl -n "${TRAEFIK_NAMESPACE}" rollout undo "deployment/${TRAEFIK_DEPLOYMENT}" || true
  kubectl -n "${TRAEFIK_NAMESPACE}" rollout status "deployment/${TRAEFIK_DEPLOYMENT}" --timeout=180s || true
  if (( cm_created )); then kubectl -n "${TRAEFIK_NAMESPACE}" delete configmap "${PLUGIN_CM}" --ignore-not-found >/dev/null || true; fi
 }
 run kubectl -n "${TRAEFIK_NAMESPACE}" patch deploy "${TRAEFIK_DEPLOYMENT}" --type=json -p "${PATCH_JSON}" --field-manager="${PLUGIN_MANAGER}"
 if ! run kubectl -n "${TRAEFIK_NAMESPACE}" rollout status "deployment/${TRAEFIK_DEPLOYMENT}" --timeout=180s; then
  kubectl -n "${TRAEFIK_NAMESPACE}" logs "deployment/${TRAEFIK_DEPLOYMENT}" --tail=20 >&2 || true
  undo_plugin; die "Traefik did not become Ready with the plugin; it was rolled back. Nothing of the banner was installed."
 fi
 health=$(plugin_health)
 case "${health%%$'\n'*}" in
  loaded) ok "Traefik reports: Plugins loaded" ;;
  disabled) warn "Traefik is up but DISABLED its plugins: $(sed -n 2p <<<"${health}")"; undo_plugin
            die "the plugin was rejected by Traefik (usually the same plugin name defined twice); Traefik was rolled back" ;;
  *) warn "could not confirm from Traefik's log that the plugin loaded; continuing (check: kubectl -n ${TRAEFIK_NAMESPACE} logs deploy/${TRAEFIK_DEPLOYMENT} | grep -i plugin)" ;;
 esac
elif [[ "${ENABLE_PLUGIN:-no}" == already ]]; then
 health=$(plugin_health)
 case "${health%%$'\n'*}" in
  loaded) ok "Traefik reports: Plugins loaded" ;;
  disabled) die "the plugin is configured but Traefik DISABLED its plugins: $(sed -n 2p <<<"${health}")" ;;
  *) warn "could not confirm from Traefik's log that the plugin is loaded" ;;
 esac
fi

VERIFY_WARNINGS=${WARNINGS}   # warnings from here on are about the result
# ============================================================================================================ 8. banner
step "Install the banner"
run kubectl apply --server-side --force-conflicts --field-manager="${FIELD_MANAGER}" -f "${W}/coder-banner/"
rm -rf "${OUT}"; mv "${W}" "${OUT}"   # this render is now what is installed
if ! run kubectl -n "${CODER_NAMESPACE}" rollout status deploy/coder-banner --timeout=180s; then
 kubectl -n "${CODER_NAMESPACE}" get pods -l app.kubernetes.io/name=coder-banner >&2 || true
 kubectl -n "${CODER_NAMESPACE}" get events --field-selector involvedObject.kind=Pod --sort-by=.lastTimestamp 2>/dev/null | grep coder-banner | tail -8 >&2 || true
 die "the banner pod did not become Ready (ImagePullBackOff? check the image name, the pull secret and the node architecture). Coder is unaffected."
fi
ok "deployment/coder-banner is Ready"
for _ in $(seq 1 15); do
 conds=$(kubectl -n "${CODER_NAMESPACE}" get httproute coder-banner -o 'jsonpath={range .status.parents[*].conditions[*]}{.type}={.status} {end}' 2>/dev/null || true)
 [[ "${conds}" == *Accepted=True* && "${conds}" == *ResolvedRefs=True* ]] && break
 sleep 2
done
if [[ "${conds}" == *Accepted=True* && "${conds}" == *ResolvedRefs=True* ]]; then ok "HTTPRoute coder-banner: ${conds}"
else warn "HTTPRoute coder-banner: ${conds:-no status yet}   (kubectl -n ${CODER_NAMESPACE} describe httproute coder-banner)"; fi

# ============================================================================================================ 9. verify
step "Verify through Traefik"
pf_pid=""
cleanup() { [[ -n "${pf_pid}" ]] && kill "${pf_pid}" 2>/dev/null || true; }
trap cleanup EXIT
BASE_URL="https://${CODER_HOST}"; CURL=(curl -sk --max-time 10)
if ! "${CURL[@]}" -o /dev/null "${BASE_URL}/healthz" 2>/dev/null; then
 # Not reachable from here: go through a port-forward to Traefik on the listener's port, with the right Host/SNI.
 lport=""; lproto=HTTPS
 if [[ -n "${GATEWAY_SECTION}" ]]; then IFS='|' read -r _ lproto lport _ <<<"$(awk -F'|' -v s="${GATEWAY_SECTION}" '$1 == s' <<<"${listeners}")"
 else IFS='|' read -r _ lproto lport _ <<<"$(awk -F'|' '$2 == "HTTPS" { print; exit }' <<<"${listeners}")"; fi
 if [[ -n "${lport}" ]]; then
  local_port=$(( 20000 + RANDOM % 20000 ))
  kubectl -n "${TRAEFIK_NAMESPACE}" port-forward "deployment/${TRAEFIK_DEPLOYMENT}" "${local_port}:${lport}" >/dev/null 2>&1 & pf_pid=$!
  sleep 3
  scheme=https; [[ "${lproto}" == HTTP ]] && scheme=http
  BASE_URL="${scheme}://${CODER_HOST}:${local_port}"; CURL+=(--resolve "${CODER_HOST}:${local_port}:127.0.0.1")
  info "${CODER_HOST} is not reachable from this machine: checking through a port-forward to Traefik (port ${lport})"
 fi
fi
injected=0
for _ in $(seq 1 15); do   # Traefik picks up new routes and middlewares within a few seconds
 html=$("${CURL[@]}" -H 'Accept: text/html' "${BASE_URL}/login" 2>/dev/null || true)
 [[ "${html}" == *"${BANNER_PATH}/banner.js"* ]] && { injected=1; break; }
 sleep 2
done
if (( injected )); then ok "Coder's page carries the banner loader (${BANNER_PATH}/banner.js)"
else warn "Coder's page does not carry ${BANNER_PATH}/banner.js yet (plugin not loaded? route not Accepted? check Traefik's log)"; fi
json=$("${CURL[@]}" "${BASE_URL}${BANNER_PATH}/banner.json" 2>/dev/null || true)
if [[ "${json}" == *'"enabled"'* ]]; then ok "${BANNER_PATH}/banner.json answers"
else warn "${BANNER_PATH}/banner.json did not answer as expected"; fi
chunk=$(grep -o '/assets/index-[A-Za-z0-9_-]*\.js' <<<"${html}" || true); chunk=${chunk%%$'\n'*}
if [[ -n "${chunk}" ]]; then
 js=$("${CURL[@]}" --compressed "${BASE_URL}${chunk}" 2>/dev/null || true)
 if [[ "${js}" == *"${BANNER_PATH}/admin"* ]]; then ok "Coder's user menu has the '${MENU_LABEL}' link"
 else warn "the user-menu link was not applied (made for Coder v2.36.0; another version keeps its stock menu, the banner itself still works)"; fi
fi

if (( VERIFY_WARNINGS < WARNINGS )); then
 printf '\n%sInstalled, but %d check(s) after the install need attention (WARN above).%s Coder itself keeps working.\n' "${Y}${B}" "$((WARNINGS - VERIFY_WARNINGS))" "${N}"
else printf '\n%sInstalled and verified.%s\n' "${G}${B}" "${N}"; fi
cat <<EOF
  Admin page   ${B}https://${CODER_HOST}${BANNER_PATH}/admin${N}   (sign in to Coder as: ${ADMIN_ROLES})
               or Coder's user menu -> ${MENU_LABEL} (one hard refresh, Ctrl+Shift+R, for browsers that cached Coder)
  The banner starts hidden: write a message there and press Publish; it appears at once in every open Coder tab.

  Rendered files and your answers: ${OUT}/   (re-run or upgrade: set -a; . rendered/answers.env; set +a; ./install.sh -y)
  Remove: kubectl delete -f ${OUT}/coder-banner/   (the Traefik plugin: README.md, "Removal")
EOF

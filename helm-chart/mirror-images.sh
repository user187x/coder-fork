#!/usr/bin/env bash
# Moves the images in images.txt across the air gap with docker.
#
# The bundle carries every image as a docker-save archive (images/README.md lists them):
#   images/custom/             the fork's Coder image - on no public registry: always keep
#   images/public/chart/       the public images the chart runs
#   images/public/prereqs/     CloudNativePG operator, Traefik (for prereqs/)
#   images/public/platform/    Argo CD + Keycloak as the reference cluster runs them - reference only
# For a smaller bundle delete images/public/ (or any folder in it): push then takes those images from the local
# docker or pulls them, and `save` on a connected machine writes the archives again.
#
#   ./mirror-images.sh push REGISTRY [GROUP...]
#                                   tag and push each image as REGISTRY/<path>, e.g. ghcr.io/user187x/coder-platform:T
#                                   -> REGISTRY/user187x/coder-platform:T (what the example values use). Source: its
#                                   archive under images/, else the local docker, else docker pull (by digest).
#                                   GROUP: custom chart prereqs platform all (default: custom chart prereqs).
#                                   Public images already in REGISTRY?  push REGISTRY custom
#   ./mirror-images.sh save [GROUP...]
#                                   connected side: pull the public images by digest and (re)write their archives
#                                   (default: chart prereqs platform)
#   ./mirror-images.sh verify       check every archive present against its folder's SHA256SUMS
#   ./mirror-images.sh list [REGISTRY]
#                                   group, image, archive present or not (-> target); changes nothing
#   ./mirror-images.sh chart REPO   package ./coder-airgapped and helm push it to oci://REPO (e.g. <registry>/charts),
#                                   the repository argocd/coder-airgapped-application.yaml pulls from
#                                   (helm registry login <registry> first; ECR=1 creates the ECR repository)
#   ./mirror-images.sh charts REPO  the same, and the prerequisites' charts (prereqs/*.tgz: CloudNativePG, Traefik)
#   ./mirror-images.sh charts-list  name and version of each of those charts
#
#   ./mirror-images.sh seed-builder [NAMESPACE]
#                                   the image builder's base: pushes the workspace image into the in-cluster registry
#                                   (coder-image-registry, through kubectl port-forward) as coder-images/base/<name:tag>.
#                                   Any cluster kubectl reaches (EKS, kind...); no-op when it is there already.
#                                   `./make --deploy` runs it; run it yourself after an Argo CD install.
#
#   ECR=1 ./mirror-images.sh push ...    create missing ECR repositories first (needs the aws CLI)
#   PLAIN_HTTP=1 ./mirror-images.sh chart ...   registry without TLS
#   HELM_PUSH_ARGS="--ca-file ca.pem" ./mirror-images.sh chart(s) ...   more helm push options (a private CA)
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ALL_GROUPS=(custom chart prereqs platform)

# images.txt -> parallel arrays (a "[group]" line starts a group)
groups=() images=() digests=() g=""
while read -r a b _; do
	[[ -z "$a" || "$a" == \#* ]] && continue
	if [[ "$a" == \[*\] ]]; then g="${a:1:-1}"; continue; fi
	groups+=("$g") images+=("$a") digests+=("$b")
done <"${here}/images.txt"

target() { # strip the source registry host
	local ref="$1" first="${1%%/*}"
	if [[ "$first" == *.* || "$first" == *:* ]]; then ref="${ref#*/}"; fi
	echo "$2/${ref}"
}
dir_of() { if [[ "$1" == custom ]]; then echo "${here}/images/custom"; else echo "${here}/images/public/$1"; fi; }
archive() { echo "$(dir_of "${groups[$1]}")/$(echo "${images[$1]}" | tr '/:@' '___').tar.gz"; }
rel() { echo "${1#"${here}"/}"; }
pick() { # pick DEFAULT... -- ARGS...: indexes of the images in the requested groups
	local -a want=() def=()
	while [[ "$1" != -- ]]; do def+=("$1"); shift; done; shift
	for w in "$@"; do
		[[ "$w" == all ]] && { want=("${ALL_GROUPS[@]}"); break; }
		[[ " ${ALL_GROUPS[*]} " == *" $w "* ]] || { echo "ERROR: unknown group '$w' (${ALL_GROUPS[*]} all)" >&2; exit 2; }
		want+=("$w")
	done
	((${#want[@]})) || want=("${def[@]}")
	for i in "${!images[@]}"; do [[ " ${want[*]} " != *" ${groups[$i]} "* ]] || echo "$i"; done
}
check() { # archive against its folder's SHA256SUMS
	local d n; d=$(dirname "$1"); n=$(basename "$1")
	grep -qF "  ${n}" "$d/SHA256SUMS" 2>/dev/null || { echo "ERROR: $(rel "$1") is not listed in $(rel "$d")/SHA256SUMS" >&2; exit 1; }
	(cd "$d" && grep -F "  ${n}" SHA256SUMS | sha256sum -c --quiet -) || { echo "ERROR: checksum mismatch: $(rel "$1")" >&2; exit 1; }
}
pull() { # index: by digest when images.txt has one
	local img=${images[$1]} dg=${digests[$1]}
	if [[ "$dg" == sha256:* ]]; then
		docker pull --platform linux/amd64 "${img}@${dg}" && docker tag "${img}@${dg}" "$img"
	else
		docker pull --platform linux/amd64 "$img"
	fi
}
ensure() { # index: the image in the local docker - from its archive when the bundle has it
	local img=${images[$1]} f; f=$(archive "$1")
	if [[ -f "$f" ]]; then
		check "$f"; echo "loading $img from $(rel "$f")"; docker load -q -i "$f" >/dev/null
	elif docker image inspect "$img" >/dev/null 2>&1; then
		echo "using $img from the local docker ($(rel "$f") is not in the bundle)"
	elif [[ "${groups[$1]}" == custom ]]; then
		echo "ERROR: $img is on no public registry and $(rel "$f") is missing" >&2; exit 1
	else
		echo "pulling $img ($(rel "$f") is not in the bundle)"; pull "$1"
	fi
}

cmd="${1:-}"; shift || true
case "$cmd" in
save)
	zip=$(command -v pigz || echo gzip)
	mapfile -t idx < <(pick chart prereqs platform -- "$@")
	for i in "${idx[@]}"; do
		[[ "${groups[$i]}" != custom ]] || { echo "skipping ${images[$i]}: custom, only built (development/tools/build-airgap-image.sh)"; continue; }
		f=$(archive "$i"); mkdir -p "$(dirname "$f")"
		pull "$i"
		echo "saving ${images[$i]} -> $(rel "$f")"
		docker save --platform linux/amd64 "${images[$i]}" | "$zip" -6 >"$f.part"; mv "$f.part" "$f"
		(cd "$(dirname "$f")" && sha256sum ./*.tar.gz | sed 's|  \./|  |' >SHA256SUMS)
	done
	;;
push)
	reg="${1:?usage: push REGISTRY [GROUP...]}"; shift; reg="${reg%/}"
	mapfile -t idx < <(pick custom chart prereqs -- "$@")
	for i in "${idx[@]}"; do
		img=${images[$i]}; ensure "$i"
		dst=$(target "$img" "$reg")
		if [[ "${ECR:-0}" == 1 ]]; then
			repo="${dst#*/}"; repo="${repo%:*}"
			aws ecr describe-repositories --repository-names "$repo" >/dev/null 2>&1 ||
				aws ecr create-repository --repository-name "$repo" >/dev/null
		fi
		echo "$img -> $dst"; docker tag "$img" "$dst"
		# A registry on slow storage can time out on a large layer: try again (pushed layers are not sent twice).
		for try in 1 2 3; do docker push -q "$dst" && break; ((try < 3)) || exit 1; echo "push failed, retrying ($try/3)"; sleep 10; done
	done
	;;
verify)
	n=0
	for i in "${!images[@]}"; do
		f=$(archive "$i")
		if [[ -f "$f" ]]; then check "$f"; echo "ok       $(rel "$f")"; n=$((n + 1)); else echo "absent   $(rel "$f")"; fi
	done
	echo "$n archives verified"
	;;
chart | charts)
	repo="${1:?usage: $cmd REPO}"; repo="${repo#oci://}"; repo="${repo%/}"
	command -v helm >/dev/null || { echo "ERROR: helm not found" >&2; exit 1; }
	tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
	pkgs=("$(helm package "${here}/coder-airgapped" -d "$tmp" | sed -n 's/.*saved it to: //p')")
	[[ "$cmd" == chart ]] || pkgs+=("${here}"/prereqs/*.tgz)
	read -r -a extra <<<"${HELM_PUSH_ARGS:-}"
	[[ "${PLAIN_HTTP:-0}" == 1 ]] && extra+=(--plain-http)
	for pkg in "${pkgs[@]}"; do
		if [[ "${ECR:-0}" == 1 ]]; then
			name=$(basename "$pkg"); name="${name%-*}"; ecr_repo="${repo#*/}/${name}"
			aws ecr describe-repositories --repository-names "$ecr_repo" >/dev/null 2>&1 ||
				aws ecr create-repository --repository-name "$ecr_repo" >/dev/null
		fi
		echo "$(basename "$pkg") -> oci://${repo}"
		helm push "$pkg" "oci://${repo}" "${extra[@]}" 2>&1 | grep -v '^Pushed:\|^Digest:' || true
		((PIPESTATUS[0] == 0)) || exit 1
	done
	;;
charts-list)
	for c in "${here}/coder-airgapped" "${here}"/prereqs/*.tgz; do
		helm show chart "$c" | awk '/^name:/ {n=$2} /^version:/ {v=$2} END {print n, v}'
	done
	;;
seed-builder)
	ns="${1:-coder}"
	ws=""; for i in "${!images[@]}"; do [[ "${images[$i]}" == ghcr.io/user187x/coder-workspace:* ]] && { ws=$i; break; }; done
	[[ -n "$ws" ]] || { echo "ERROR: no workspace image in images.txt" >&2; exit 1; }
	name="${images[$ws]##*/}"
	kubectl -n "$ns" rollout status deploy/coder-image-registry --timeout=300s >/dev/null ||
		{ echo "ERROR: deploy/coder-image-registry in namespace $ns is not ready (imageBuilder.enabled?)" >&2; exit 1; }
	port=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')
	kubectl -n "$ns" port-forward svc/coder-image-registry "${port}:5000" >/dev/null 2>&1 & pf=$!
	trap 'kill "$pf" 2>/dev/null || true' EXIT
	for _ in $(seq 60); do curl -fs -o /dev/null "http://127.0.0.1:${port}/v2/" && break; sleep 1; done
	curl -fs -o /dev/null "http://127.0.0.1:${port}/v2/" || { echo "ERROR: the registry does not answer through port-forward" >&2; exit 1; }
	if curl -fs -o /dev/null -H "Accept: application/vnd.docker.distribution.manifest.v2+json" \
		"http://127.0.0.1:${port}/v2/coder-images/base/${name%%:*}/manifests/${name##*:}"; then
		echo "the image builder's registry has coder-images/base/${name} already"; exit 0
	fi
	ensure "$ws"
	# Docker pushes to a registry on 127.0.0.1 over plain HTTP without any daemon setting.
	dst="127.0.0.1:${port}/coder-images/base/${name}"
	echo "${images[$ws]} -> coder-images/base/${name} (the image builder's registry; a few minutes)"
	docker tag "${images[$ws]}" "$dst" && docker push -q "$dst" >/dev/null && docker rmi "$dst" >/dev/null
	echo "seeded coder-images/base/${name}"
	;;
list)
	reg="${1:-}"
	for i in "${!images[@]}"; do
		f=$(archive "$i"); have=absent; [[ -f "$f" ]] && have=bundled
		printf '%-9s %-8s %-56s %s\n' "${groups[$i]}" "$have" "${images[$i]}" "${reg:+-> $(target "${images[$i]}" "$reg")}"
	done
	;;
*) sed -n '2,/^set /p' "$0" | sed '$d'; exit 2 ;;
esac

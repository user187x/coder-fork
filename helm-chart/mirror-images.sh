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
#
#   ECR=1 ./mirror-images.sh push ...    create missing ECR repositories first (needs the aws CLI)
#   PLAIN_HTTP=1 ./mirror-images.sh chart ...   registry without TLS
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
		echo "$img -> $dst"; docker tag "$img" "$dst"; docker push -q "$dst"
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
chart)
	repo="${1:?usage: chart REPO}"; repo="${repo#oci://}"; repo="${repo%/}"
	command -v helm >/dev/null || { echo "ERROR: helm not found" >&2; exit 1; }
	tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
	pkg=$(helm package "${here}/coder-airgapped" -d "$tmp" | sed -n 's/.*saved it to: //p')
	if [[ "${ECR:-0}" == 1 ]]; then
		name=$(basename "$pkg"); name="${name%-*}"; ecr_repo="${repo#*/}/${name}"
		aws ecr describe-repositories --repository-names "$ecr_repo" >/dev/null 2>&1 ||
			aws ecr create-repository --repository-name "$ecr_repo" >/dev/null
	fi
	helm push "$pkg" "oci://${repo}" $([[ "${PLAIN_HTTP:-0}" == 1 ]] && echo --plain-http)
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

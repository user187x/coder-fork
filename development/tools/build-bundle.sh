#!/usr/bin/env bash
# Builds the delivery tarballs from ../../helm-chart into ../../dist/:
#   coder-airgapped-<version>.tar.gz        everything, every image (images/custom + images/public)
#   coder-airgapped-<version>-slim.tar.gz   the same without images/public (only the custom Coder image)
#   coder-airgapped-<version>-layer.tar.gz  the customization layer alone, for a cluster that runs an earlier release
#                                           (the base, by default the newest release tag before <version>): this chart
#                                           and its installers, and only the images changed since the base - ours thin
#                                           (the layers the base image lacks) when the base image is in this docker or
#                                           a Minikube node (LAYER_PROFILE, default coder); no third-party chart or
#                                           image unless it changed (helm-chart/layer-support)
# each with a .sha256. <version> is the chart's version. Lints and renders the chart first. Public image archives that
# are missing are fetched with `mirror-images.sh save` (needs internet; --no-save fails instead). The custom image
# archive must exist (tools/build-airgap-image.sh).
#   ./build-bundle.sh [--no-save] [--layer-base VERSION | --no-layer]
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; top="$(cd "${here}/../.." && pwd)"
bundle="${top}/helm-chart"; chart="${bundle}/coder-airgapped"; dist="${top}/dist"
version=$(sed -n 's/^version: *//p' "${chart}/Chart.yaml"); name="coder-airgapped-${version}"
save=1 layer_base=""
while (($#)); do
	case "$1" in
	--no-save) save=0 ;;
	--layer-base) layer_base="${2:?--layer-base VERSION}"; shift ;;
	--no-layer) layer_base=none ;;
	*) echo "unknown option: $1" >&2; exit 2 ;;
	esac
	shift
done
if [[ -z "$layer_base" ]]; then # the newest release before this one
	layer_base=$(git -C "$top" tag -l 'v*' --sort=-v:refname | sed 's/^v//' |
		while read -r t; do [[ "$t" != "$version" && "$(printf '%s\n%s\n' "$t" "$version" | sort -V | tail -n1)" == "$version" ]] && { echo "$t"; break; }; done)
fi

helm lint "$chart" >/dev/null && echo "lint ok"
helm template coder "$chart" -n coder >/dev/null && echo "render ok"

mapfile -t absent < <("${bundle}/mirror-images.sh" list | awk '$2 == "absent" {print $1}' | sort -u)
for g in "${absent[@]}"; do
	[[ "$g" != custom ]] || { echo "ERROR: the custom image archive is missing: run tools/build-airgap-image.sh" >&2; exit 1; }
	((save)) || { echo "ERROR: images of group '$g' are missing (run without --no-save)" >&2; exit 1; }
done
((${#absent[@]} == 0)) || "${bundle}/mirror-images.sh" save "${absent[@]}"
"${bundle}/mirror-images.sh" verify | tail -n 1

zip=$(command -v pigz || echo gzip); mkdir -p "$dist"
build() { # OUT [tar options...]
	local out="$1"; shift
	tar -I "$zip" -C "$top" -cf "${dist}/${out}.part" --exclude='*.part' --exclude='__pycache__' --exclude='helm-chart/.deploy' "$@" \
		--transform "s|^helm-chart|${name}|" helm-chart
	mv "${dist}/${out}.part" "${dist}/${out}"; (cd "$dist" && sha256sum "$out" >"${out}.sha256")
	echo "wrote dist/${out} ($(du -h "${dist}/${out}" | cut -f1))"
}
build "${name}.tar.gz"
build "${name}-slim.tar.gz" --exclude='helm-chart/images/public'

# The layer: the chart and installers, without any image archive or third-party chart; then LAYER.json and the
# images (and prerequisites) changed since the base, staged under the same paths.
[[ -n "$layer_base" && "$layer_base" != none ]] || { echo "no layer bundle (no earlier release to build it on)"; exit 0; }
# (staged as layer-files/: the excludes below match names before --transform, so they leave the staged files alone)
staging="${dist}/.layer-${version}"; rm -rf "$staging"; mkdir -p "${staging}/layer-files"
"${bundle}/layer-support" build "$layer_base" "${staging}/layer-files"
while read -r f; do
	mkdir -p "${staging}/layer-files/prereqs"; cp "${bundle}/prereqs/${f}" "${staging}/layer-files/prereqs/"
done < <(python3 -c 'import json,sys; [print(f) for f in json.load(open(sys.argv[1]))["prereqs"]]' "${staging}/layer-files/LAYER.json")
out="${name}-layer.tar.gz"
tar -I "$zip" -cf "${dist}/${out}.part" --exclude='*.part' --exclude='__pycache__' --exclude='helm-chart/.deploy' \
	--exclude='helm-chart/images/public' --exclude='helm-chart/images/custom/*.tar.gz' \
	--exclude='helm-chart/images/custom/SHA256SUMS' --exclude='helm-chart/prereqs/*' \
	--transform "s|^helm-chart|${name}|;s|^layer-files|${name}|" -C "$top" helm-chart -C "$staging" layer-files
mv "${dist}/${out}.part" "${dist}/${out}"; (cd "$dist" && sha256sum "$out" >"${out}.sha256")
rm -rf "$staging"
echo "wrote dist/${out} ($(du -h "${dist}/${out}" | cut -f1)), the layer on ${layer_base}"

#!/usr/bin/env bash
# Builds the delivery tarballs from ../../helm-chart into ../../dist/:
#   coder-airgapped-<version>.tar.gz        everything, every image (images/custom + images/public)
#   coder-airgapped-<version>-slim.tar.gz   the same without images/public (only the custom Coder image)
# each with a .sha256. <version> is the chart's version. Lints and renders the chart first. Public image archives that
# are missing are fetched with `mirror-images.sh save` (needs internet; --no-save fails instead). The custom image
# archive must exist (tools/build-airgap-image.sh).
#   ./build-bundle.sh [--no-save]
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; top="$(cd "${here}/../.." && pwd)"
bundle="${top}/helm-chart"; chart="${bundle}/coder-airgapped"; dist="${top}/dist"
version=$(sed -n 's/^version: *//p' "${chart}/Chart.yaml"); name="coder-airgapped-${version}"

helm lint "$chart" >/dev/null && echo "lint ok"
helm template coder "$chart" -n coder >/dev/null && echo "render ok"

mapfile -t absent < <("${bundle}/mirror-images.sh" list | awk '$2 == "absent" {print $1}' | sort -u)
for g in "${absent[@]}"; do
	[[ "$g" != custom ]] || { echo "ERROR: the custom image archive is missing: run tools/build-airgap-image.sh" >&2; exit 1; }
	[[ "${1:-}" != --no-save ]] || { echo "ERROR: images of group '$g' are missing (run without --no-save)" >&2; exit 1; }
done
((${#absent[@]} == 0)) || "${bundle}/mirror-images.sh" save "${absent[@]}"
"${bundle}/mirror-images.sh" verify | tail -n 1

zip=$(command -v pigz || echo gzip); mkdir -p "$dist"
build() { # OUT [tar options...]
	local out="$1"; shift
	tar -I "$zip" -C "$top" -cf "${dist}/${out}.part" --exclude='*.part' --exclude='__pycache__' "$@" \
		--transform "s|^helm-chart|${name}|" helm-chart
	mv "${dist}/${out}.part" "${dist}/${out}"; (cd "$dist" && sha256sum "$out" >"${out}.sha256")
	echo "wrote dist/${out} ($(du -h "${dist}/${out}" | cut -f1))"
}
build "${name}.tar.gz"
build "${name}-slim.tar.gz" --exclude='helm-chart/images/public'

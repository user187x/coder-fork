#!/usr/bin/env bash
# Builds the delivery tarballs from ../../helm-chart into ../../dist/, each with a .sha256. <version> is the chart's.
#
# The bundle, one tarball:
#   coder-airgapped-<version>.tar.gz        everything: the installers, the chart (coder-airgapped/), every chart of ours
#                                           packaged (charts/: coder-airgapped, and the Argo CD decorator install's
#                                           coder-airgapped-{base,layer}; with its index.yaml, a Helm repository, and
#                                           SHA256SUMS), the prerequisites (prereqs/), and every image (images/<part>/,
#                                           one folder per section of images.txt: ui, workspaces, cloud-drive, ...)
#   coder-airgapped-<version>-slim.tar.gz   the same with only our images (no archive of a public image)
# The same release in parts, for a partial download or update (--no-split: not these). Unpacked into one directory
# they are the bundle above, file for file:
#   coder-airgapped-<version>-charts.tar.gz            the bundle without any image: installers, chart, charts/, prereqs/
#   coder-airgapped-<version>-images-<part>.tar.gz     one folder of images/ (its archives and SHA256SUMS), e.g. -images-ui
#                                                      (the user interface: Coder, the platform service, their databases)
#   coder-airgapped-<version>-ASSETS.txt               which asset holds what
# And for a cluster that runs an earlier release:
#   coder-airgapped-<version>-layer.tar.gz  the customization layer alone, on the base (by default the newest release tag
#                                           before <version>): this chart and its installers, and only the images
#                                           changed since the base - ours thin (the layers the base image lacks) when the
#                                           base image is in this docker or a Minikube node (LAYER_PROFILE, default
#                                           coder); no third-party chart or image unless it changed (helm-chart/layer-support)
# Lints and renders the chart first. Public image archives that are missing are fetched with `mirror-images.sh save`
# (needs internet; --no-save fails instead). Our image archives must exist (tools/build-*.sh).
#   ./build-bundle.sh [--no-save] [--no-slim] [--no-split] [--layer-base VERSION | --no-layer] [--no-obsidian]
# --no-slim: not the slim tarball (not a release asset; it holds every image of ours, the dataflow one among them).
# --no-obsidian: no Obsidian in any of the tarballs - Obsidian is a private package, for the organization's own clusters,
#   so a bundle that leaves the organization (a public release) must not carry it: the Obsidian image's archive and
#   its images.txt line are left out, and the Obsidian template is off in the chart's values and in every packaged
#   chart. (The AI image's Unsloth Studio is AGPL-3.0 and ships; LM Studio, which it replaced, did not.)
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; top="$(cd "${here}/../.." && pwd)"
bundle="${top}/helm-chart"; chart="${bundle}/coder-airgapped"; dist="${top}/dist"
version=$(sed -n 's/^version: *//p' "${chart}/Chart.yaml"); name="coder-airgapped-${version}"
save=1 slim=1 split=1 layer_base="" private=1
while (($#)); do
	case "$1" in
	--no-save) save=0 ;;
	--no-slim) slim=0 ;;
	--no-split) split=0 ;;
	--layer-base) layer_base="${2:?--layer-base VERSION}"; shift ;;
	--no-layer) layer_base=none ;;
	--no-obsidian) private=0 ;;
	*) echo "unknown option: $1" >&2; exit 2 ;;
	esac
	shift
done
if [[ -z "$layer_base" ]]; then # the newest release before this one
	layer_base=$(git -C "$top" tag -l 'v*' --sort=-v:refname | sed 's/^v//' |
		while read -r t; do [[ "$t" != "$version" && "$(printf '%s\n%s\n' "$t" "$version" | sort -V | tail -n1)" == "$version" ]] && { echo "$t"; break; }; done)
fi
mirror() { "${bundle}/mirror-images.sh" "$@"; }

helm lint "$chart" >/dev/null && echo "lint ok"
helm template coder "$chart" -n coder >/dev/null && echo "render ok"

mapfile -t absent < <(mirror list | awk '$2 == "absent" {print $3}')
for img in "${absent[@]}"; do
	[[ "$img" != ghcr.io/user187x/* ]] || { echo "ERROR: our image's archive is missing: $(mirror path "$img") (tools/build-*.sh)" >&2; exit 1; }
	((save)) || { echo "ERROR: $(mirror path "$img") is missing (run without --no-save)" >&2; exit 1; }
done
((${#absent[@]} == 0)) || mirror save all
mirror verify | tail -n 1

zip=$(command -v pigz || echo gzip); mkdir -p "$dist"
ob_image=$(awk '!/^#/ && $1 ~ /^ghcr\.io\/user187x\/coder-workspace-obsidian:/ {print $1; exit}' "${bundle}/images.txt")
ob_archive=$(mirror path "$ob_image")
ob_off() { # VALUES OUT: the Obsidian template off
	python3 - "$1" "$2" <<'PY'
import re, sys
s = open(sys.argv[1]).read()
s, n = re.subn(r"(\n  obsidianTemplate:\n(?:    #.*\n)*    enabled: )true", r"\1false", s)
if n != 1:
    sys.exit("obsidianTemplate.enabled not found in " + sys.argv[1])
open(sys.argv[2], "w").write(s)
PY
}

# --no-obsidian: the files that change, staged under nolms/ (put in place of the originals, which are excluded).
nolms_dir="${dist}/.no-private-${version}" nolms=() nolms_src=()
rm -rf "$nolms_dir"
if ((!private)); then
	mkdir -p "${nolms_dir}/nolms/coder-airgapped" "${nolms_dir}/nolms/$(dirname "$ob_archive")"
	ob_off "${chart}/values.yaml" "${nolms_dir}/nolms/coder-airgapped/values.yaml"
	grep -vF "${ob_image} " "${bundle}/images.txt" >"${nolms_dir}/nolms/images.txt"
	grep -vF "  $(basename "$ob_archive")" "${bundle}/$(dirname "$ob_archive")/SHA256SUMS" \
		>"${nolms_dir}/nolms/$(dirname "$ob_archive")/SHA256SUMS"
	nolms=(--exclude="helm-chart/${ob_archive}" --exclude='helm-chart/images.txt'
		--exclude="helm-chart/$(dirname "$ob_archive")/SHA256SUMS" --exclude='helm-chart/coder-airgapped/values.yaml')
	nolms_src=(-C "$nolms_dir" nolms)
	echo "--no-obsidian: without the Obsidian image, the Obsidian template off"
fi

# charts/: every chart of ours packaged - from a copy with the Obsidian template off for --no-obsidian - with the index of
# a Helm repository and their checksums (git ignores the folder; every tarball ships it)
charts="${bundle}/charts"; rm -rf "$charts"; mkdir -p "$charts"
src_chart=$(mktemp -d)/coder-airgapped; cp -r "$chart" "$src_chart"
find "$src_chart" -name __pycache__ -type d -prune -exec rm -rf {} +
((private)) || ob_off "${chart}/values.yaml" "${src_chart}/values.yaml"
helm package "$src_chart" -d "$charts" | sed "s|.*saved it to: ${bundle}/|packaged |"
CHART_DIR="$src_chart" mirror decorator-charts "$charts" | sed "s|^${bundle}/|packaged |"
rm -rf "$(dirname "$src_chart")"
helm repo index "$charts"
(cd "$charts" && sha256sum ./*.tgz | sed 's|  \./|  |' >SHA256SUMS)

tarball() { # OUT [tar options...]: the bundle's tree as coder-airgapped-<version>/, but what the options leave out
	local out="$1"; shift
	tar -I "$zip" -C "$top" -cf "${dist}/${out}.part" --exclude='*.part' --exclude='__pycache__' --exclude='helm-chart/.deploy' \
		--exclude='helm-chart/LAYER.json' "$@" "${nolms[@]}" --transform "s|^helm-chart|${name}|;s|^nolms|${name}|" helm-chart "${nolms_src[@]}"
	mv "${dist}/${out}.part" "${dist}/${out}"; (cd "$dist" && sha256sum "$out" >"${out}.sha256")
	echo "wrote dist/${out} ($(du -h "${dist}/${out}" | cut -f1))"
}
tarball "${name}.tar.gz"

# The archives of public images (the slim tarball leaves them out)
public=()
while read -r _ _ img _; do
	[[ "$img" == ghcr.io/user187x/* ]] || public+=(--exclude="helm-chart/$(mirror path "$img")")
done < <(mirror list)
if ((slim)); then tarball "${name}-slim.tar.gz" "${public[@]}"; fi

# The parts: the bundle without images, then one tarball per images/ folder (its archives and SHA256SUMS)
if ((split)); then
	rm -f "${dist}/${name}"-charts.tar.gz* "${dist}/${name}"-images-*.tar.gz*
	tarball "${name}-charts.tar.gz" --exclude='helm-chart/images/*/*.tar.gz' --exclude='helm-chart/images/*/SHA256SUMS' \
		--exclude='nolms/images'
	assets="${dist}/${name}-ASSETS.txt"
	{
		echo "coder-airgapped ${version}: the release in parts. Unpack them all into one directory: together they are"
		echo "${name}.tar.gz, file for file (each unpacks into ${name}/). Check each against its .sha256."
		echo
		printf '%-58s %s\n' "${name}-charts.tar.gz" "installers, the chart, charts/ (every chart packaged), prereqs/, docs - no images"
	} >"$assets"
	for d in "${bundle}"/images/*/; do
		part=$(basename "$d"); out="${name}-images-${part}.tar.gz"
		ls "$d"*.tar.gz >/dev/null 2>&1 || continue
		desc=$(awk -v p="[${part}]" '$1 == p {for (i = 4; i <= NF; i++) printf "%s%s", $i, (i < NF ? " " : ""); exit}' "${bundle}/images.txt")
		# only this folder: every other path and file left out
		tar -I "$zip" -C "$top" -cf "${dist}/${out}.part" "${nolms[@]}" --transform "s|^helm-chart|${name}|;s|^nolms|${name}|" \
			"helm-chart/images/${part}" $([[ ! -d "${nolms_dir}/nolms/images/${part}" ]] || echo -C "$nolms_dir" "nolms/images/${part}")
		mv "${dist}/${out}.part" "${dist}/${out}"; (cd "$dist" && sha256sum "$out" >"${out}.sha256")
		echo "wrote dist/${out} ($(du -h "${dist}/${out}" | cut -f1))"
		printf '%-58s %s (%s)\n' "$out" "images/${part}/: ${desc}" "$(du -h "${dist}/${out}" | cut -f1)" >>"$assets"
	done
	echo "wrote dist/$(basename "$assets")"
fi

# The layer: the chart and installers, without any image archive or third-party chart; then LAYER.json and the
# images (and prerequisites) changed since the base, staged under the same paths.
[[ -n "$layer_base" && "$layer_base" != none ]] || { echo "no layer bundle (no earlier release to build it on)"; rm -rf "$nolms_dir"; exit 0; }
# (staged as layer-files/: the excludes below match names before --transform, so they leave the staged files alone)
staging="${dist}/.layer-${version}"; rm -rf "$staging"; mkdir -p "${staging}/layer-files"
# (--no-obsidian: the Obsidian image left out of the layer too)
LAYER_SKIP_IMAGES=$( ((private)) || echo '^ghcr\.io/user187x/coder-workspace-obsidian:') \
	"${bundle}/layer-support" build "$layer_base" "${staging}/layer-files"
while read -r f; do
	mkdir -p "${staging}/layer-files/prereqs"; cp "${bundle}/prereqs/${f}" "${staging}/layer-files/prereqs/"
done < <(python3 -c 'import json,sys; [print(f) for f in json.load(open(sys.argv[1]))["prereqs"]]' "${staging}/layer-files/LAYER.json")
out="${name}-layer.tar.gz"
tar -I "$zip" -cf "${dist}/${out}.part" --exclude='*.part' --exclude='__pycache__' --exclude='helm-chart/.deploy' \
	--exclude='helm-chart/images/*/*.tar.gz' --exclude='helm-chart/images/*/SHA256SUMS' --exclude='helm-chart/prereqs/*' \
	"${nolms[@]}" --transform "s|^helm-chart|${name}|;s|^layer-files|${name}|;s|^nolms|${name}|" -C "$top" helm-chart \
	-C "$staging" layer-files "${nolms_src[@]}"
mv "${dist}/${out}.part" "${dist}/${out}"; (cd "$dist" && sha256sum "$out" >"${out}.sha256")
rm -rf "$staging" "$nolms_dir"
echo "wrote dist/${out} ($(du -h "${dist}/${out}" | cut -f1)), the layer on ${layer_base}"

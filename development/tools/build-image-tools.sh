#!/usr/bin/env bash
# Builds ghcr.io/user187x/coder-image-tools:<TAG> (../image-tools): the platform service's image with shellcheck, shfmt,
# trivy (+ its vulnerability database, downloaded now), regctl and copa (Copacetic: the Images view's pit stops patch
# images with it, through BuildKit), and saves it into the delivery bundle
# (helm-chart/images/custom/), replacing the previous one. Needs internet (the binaries and the database).
# Then update its [custom] line (tag, digest) in helm-chart/images.txt and uiUpdates.image in values.yaml.
#   ./build-image-tools.sh [TAG]           default 1.1.0
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; dev="$(dirname "$here")"; top="$(dirname "$dev")"
tag="${1:-1.1.0}"; img="ghcr.io/user187x/coder-image-tools:${tag}"
cache="${XDG_CACHE_HOME:-$HOME/.cache}/coder-airgapped/image-tools"; mkdir -p "$cache"
ctx="$(mktemp -d)"; trap 'rm -rf "$ctx"' EXIT
cp "${dev}/image-tools/Dockerfile" "$ctx/"; mkdir -p "$ctx/bin" "$ctx/trivy-cache"

fetch() { # URL SHA256 -> path of the verified download (kept in the cache)
	local url="$1" sum="$2" file="${cache}/$(basename "$1")"
	if [[ ! -f "$file" ]] || ! echo "${sum}  ${file}" | sha256sum -c --quiet 2>/dev/null; then
		curl -fsSL -o "${file}.part" "$url"
		echo "${sum}  ${file}.part" | sha256sum -c --quiet || { rm -f "${file}.part"; echo "ERROR: checksum mismatch: $url" >&2; exit 1; }
		mv "${file}.part" "$file"
	fi
	echo "$file"
}
gh=https://github.com
f=$(fetch "$gh/koalaman/shellcheck/releases/download/v0.11.0/shellcheck-v0.11.0.linux.x86_64.tar.xz" \
	8c3be12b05d5c177a04c29e3c78ce89ac86f1595681cab149b65b97c4e227198)
tar -xJf "$f" -C "$ctx" && mv "$ctx/shellcheck-v0.11.0/shellcheck" "$ctx/bin/" && rm -rf "$ctx/shellcheck-v0.11.0"
f=$(fetch "$gh/mvdan/sh/releases/download/v3.14.1/shfmt_v3.14.1_linux_amd64" \
	76e77641faa025814b77f153b29796b8e6fa2fca03e0c76a691608b86c7ea7bf)
install -m 0755 "$f" "$ctx/bin/shfmt"
f=$(fetch "$gh/aquasecurity/trivy/releases/download/v0.74.0/trivy_0.74.0_Linux-64bit.tar.gz" \
	2ae6fe3ee734b7fdf11335663e18c75ea12dccc76062f09f164a3b0f8be4371a)
tar -xzf "$f" -C "$ctx/bin" trivy
f=$(fetch "$gh/regclient/regclient/releases/download/v0.11.6/regctl-linux-amd64" \
	8e0e62a497fcdb8048d18aa927a139613176ba0531f412bc541044e28f9856bd)
install -m 0755 "$f" "$ctx/bin/regctl"
# Copacetic: patches an image's OS (and, experimental, Python/Node.js) packages in place through BuildKit (Images > Fix
# image > Pit stop); the checksum is the release's copacetic_checksums.txt line.
f=$(fetch "$gh/project-copacetic/copacetic/releases/download/v0.15.0/copa_0.15.0_linux_amd64.tar.gz" \
	cff3fe9320f3bd24bafcfd0ce877ef57e23acad0836da0ac6c253919f71c9cad)
tar -xzf "$f" -C "$ctx/bin" copa
chmod 0755 "$ctx"/bin/*
# The vulnerability database as of today (its date shows in every security report).
"$ctx/bin/trivy" image --download-db-only --cache-dir "$ctx/trivy-cache" --no-progress >/dev/null
du -sh "$ctx/trivy-cache" | sed 's/^/trivy database: /'

docker build --platform linux/amd64 -t "$img" "$ctx"
docker run --rm --network none --entrypoint sh "$img" -c '
	set -e
	printf "#!/bin/bash\nif [ \$1 ]\nthen\necho hi\nfi\n" > /tmp/t.sh
	shfmt -i 1 /tmp/t.sh | grep -q "^ echo hi$"
	printf "echo \$foo\n" > /tmp/u.sh; ! shellcheck -s bash -f json /tmp/u.sh >/tmp/l.json; grep -q "\"code\":2086" /tmp/l.json
	trivy --version | grep -q "Vulnerability DB"
	copa --version | grep -q "0.15.0"
	echo "image-tools checks passed"'
# its archive in the bundle, where images.txt lists it; it ships inside the platform service's image
# (coder-ui-updates, build-ui-image.sh), which images.txt lists instead
if grep -q '^ghcr.io/user187x/coder-image-tools:' "${top}/helm-chart/images.txt"; then
	"${top}/helm-chart/mirror-images.sh" store "$img"
else
	echo "not stored on its own: build-ui-image.sh builds the platform service's image on it"
fi
echo "built $img; image ID $(docker image inspect --format '{{.Id}}' "$img")"

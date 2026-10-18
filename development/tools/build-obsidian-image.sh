#!/usr/bin/env bash
# Builds the workspace image of the Obsidian template (ubuntu-obsidian), ghcr.io/user187x/coder-workspace-obsidian:<TAG>
# (../workspace-image-obsidian: the ubuntu-vnc-desktop image + Obsidian), and checks with no network at all that
# Obsidian starts on a desktop with the workspace's vault and its updates off. Obsidian is the obsidian.deb kept
# untracked at the repository's root (or OBSIDIAN_DEB=<file>), checked against the SHA-256 pinned here.
# Obsidian's terms apply: the image is for the organization's own clusters and is never pushed to a public registry or
# attached to a public release (build-bundle.sh --no-obsidian).
#   ./build-obsidian-image.sh [TAG]               default 1.0.0
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; dev="$(dirname "$here")"; top="$(dirname "$dev")"
ctx="${dev}/workspace-image-obsidian"
tag="${1:-1.0.0}"; img="ghcr.io/user187x/coder-workspace-obsidian:${tag}"
OBSIDIAN_DEB_SHA256=85b10dcba6edfc1c0460a6d18260cf31c30447a444bd858a6440b9c9c8806d25   # obsidian_1.14.4_amd64.deb

deb="${OBSIDIAN_DEB:-${top}/obsidian.deb}"
[[ -f "$deb" ]] || { echo "Obsidian's package is missing: $deb" >&2; exit 1; }
got="$(sha256sum "$deb" | cut -d' ' -f1)"
[[ "$got" == "$OBSIDIAN_DEB_SHA256" ]] || { echo "obsidian.deb: SHA-256 mismatch (got ${got:0:16}…)" >&2; exit 1; }
echo "obsidian.deb: SHA-256 verified"

# the package beside the Dockerfile for the build only (a hard link where it can be)
ln -f "$deb" "${ctx}/obsidian.deb" 2>/dev/null || cp "$deb" "${ctx}/obsidian.deb"
trap 'rm -f "${ctx}/obsidian.deb"' EXIT
docker build --platform linux/amd64 -t "$img" "$ctx"

# As a workspace starts it (coder-obsidian-start, which the template's startup script runs): a desktop display, the
# vault, Obsidian; with no network at all.
docker run --rm --network none --shm-size=1g --entrypoint bash "$img" -c '
	set -e; export HOME=/home/coder; cd
	Xtigervnc :1 -SecurityTypes None -geometry 1280x800 -localhost >/tmp/x.log 2>&1 &
	for i in $(seq 1 50); do [ -S /tmp/.X11-unix/X1 ] && break; sleep 0.2; done
	coder-obsidian-start "$HOME/Obsidian"
	for i in $(seq 1 30); do pgrep -x obsidian >/dev/null && break; sleep 1; done
	sleep 8
	pgrep -x obsidian >/dev/null || { echo "Obsidian is not running" >&2; tail -n 30 /tmp/obsidian.log; exit 1; }
	python3 -c "import json,sys; c=json.load(open(sys.argv[1])); v=list(c[\"vaults\"].values()); assert c[\"updateDisabled\"] and v[0][\"open\"] and v[0][\"path\"]==sys.argv[2], c" \
		"$HOME/.config/obsidian/obsidian.json" "$HOME/Obsidian"
	test -f "$HOME/Obsidian/Welcome.md"
	! test -e /opt/Obsidian/chrome-sandbox
	echo "Obsidian offline check passed"'
# Into the delivery bundle, replacing the previous one; then update its line (tag, digest) in helm-chart/images.txt
# and coder-workspace.obsidianImage in helm-chart/coder-airgapped/values.yaml.
"${top}/helm-chart/mirror-images.sh" store "$img"
echo "built $img (Obsidian $(sed -n 's/^ARG OBSIDIAN_VERSION=//p' "${ctx}/Dockerfile")); image ID $(docker image inspect --format '{{.Id}}' "$img")"

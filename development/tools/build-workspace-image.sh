#!/usr/bin/env bash
# Builds the workspace image ghcr.io/user187x/coder-workspace:<TAG> (../workspace-image: enterprise-base + desktop,
# JetBrains Mono Nerd Font, tmux) and saves it into the delivery bundle (helm-chart/images/custom/), replacing the
# previous one. Then update its [custom] line (tag, digest) in helm-chart/images.txt and
# coder-workspace.workspaceImage in helm-chart/coder-airgapped/values.yaml.
#   ./build-workspace-image.sh [TAG]       default 1.0.0
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; dev="$(dirname "$here")"; top="$(dirname "$dev")"
tag="${1:-1.0.0}"; img="ghcr.io/user187x/coder-workspace:${tag}"
docker build --platform linux/amd64 -t "$img" "${dev}/workspace-image"
# What the image must provide (the template relies on it):
docker run --rm --entrypoint sh "$img" -c '
	set -e
	test -x /usr/bin/vncserver && test -x /usr/bin/websockify && test -f /usr/share/novnc/index.html
	! dpkg -s light-locker >/dev/null 2>&1
	fc-match monospace | grep -q JetBrainsMono
	/usr/local/bin/tmux -V && command -v tmux-mirror-setup >/dev/null
	echo "image checks passed"'
out="${top}/helm-chart/images/custom"; mkdir -p "$out"; rm -f "$out"/ghcr.io_user187x_coder-workspace_*.tar.gz
docker save --platform linux/amd64 "$img" | gzip -1 >"${out}/$(echo "$img" | tr '/:@' '___').tar.gz"
(cd "$out" && sha256sum ./*.tar.gz | sed 's|  \./|  |' >SHA256SUMS)
echo "built $img; saved to ${out}; image ID $(docker image inspect --format '{{.Id}}' "$img")"

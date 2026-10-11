#!/usr/bin/env bash
# Builds the workspace image ghcr.io/user187x/coder-workspace:<TAG> (../workspace-image: enterprise-base + desktop,
# JetBrains Mono Nerd Font, tmux, command-line tools, k9s/kubectl, default ~/.bashrc) and saves it into the delivery bundle (helm-chart/images/custom/), replacing the
# previous one. Then update its [custom] line (tag, digest) in helm-chart/images.txt and
# coder-workspace.workspaceImage in helm-chart/coder-airgapped/values.yaml.
#   ./build-workspace-image.sh [TAG]       default 1.3.0
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; dev="$(dirname "$here")"; top="$(dirname "$dev")"
tag="${1:-1.3.0}"; img="ghcr.io/user187x/coder-workspace:${tag}"
docker build --platform linux/amd64 -t "$img" "${dev}/workspace-image"
# What the image must provide (the template relies on it):
docker run --rm --entrypoint sh "$img" -c '
	set -e
	test -x /usr/bin/vncserver && test -x /usr/bin/websockify && test -f /usr/share/novnc/index.html
	! dpkg -s light-locker >/dev/null 2>&1
	fc-match monospace | grep -q JetBrainsMono
	/usr/local/bin/tmux -V && command -v tmux-mirror-setup >/dev/null
	for t in btop bat batcat vim eza fzf grc jq yq k9s kubectl k9s-copy-secret; do command -v $t >/dev/null || { echo "missing: $t"; exit 1; }; done
	test -f /usr/share/coder-workspace/bashrc && test -f /usr/share/coder-workspace/vimrc && cmp -s /etc/skel/.bashrc /usr/share/coder-workspace/bashrc
	HOME=$(mktemp -d) bash -ic "type ls; echo ok" 2>&1 | grep -q "^ok$"
	HOME=$(mktemp -d) bash -ic "complete -p kubectl >/dev/null || _completion_loader kubectl; complete -p kubectl" 2>/dev/null | grep -q kubectl
	HOME=$(mktemp -d) sh -c "k9s version -s >/dev/null && test -f \$HOME/.config/k9s/config.yaml"
	grep -q xfce-cp-dark.svg /etc/xdg/xfce4/xfconf/xfce-perchannel-xml/xfce4-desktop.xml && test -f /usr/share/backgrounds/xfce/xfce-cp-dark.svg
	echo "image checks passed"'
out="${top}/helm-chart/images/custom"; mkdir -p "$out"; rm -f "$out"/ghcr.io_user187x_coder-workspace_*.tar.gz
docker save --platform linux/amd64 "$img" | gzip -1 >"${out}/$(echo "$img" | tr '/:@' '___').tar.gz"
(cd "$out" && sha256sum ./*.tar.gz | sed 's|  \./|  |' >SHA256SUMS)
echo "built $img; saved to ${out}; image ID $(docker image inspect --format '{{.Id}}' "$img")"

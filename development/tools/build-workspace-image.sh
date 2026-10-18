#!/usr/bin/env bash
# Builds the workspace image ghcr.io/user187x/coder-workspace:<TAG> (../workspace-image: enterprise-base + desktop,
# Firefox, JetBrains Mono Nerd Font, tmux, command-line tools, k9s/kubectl/kubelogin, Python venv, default ~/.bashrc)
# and saves it into the delivery bundle (helm-chart/images/custom/), replacing the previous one (needs internet).
# Then update its [custom] line (tag, digest) in helm-chart/images.txt and
# coder-workspace.workspaceImage in helm-chart/coder-airgapped/values.yaml.
#   ./build-workspace-image.sh [TAG]       default 1.8.6
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; dev="$(dirname "$here")"; top="$(dirname "$dev")"
tag="${1:-1.8.6}"; img="ghcr.io/user187x/coder-workspace:${tag}"
docker build --platform linux/amd64 -t "$img" "${dev}/workspace-image"
# What the image must provide (the template relies on it):
docker run --rm --entrypoint sh "$img" -c '
	set -e
	test -x /usr/bin/vncserver && test -x /usr/bin/websockify && test -f /usr/share/novnc/index.html
	! dpkg -s light-locker >/dev/null 2>&1
	fc-match monospace | grep -q JetBrainsMono
	/usr/local/bin/tmux -V && command -v tmux-mirror-setup >/dev/null
	for t in btop bat batcat vim eza fzf grc jq yq k9s kubectl kubectl-oidc_login kubelogin k9s-copy-secret code-server filebrowser; do command -v $t >/dev/null || { echo "missing: $t"; exit 1; }; done
	# Lock Down runs these from the image (lockdown.sh: Network, Freeze), as root in the pod
	for t in iptables ip6tables getent awk kill stat; do command -v $t >/dev/null || { echo "missing (Lock Down): $t"; exit 1; }; done
	# GNU coreutils (not Rust coreutils), and the compilers still there without build-essential
	ls --version | grep -q "GNU coreutils" && gcc --version >/dev/null && g++ --version >/dev/null && make --version >/dev/null
	test -f /usr/share/coder-workspace/bashrc && test -f /usr/share/coder-workspace/vimrc && cmp -s /etc/skel/.bashrc /usr/share/coder-workspace/bashrc
	HOME=$(mktemp -d) bash -ic "type ls; echo ok" 2>&1 | grep -q "^ok$"
	HOME=$(mktemp -d) bash -ic "complete -p kubectl >/dev/null || _completion_loader kubectl; complete -p kubectl" 2>/dev/null | grep -q kubectl
	HOME=$(mktemp -d) sh -c "k9s version -s >/dev/null && test -f \$HOME/.config/k9s/config.yaml"
	grep -q xfce-cp-dark.svg /etc/xdg/xfce4/xfconf/xfce-perchannel-xml/xfce4-desktop.xml && test -f /usr/share/backgrounds/xfce/xfce-cp-dark.svg
	dpkg-query -W -f "\${Version}\n" firefox | grep -qv snap && firefox --version
	grep -qx WebBrowser=firefox /etc/xdg/xfce4/helpers.rc && grep -q "use_compositing\" type=\"bool\" value=\"false" /etc/xdg/xfce4/xfconf/xfce-perchannel-xml/xfwm4.xml
	python3 -c "import json; d = json.load(open(\"/usr/share/coder-workspace/addons.json\")); assert d[\"software\"] and d[\"packages\"][\"added\"]"
	python3 -m venv "$(mktemp -d)/v"
	echo "image checks passed"'
# Firefox runs in a container with no network: it renders a local page headless.
docker run --rm --network none --shm-size 256m --entrypoint bash "$img" -c '
	set -e; export HOME="$(mktemp -d)"; echo "<h1>ok</h1>" >"$HOME/t.html"
	timeout 120 firefox --headless --screenshot "$HOME/s.png" "file://$HOME/t.html" >"$HOME/ff.log" 2>&1 || { cat "$HOME/ff.log"; exit 1; }
	test -s "$HOME/s.png" && echo "firefox offline check passed"'
# code-server and File Browser start and answer with no network at all (the template's two web apps).
docker run --rm --network none --entrypoint bash "$img" -c '
	set -e; export HOME="$(mktemp -d)"; cd "$HOME"
	code-server --auth none --bind-addr 127.0.0.1:13337 --disable-telemetry --disable-update-check >cs.log 2>&1 &
	filebrowser --noauth --root "$HOME" --port 13339 --database "$HOME/fb.db" >fb.log 2>&1 &
	for i in $(seq 1 60); do curl -fs http://127.0.0.1:13337/healthz >/dev/null && curl -fs http://127.0.0.1:13339/health >/dev/null && break; sleep 0.5; done
	curl -fs http://127.0.0.1:13337/healthz >/dev/null || { cat cs.log; exit 1; }
	curl -fs http://127.0.0.1:13339/health >/dev/null || { cat fb.log; exit 1; }
	echo "code-server and File Browser offline checks passed"'
# its archive in the bundle, in its section's folder of images.txt (replacing the previous tag's)
"${top}/helm-chart/mirror-images.sh" store "$img"
echo "built $img; image ID $(docker image inspect --format '{{.Id}}' "$img")"

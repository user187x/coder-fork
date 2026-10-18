#!/usr/bin/env bash
# Builds the workspace image of the AI templates (ubuntu-unsloth, ubuntu-opencode),
# ghcr.io/user187x/coder-workspace-ai:<TAG> (../workspace-image-ai: the ubuntu-vnc-desktop image + Unsloth Studio +
# OpenCode), and checks with no network at all that Unsloth's app starts on a desktop and its server answers, that a
# workspace's ~/.unsloth only links to the image's install, and that OpenCode runs on it. The Unsloth desktop app is
# the AppImage kept untracked at the repository's root as unsloth.AppImage (or UNSLOTH_APPIMAGE=<file>), checked against
# the SHA-256 pinned here; the build installs Unsloth Studio's server with the installer that AppImage carries (it
# downloads its Python packages, llama.cpp and Node). OpenCode's release archive is downloaded once (kept in
# ../workspace-image-ai/, not in git) and checked too.
#   ./build-ai-image.sh [TAG]                     default 2.0.0
#   ./build-ai-image.sh TAG --cpu                 PyTorch and llama.cpp for the CPU only (a much smaller image)
#   TEST_MODEL=/path/model.gguf ./build-ai-image.sh   the model the check chats with (default: the garak image's
#                                                   SmolLM2-135M from ~/.cache/coder-airgapped/garak, when there)
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; dev="$(dirname "$here")"; top="$(dirname "$dev")"
ctx="${dev}/workspace-image-ai"
tag="${1:-2.0.0}"; img="ghcr.io/user187x/coder-workspace-ai:${tag}"
cpu=false; [[ "${2:-}" == "--cpu" ]] && cpu=true
opencode="$(sed -n 's/^ARG OPENCODE_VERSION=//p' "${ctx}/Dockerfile")"
UNSLOTH_APPIMAGE_SHA256=37b3736f6cea9bf75bff386fafb3ae3e1572f3767e00706783d3e96ac4156c2b   # unsloth.AppImage
OPENCODE_SHA256=24b0d458d21ef548b2752166303defcf7f4945b049fb4876ab78dfaf86d81b27   # opencode-linux-x64-baseline.tar.gz, v1.18.34 (GitHub's digest)

appimage="${UNSLOTH_APPIMAGE:-${top}/unsloth.AppImage}"
[[ -f "$appimage" ]] || { echo "Unsloth's AppImage is missing: $appimage" >&2; exit 1; }
got="$(sha256sum "$appimage" | cut -d' ' -f1)"
[[ "$got" == "$UNSLOTH_APPIMAGE_SHA256" ]] || { echo "unsloth.AppImage: SHA-256 mismatch (got ${got:0:16}…)" >&2; exit 1; }
echo "unsloth.AppImage: SHA-256 verified"

fetch() { # FILE URL SHA256
	local file="${ctx}/$1"
	[[ -f "$file" ]] || { curl -fL --retry 3 -o "${file}.part" "$2" && mv "${file}.part" "$file"; }
	local sum; sum="$(sha256sum "$file" | cut -d' ' -f1)"
	[[ "$sum" == "$3" ]] || { echo "$1: SHA-256 mismatch (got ${sum:0:16}…, want ${3:0:16}…)" >&2; rm -f "$file"; exit 1; }
	echo "$1: SHA-256 verified"
}
fetch "opencode-${opencode}-linux-x64-baseline.tar.gz" \
	"https://github.com/anomalyco/opencode/releases/download/v${opencode}/opencode-linux-x64-baseline.tar.gz" "$OPENCODE_SHA256"

# the AppImage beside the Dockerfile for the build only (a hard link where it can be: no copy of 180 MB)
ln -f "$appimage" "${ctx}/unsloth.AppImage" 2>/dev/null || cp "$appimage" "${ctx}/unsloth.AppImage"
trap 'rm -f "${ctx}/unsloth.AppImage"' EXIT
args=()
if $cpu; then
	args+=(--build-arg UNSLOTH_TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu --build-arg UNSLOTH_LLAMA_CPP_BACKEND=cpu)
fi
docker build --platform linux/amd64 "${args[@]}" -t "$img" "$ctx"

# As a workspace starts it (coder-unsloth-start, which the template's startup script runs): a desktop display, the
# app, its server; with no network at all. Then, with a model in the model library's place (~/models): OpenCode
# pointed at Unsloth (an API key of it), a chat through Unsloth's API, and one through OpenCode.
model="${TEST_MODEL:-$HOME/.cache/coder-airgapped/garak/SmolLM2-135M-Instruct-Q8_0.gguf}"
model_mount=() model_name=""
if [[ -f "$model" ]]; then
	model_mount=(-v "$(realpath "$model"):/home/coder/models/$(basename "$model"):ro")
	model_name="$(basename "$model")"
fi
docker run --rm --network none --shm-size=2g "${model_mount[@]}" -e MODEL="$model_name" --entrypoint bash "$img" -c '
	set -e; export HOME=/home/coder; cd
	Xtigervnc :1 -SecurityTypes None -geometry 1280x800 -localhost >/tmp/x.log 2>&1 &
	for i in $(seq 1 50); do [ -S /tmp/.X11-unix/X1 ] && break; sleep 0.2; done
	coder-unsloth-start || { tail -n 30 /tmp/unsloth-studio.log; exit 1; }
	pgrep -x unsloth-studio >/dev/null || { echo "the Unsloth app is not running" >&2; tail -n 30 /tmp/unsloth-studio.log; exit 1; }
	for l in ~/.unsloth/llama.cpp ~/.unsloth/studio/unsloth_studio; do [ -L "$l" ] || { echo "$l is not a link" >&2; exit 1; }; done
	du -sh "$HOME/.unsloth" | sed "s/^/per-workspace Unsloth home: /"
	opencode --version | sed "s/^/OpenCode /"
	if [ -n "$MODEL" ]; then
		unsloth start opencode --no-launch --model "$HOME/models/$MODEL" --context-length 4096 </dev/null >/tmp/oc.env
		cfg=$HOME/.unsloth/studio/auth/agents/opencode/opencode.json
		key=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))[\"provider\"][\"unsloth-studio\"][\"options\"][\"apiKey\"])" "$cfg")
		id=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))[\"model\"].split(\"/\",1)[1])" <(sed -n "s/^export OPENCODE_CONFIG_CONTENT=.\(.*\).$/\1/p" /tmp/oc.env) 2>/dev/null || basename "$MODEL" .gguf)
		curl -fs -m 300 http://127.0.0.1:8888/v1/chat/completions -H "Authorization: Bearer $key" -H "Content-Type: application/json" \
			-d "{\"model\":\"$id\",\"messages\":[{\"role\":\"user\",\"content\":\"Say hello in three words.\"}],\"max_tokens\":20,\"stream\":false}" |
			python3 -c "import json,sys; print(\"Unsloth says:\", json.load(sys.stdin)[\"choices\"][0][\"message\"][\"content\"].strip())"
		mkdir -p ~/check && cd ~/check
		eval "$(grep "^export " /tmp/oc.env)"
		timeout 300 opencode run "Reply with the single word PONG." </dev/null | tail -n 3 | sed "s/^/OpenCode says: /" || true
	fi
	echo "Unsloth and OpenCode offline checks passed"'
# Into the delivery bundle, replacing the previous one; then update its line (tag, digest) in helm-chart/images.txt
# and coder-workspace.aiImage in helm-chart/coder-airgapped/values.yaml.
"${top}/helm-chart/mirror-images.sh" store "$img"
echo "built $img (Unsloth Studio, OpenCode ${opencode}); image ID $(docker image inspect --format '{{.Id}}' "$img")"

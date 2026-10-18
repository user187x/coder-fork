#!/usr/bin/env bash
# Builds the workspace image of the AI templates (ubuntu-lmstudio, ubuntu-opencode),
# ghcr.io/user187x/coder-workspace-ai:<TAG> (../workspace-image-ai: the ubuntu-vnc-desktop image + LM Studio + OpenCode),
# and checks with no network at all that LM Studio starts on a desktop, serves its API, and that OpenCode runs. The
# LM Studio AppImage and OpenCode's release archive are downloaded once (kept in ../workspace-image-ai/, not in git)
# and checked against the SHA-256 pinned here.
# LM Studio's terms allow personal and internal business use only: the image is for the organization's own clusters
# and is never pushed to a public registry or attached to a public release (build-bundle.sh --no-lmstudio).
#   ./build-ai-image.sh [TAG]                     default 1.0.1
#   TEST_MODEL=/path/model.gguf ./build-ai-image.sh   also chats with that model, through LM Studio and through
#                                                   OpenCode (a small one: e.g. Qwen2.5 0.5B Instruct, 32k context)
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; dev="$(dirname "$here")"
ctx="${dev}/workspace-image-ai"
tag="${1:-1.0.1}"; img="ghcr.io/user187x/coder-workspace-ai:${tag}"
lmstudio="$(sed -n 's/^ARG LMSTUDIO_VERSION=//p' "${ctx}/Dockerfile")"
opencode="$(sed -n 's/^ARG OPENCODE_VERSION=//p' "${ctx}/Dockerfile")"
LMSTUDIO_SHA256=2c62a38a93ad03ca5daaef28804880be167566783ffafc173ce2e1cb10b7552e   # LM-Studio-0.4.26-4-x64.AppImage
OPENCODE_SHA256=24b0d458d21ef548b2752166303defcf7f4945b049fb4876ab78dfaf86d81b27   # opencode-linux-x64-baseline.tar.gz, v1.18.34 (GitHub's digest)

fetch() { # FILE URL SHA256
	local file="${ctx}/$1"
	[[ -f "$file" ]] || { curl -fL --retry 3 -o "${file}.part" "$2" && mv "${file}.part" "$file"; }
	local got; got="$(sha256sum "$file" | cut -d' ' -f1)"
	[[ "$got" == "$3" ]] || { echo "$1: SHA-256 mismatch (got ${got:0:16}…, want ${3:0:16}…)" >&2; rm -f "$file"; exit 1; }
	echo "$1: SHA-256 verified"
}
fetch "LM-Studio-${lmstudio}-x64.AppImage" \
	"https://installers.lmstudio.ai/linux/x64/${lmstudio}/LM-Studio-${lmstudio}-x64.AppImage" "$LMSTUDIO_SHA256"
fetch "opencode-${opencode}-linux-x64-baseline.tar.gz" \
	"https://github.com/anomalyco/opencode/releases/download/v${opencode}/opencode-linux-x64-baseline.tar.gz" "$OPENCODE_SHA256"

docker build --platform linux/amd64 -t "$img" "$ctx"

# As a workspace starts it (coder-lmstudio-start, which the template's startup script runs): a desktop display, the
# app, its server; with no network at all. With TEST_MODEL: in the model library's place (~/models), a chat through
# LM Studio's API, and one through OpenCode.
model_mount=() model_name=""
if [[ -n "${TEST_MODEL:-}" ]]; then
	model_mount=(-v "$(realpath "$TEST_MODEL"):/home/coder/models/$(basename "$TEST_MODEL"):ro")
	model_name="$(basename "$TEST_MODEL")"
fi
docker run --rm --network none --shm-size=1g "${model_mount[@]}" -e MODEL="$model_name" --entrypoint bash "$img" -c '
	set -e; export HOME=/home/coder; cd
	Xtigervnc :1 -SecurityTypes None -geometry 1280x800 -localhost >/tmp/x.log 2>&1 &
	for i in $(seq 1 50); do [ -S /tmp/.X11-unix/X1 ] && break; sleep 0.2; done
	export OPENCODE_CONFIG=$HOME/.config/opencode/coder-workspace.json
	coder-lmstudio-start 32768
	curl -fs http://127.0.0.1:1234/v1/models | python3 -c "import json,sys; print(\"LM Studio API models:\", [m[\"id\"] for m in json.load(sys.stdin)[\"data\"]])"
	for f in "$HOME"/.lmstudio/extensions/backends/*; do [ -L "$f" ] || { echo "$f is a copy, not a link" >&2; exit 1; }; done
	du -sh "$HOME/.lmstudio" | sed "s/^/per-workspace LM Studio home: /"
	opencode --version | sed "s/^/OpenCode /"
	if [ -n "$MODEL" ]; then
		key=$(python3 -c "import json; print(json.load(open(\"$OPENCODE_CONFIG\"))[\"model\"].split(\"/\",1)[1])")
		curl -fs http://127.0.0.1:1234/v1/chat/completions -H "Content-Type: application/json" \
			-d "{\"model\":\"$key\",\"messages\":[{\"role\":\"user\",\"content\":\"Say hello in three words.\"}],\"max_tokens\":20}" |
			python3 -c "import json,sys; print(\"LM Studio says:\", json.load(sys.stdin)[\"choices\"][0][\"message\"][\"content\"].strip())"
		mkdir -p ~/check && cd ~/check
		timeout 300 opencode run "Reply with the single word PONG." | tail -n 3 | sed "s/^/OpenCode says: /"
	fi
	echo "LM Studio and OpenCode offline checks passed"'
# Into the delivery bundle (helm-chart/images/custom/), replacing the previous one; then update its [custom] line (tag,
# digest) in helm-chart/images.txt and coder-workspace.aiImage in helm-chart/coder-airgapped/values.yaml.
top="$(dirname "$dev")"
# its archive in the bundle, in its section's folder of images.txt (replacing the previous tag's)
"${top}/helm-chart/mirror-images.sh" store "$img"
echo "built $img (LM Studio ${lmstudio}, OpenCode ${opencode}); image ID $(docker image inspect --format '{{.Id}}' "$img")"

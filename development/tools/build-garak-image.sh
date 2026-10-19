#!/usr/bin/env bash
# Builds ghcr.io/user187x/coder-garak:<TAG> (../garak-image): the Model Library's security scanner - garak with the
# Python packages of requirements.lock, its detector models and probe data, and llama.cpp's llama-server - checks it
# with no network (every probe and detector loads; a real scan of a small model), and saves it as a plug-in archive:
# dist/plugins/ghcr.io_user187x_coder-garak_<TAG>.tar.gz (a docker save, gzipped), outside the delivery bundle - the
# platform ships no scanner. Needs internet (PyPI, the PyTorch CPU index, Hugging Face, GitHub).
#   ./build-garak-image.sh TAG                 e.g. 0.17.1-1: garak 0.17.1, the image's first build
#   ./build-garak-image.sh TAG --relock        resolve garak <TAG's version> and its dependencies afresh
#                                              (a newer garak): rewrites requirements.lock
# Then hook it up: Templates > Images > Model library > Import scanner (the archive) - or pin it for a deployment of
# your own: modelScanner.image in values.yaml, the image in your registry.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; dev="$(dirname "$here")"; top="$(dirname "$dev")"
tag="${1:?usage: build-garak-image.sh TAG [--relock]}"; relock="${2:-}"
version="${tag%%-*}"; img="ghcr.io/user187x/coder-garak:${tag}"
llama_build=b11717; llama_sum=1fce11be93423044e073a9a967b99998569c4bd343220f60ef98b080385d09cb
# the end-to-end check's model: SmolLM2 135M Instruct (Apache-2.0), 145 MB
test_model=https://huggingface.co/bartowski/SmolLM2-135M-Instruct-GGUF/resolve/main/SmolLM2-135M-Instruct-Q8_0.gguf
test_sum=5a1395716f7913741cc51d98581b9b1228d80987a9f7d3664106742eb06bba83
cache="${XDG_CACHE_HOME:-$HOME/.cache}/coder-airgapped/garak"; mkdir -p "$cache"
ctx="$(mktemp -d)"; trap 'rm -rf "$ctx"' EXIT

fetch() { # URL SHA256 -> path of the verified download (kept in the cache)
	local url="$1" sum="$2" file="${cache}/$(basename "$1")"
	if [[ ! -f "$file" ]] || ! echo "${sum}  ${file}" | sha256sum -c --quiet 2>/dev/null; then
		curl -fsSL -o "${file}.part" "$url"
		echo "${sum}  ${file}.part" | sha256sum -c --quiet || { rm -f "${file}.part"; echo "ERROR: checksum mismatch: $url" >&2; exit 1; }
		mv "${file}.part" "$file"
	fi
	echo "$file"
}

if [[ "$relock" == "--relock" ]]; then
	echo "resolving garak ${version} and its dependencies (CPU PyTorch)"
	docker run --rm docker.io/library/python:3.13-slim-trixie sh -c \
		"pip install -q --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch >/dev/null 2>&1 \
		 && pip install -q --no-cache-dir garak==${version} >/dev/null 2>&1 && pip freeze" > "${dev}/garak-image/requirements.lock.new"
	grep -q "^garak==${version}$" "${dev}/garak-image/requirements.lock.new" || { echo "ERROR: garak ${version} did not install" >&2; exit 1; }
	mv "${dev}/garak-image/requirements.lock.new" "${dev}/garak-image/requirements.lock"
fi
grep -q "^garak==${version}$" "${dev}/garak-image/requirements.lock" \
	|| { echo "ERROR: requirements.lock is not garak ${version}: add --relock" >&2; exit 1; }

cp "${dev}/garak-image/"{Dockerfile,coder-garak,profiles.json,requirements.lock} "$ctx/"
f=$(fetch "https://github.com/ggml-org/llama.cpp/releases/download/${llama_build}/llama-${llama_build}-bin-ubuntu-x64.tar.gz" "$llama_sum")
mkdir -p "$ctx/llama" && tar -xzf "$f" -C "$ctx/llama" --strip-components=1
# only the server and its libraries (and the licence)
find "$ctx/llama" -maxdepth 1 -type f ! -name 'llama-server' ! -name '*.so*' ! -name 'LICENSE*' -delete
find "$ctx/llama" -maxdepth 1 -name 'lib*-impl.so' ! -name 'libllama-server-impl.so' -delete

docker build --platform linux/amd64 --build-arg BUILT="$(date -u +%Y-%m-%dT%H:%MZ)" -t "$img" "$ctx"
echo "--- offline: every probe and detector loads"
docker run --rm --network none "$img" selftest
echo "--- offline: a scan of a small model (quick profile)"
m=$(fetch "$test_model" "$test_sum")
out="$(mktemp -d)"; chmod 0777 "$out"
# within the memory a scan Job of this model gets (its size plus 6 GiB): llama-server must not hoard prompts
docker run --rm --network none --memory 6g --cpus 4 -v "$m:/model.gguf:ro" -v "$out:/out" "$img" scan /model.gguf \
	--profile quick --out /out
python3 - "$out/coder-report.json" <<'PY'
import json, sys
r = json.load(open(sys.argv[1]))
assert not r["error"], r["error"]
assert r["digest"] and r["evals"], "no digest"
assert {c["id"] for c in r["checks"]} >= {"gguf", "chat-template", "loads"}, r["checks"]
print("scan check passed: garak %s, %d probe/detector results, TBSA %s, checks %s" % (
    r["garak"], len(r["evals"]), (r["tbsa"] or {}).get("score"), [(c["id"], c["status"]) for c in r["checks"]]))
PY
rm -rf "$out"
plugins="${top}/dist/plugins"; mkdir -p "$plugins"
archive="${plugins}/$(echo "$img" | tr '/:' '__').tar.gz"
docker save "$img" | gzip -1 >"${archive}.part" && mv "${archive}.part" "$archive"
(cd "$plugins" && sha256sum "$(basename "$archive")" >"$(basename "$archive").sha256")
echo "built $img; image ID $(docker image inspect --format '{{.Id}}' "$img")"
echo "the plug-in archive: ${archive#"${top}/"} (Model library > Import scanner)"

#!/usr/bin/env bash
# Builds ghcr.io/user187x/coder-platform:<tag>-airgap (the fork's image + Terraform provider mirror) and saves it
# into the delivery bundle (helm-chart/images/custom/), replacing the previous one. Update its digest in images.txt.
#   ./build-airgap-image.sh [BASE_TAG]      default 2.0.0-platform.14 (from local docker, ghcr, or the private backup)
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; dev="$(dirname "$here")"; top="$(dirname "$dev")"
base_tag="${1:-2.0.0-platform.14}"; base="ghcr.io/user187x/coder-platform:${base_tag}"; img="${base}-airgap"
ctx="${dev}/airgap-image"
# The base image: local docker, else ghcr (docker login ghcr.io), else the backup in ~/coder-airgapped-private/images/
docker image inspect "$base" >/dev/null 2>&1 || docker pull "$base" 2>/dev/null ||
	docker load -i "${HOME}/coder-airgapped-private/images/coder-platform-${base_tag}.tar.gz"
rm -rf "${ctx}/template"; mkdir -p "${ctx}/template"
cp "${top}"/helm-chart/coder-airgapped/charts/coder-workspace/files/*.tf "${ctx}/template/"
tf=$(docker run --rm --entrypoint terraform "$base" version | sed -n 's/^Terraform v//p' | head -n1)
docker build --build-arg BASE="$base" --build-arg TERRAFORM_VERSION="$tf" -t "$img" "$ctx"
out="${top}/helm-chart/images/custom"; mkdir -p "$out"; rm -f "$out"/ghcr.io_user187x_coder-platform_*.tar.gz
docker save --platform linux/amd64 "$img" | gzip -1 >"${out}/$(echo "$img" | tr '/:@' '___').tar.gz"
(cd "$out" && sha256sum ./*.tar.gz | sed 's|  \./|  |' >SHA256SUMS)
echo "built $img (terraform $tf); saved to ${out}"
docker run --rm --entrypoint sh "$img" -c 'find /opt/terraform/plugins -maxdepth 4 -mindepth 3 -type d | sed "s|/opt/terraform/plugins/||"'

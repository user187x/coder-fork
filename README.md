# coder-airgapped

The fork of Coder (`ghcr.io/user187x/coder-platform`) with its dashboard modifications, add-on platform service,
announcement banner and workspace template, packaged as one Helm chart for air-gapped Kubernetes (EKS), with an
Argo CD Application and every image it needs.

| Path | What |
|---|---|
| [`helm-chart/`](helm-chart/) | **The deliverable** and source of truth: the chart, `DEPLOY.md` (how to deploy), `README.md` (reference), the Argo CD Application, `install.sh`, `mirror-images.sh`, `images.txt`, prerequisites |
| [`development/`](development/) | Tools to build, test and upgrade it; the fork pin; a reference capture of the original deployment; legacy tools |
| `dist/` | Built tarballs (git-ignored) |
| [`CHANGELOG.md`](CHANGELOG.md) | What changed in each version |

The Coder server is built from a separate repository, `git@github.com:user187x/coder.git`
(see [`development/coder-fork/FORK.md`](development/coder-fork/FORK.md)).

## Build a release

```bash
development/tools/build-bundle.sh      # -> dist/coder-airgapped-<version>.tar.gz (all images) and -slim.tar.gz
```

Image archives are not in git: `images.txt` pins every image by digest, and `build-bundle.sh` fetches missing public
ones (`mirror-images.sh save`). The custom Coder image comes from `development/tools/build-airgap-image.sh` (or
`ghcr.io/user187x/coder-platform`).

## Test a release

`development/tools/airgap-e2e/` runs DEPLOY.md end to end on a kind cluster with all internet egress blocked.

## Deploy

[`helm-chart/DEPLOY.md`](helm-chart/DEPLOY.md).

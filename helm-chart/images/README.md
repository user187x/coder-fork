# Images in this bundle

Every container image the solution runs, saved with `docker save` (linux/amd64, gzip). `../mirror-images.sh push`
loads them from here and pushes them to your registry – no internet needed.

| Folder | What | Size | Keep? |
|---|---|---:|---|
| `custom/` | The fork's Coder server image and the workspace image. **On no public registry.** | ~1.1 GB | **Always keep** – nothing else can replace them |
| `public/chart/` | Public images the chart runs (database, Python add-ons, template push) | 513 MB | Remove if your registry already has them |
| `public/prereqs/` | CloudNativePG operator, Traefik – for the charts in `../prereqs/` | 87 MB | Remove if those are already installed |
| `public/platform/` | Argo CD and Keycloak as the reference cluster runs them – reference only, not pushed by default | 986 MB | Remove if you have Argo CD and Keycloak |

## Make the bundle smaller

```bash
rm -rf images/public              # keeps only custom/: ~1.1 GB instead of ~2.7 GB
# or just one folder:  rm -rf images/public/platform
```

Then on the air-gapped side, either your registry already has the public images – at the paths
`./mirror-images.sh list $REGISTRY` shows – and you push only the custom one:

```bash
./mirror-images.sh push $REGISTRY custom
```

or put them back on an internet machine first: `./mirror-images.sh save` (pulls each by the exact digest in
`../images.txt` and rewrites the archives). `./mirror-images.sh list` shows what is bundled and what is not;
`./mirror-images.sh verify` checks the archives against their `SHA256SUMS`.

## What each file is
### `custom/`

| Archive | Image | Size | Used by |
|---|---|---:|---|
| `ghcr.io_user187x_coder-platform_2.0.0-platform.23-airgap.tar.gz` | `ghcr.io/user187x/coder-platform:2.0.0-platform.23-airgap` | 312 MB | coder (server); = the fork's platform.23 (0eb28965f) + Terraform provider mirror |
| `ghcr.io_user187x_coder-image-tools_1.0.0.tar.gz` | `ghcr.io/user187x/coder-image-tools:1.0.0` | 208 MB | coder-ui-updates and the Images builder's scans: python:3.13-alpine + shellcheck, shfmt, trivy (+ its vulnerability database), regctl (`development/image-tools`) |
| `ghcr.io_user187x_coder-workspace_1.7.0.tar.gz` | `ghcr.io/user187x/coder-workspace:1.7.0` | 1539 MB | workspace pods (ubuntu-vnc-desktop): enterprise-base + desktop (no light-locker), Firefox, JetBrains Mono Nerd Font, tmux, CLI tools (btop, bat, vim, eza, fzf, grc, jq, yq), k9s (custom build + config), kubectl, kubelogin (admins' kubectl sign-in), marimo with its Python packages (offline), default ~/.bashrc and desktop background |

### `public/chart/`

| Archive | Image | Size | Used by |
|---|---|---:|---|
| `ghcr.io_cloudnative-pg_postgresql_18.4-system-trixie.tar.gz` | `ghcr.io/cloudnative-pg/postgresql:18.4-system-trixie` | 249 MB | database, uiUpdates.logsDb, uiUpdates.pgImage |
| `docker.io_library_python_3.13-alpine.tar.gz` | `docker.io/library/python:3.13-alpine` | 19 MB | coder-banner, coder-image-registry-proxy, bootstrap Job, push-Job login (reference cluster: same tag via its Harbor) |
| `ghcr.io_coder_coder_latest.tar.gz` | `ghcr.io/coder/coder:latest` | 244 MB | coder-workspace push-template Job (floating tag, pinned here) |
| `docker.io_moby_buildkit_v0.33.0-rootless.tar.gz` | `docker.io/moby/buildkit:v0.33.0-rootless` | 123 MB | Images view: the build Jobs (BuildKit rootless) |
| `docker.io_library_registry_3.1.2.tar.gz` | `docker.io/library/registry:3.1.2` | 20 MB | Images view: coder-image-registry, where built images are kept |
| `registry.k8s.io_metrics-server_metrics-server_v0.8.1.tar.gz` | `registry.k8s.io/metrics-server/metrics-server:v0.8.1` | 21 MB | metrics-server: live CPU and memory (HUD, Health, quotas); installed only where the cluster has no metrics API |

### `public/prereqs/`

| Archive | Image | Size | Used by |
|---|---|---:|---|
| `ghcr.io_cloudnative-pg_cloudnative-pg_1.30.0.tar.gz` | `ghcr.io/cloudnative-pg/cloudnative-pg:1.30.0` | 35 MB | CloudNativePG operator (prereqs/cloudnative-pg-0.29.0.tgz, install.sh --cnpg) |
| `docker.io_traefik_v3.7.12.tar.gz` | `docker.io/traefik:v3.7.12` | 52 MB | Traefik (prereqs/traefik-41.4.0.tgz) |

### `public/platform/`

| Archive | Image | Size | Used by |
|---|---|---:|---|
| `quay.io_argoproj_argocd_v3.5.2.tar.gz` | `quay.io/argoproj/argocd:v3.5.2` | 199 MB | Argo CD (server, repo-server, controller, applicationset) |
| `ghcr.io_dexidp_dex_v2.45.1.tar.gz` | `ghcr.io/dexidp/dex:v2.45.1` | 45 MB | Argo CD dex |
| `ecr-public.aws.com_docker_library_redis_8.6.4-alpine.tar.gz` | `ecr-public.aws.com/docker/library/redis:8.6.4-alpine` | 33 MB | Argo CD redis |
| `quay.io_keycloak_keycloak-operator_26.7.3.tar.gz` | `quay.io/keycloak/keycloak-operator:26.7.3` | 224 MB | Keycloak operator |
| `quay.io_keycloak_keycloak_26.7.3.tar.gz` | `quay.io/keycloak/keycloak:26.7.3` | 254 MB | Keycloak |
| `ghcr.io_cloudnative-pg_postgresql_17.tar.gz` | `ghcr.io/cloudnative-pg/postgresql:17` | 231 MB | Keycloak's database (CNPG Cluster keycloak-db) |

## Digests

`../images.txt` has the full digest of every image. That is the digest that was pulled (the upstream multi-arch
index) and the one `save` pulls again. The archive holds only its linux/amd64 image, so after `docker load` – and in
your registry after `push` – the image shows a different digest (that platform's manifest). Same image, same layers.

Each folder's `SHA256SUMS` covers its archives; `mirror-images.sh push` checks an archive before loading it.

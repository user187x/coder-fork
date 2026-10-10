# coder-platform

Coder and everything this platform adds, packaged to install on an air-gapped Kubernetes cluster and end up in the
same state as the reference installation:

| Part | What you get |
|---|---|
| Coder 2.37.3 | Air-gap image with a Terraform provider mirror (workspace builds never reach the internet), telemetry, update check and public STUN off |
| Database | CloudNativePG operator + a 2-instance PostgreSQL cluster (kept on uninstall) |
| Sign-in | Keycloak (OIDC) only; password sign-in off. Named admins become owners on their first Keycloak sign-in |
| Dashboard add-ons | General (was Deployment): Announcement, Classification, Change Logo, Monitoring, Keycloak, AI tree; Health › Workspace Health; Settings › Certificates; trimmed menus |
| Announcement banner | The coder-banner service, edited in General › Announcement |
| Workspace template | `ubuntu-vnc-desktop`: XFCE desktop (noVNC), code-server, File Browser, marimo, the user's `.p12` at `~/.cert/<username>.p12`; all tools preinstalled in the workspace image |

## What the cluster must already have

* **Kubernetes 1.27+** with a default StorageClass (or set `STORAGE_CLASS`).
* **Traefik v3** with the Gateway API provider, and a **Gateway** with an HTTPS listener whose certificate covers
  `coder.<DOMAIN>` and `*.<DOMAIN>`. The rewrite-body plugin is *not* required up front: `./install plugin` adds the
  bundled copy to the running Traefik (Traefik managed by Argo CD/Flux: it prints the values to add there instead).
* **DNS**: `coder.<DOMAIN>` and `*.<DOMAIN>` pointing at Traefik, resolvable by browsers *and* inside the cluster
  (workspaces connect back to Coder's address).
* **Keycloak** (or any OIDC provider) with a confidential client (default id `coder`), redirect URI
  `https://coder.<DOMAIN>/api/v2/users/oidc/callback`, and the `profile` + `email` scopes.
* **A container registry** the nodes can pull from (e.g. Harbor).
* On the machine you install from: `bash`, `curl`, and `kubectl` access to the cluster. `helm`, `kubectl`, `crane`
  (image push) and `jq` are in `tools/` for when the host has none; no Docker needed.

## Install

```bash
tar -xzf coder-platform-airgap-<version>.tar.gz && cd coder-platform-airgap-<version>
sha256sum -c --quiet SHA256SUMS          # the bundle arrived intact
cp install.conf.example install.conf     # 5 required answers: DOMAIN, REGISTRY, OIDC_ISSUER_URL, ADMINS, CA_FILE
cp /path/to/your-ca.crt ca.crt           # only with a private CA
./install preflight                      # checks everything, changes nothing
./install all                            # images -> secrets -> Traefik plugin -> deploy -> verify
```

`./install all` asks once for the Keycloak client secret (and the registry password, if `REGISTRY_USERNAME` is set);
with `--yes` it takes them from `OIDC_CLIENT_SECRET` / `REGISTRY_PASSWORD` instead. A first install takes 10-20
minutes (most of it pushing the 3.5 GB workspace image and the first database start). Afterwards the admins listed in
`ADMINS` sign in at `https://coder.<DOMAIN>` with Keycloak.

Every step can be run on its own and re-run safely:

| Command | Does |
|---|---|
| `./install preflight` | Checks install.conf, Kubernetes, StorageClass, CloudNativePG, Traefik (version, Gateway provider, plugin), the Gateway listener, DNS, Keycloak, images, Secrets |
| `./install images` | Pushes the 6 bundled images to `REGISTRY` (skips those already there) |
| `./install secrets` | Creates `tls-ca`, `keycloaking-coder-ca`, `keycloaking-coder-oidc` (and a registry pull Secret) if missing; `--force` replaces changed ones |
| `./install plugin` | Enables Traefik's rewrite-body plugin from the bundled source, if not loaded (rolls itself back on failure) |
| `./install values` | Writes and prints `generated/values.yaml` (what deploy uses) |
| `./install deploy` | `DEPLOY=helm`: CloudNativePG operator release, then `helm upgrade --install`. `DEPLOY=argocd`: pushes the chart to `oci://<REGISTRY>/charts` and applies an Argo CD Application |
| `./install verify` | Workloads, database, bootstrap Job, and 48 checks through the Gateway (every dashboard add-on, as a browser gets it) |
| `./install status` | Pods, the bootstrap Job's log, how to read the break-glass admin password |

Your own chart overrides go in `values-local.yaml` next to `install` (applied after the generated values). All chart
settings are documented in `chart/values.yaml` (`helm show values charts/coder-platform-*.tgz`).

## Adding the platform to a cluster that already runs Coder

`./upgrade` (guide: `UPGRADE-EXISTING.txt`) is for a Coder cluster that already has Coder, the CloudNativePG operator,
Traefik with a Gateway and the Gateway API CRDs, e.g. a spoke EKS cluster whose Keycloak runs on a hub cluster:

| Command | Does |
|---|---|
| `./upgrade discover` | Read-only. Finds how Coder is deployed (Helm release, Argo CD Application here or on the hub, plain manifests), its version, database (CNPG or other), OIDC issuer, CA bundle, route and Gateway, Traefik and its plugin, Gateway API CRD version, CNPG operator, node architectures, workspace namespaces; writes `install.conf` and `ca.crt` |
| `./upgrade token` | Stores an owner's API token (renewed on every run) |
| `./upgrade plan` | Read-only. Blocking checks (version direction, settings Coder 2.37 refuses, Keycloak reachable *from Coder's pod*, template providers vs the new image's Terraform mirror, name clashes, arm64 nodes, Argo CD reachability) and the exact actions |
| `./upgrade images` | Pushes the images (ECR: AWS CLI login, repositories created) and proves the nodes can pull |
| `./upgrade tfcheck` | `terraform init` of every existing template with the new image and the current one: regressions found before anything changes |
| `./upgrade backup` | Verified `pg_dump` of Coder's database + the Kubernetes objects involved, to `./backups/` |
| `./upgrade coder` | Coder -> the bundle's version through its own Helm release (`--reuse-values`) or Argo CD Application |
| `./upgrade platform` | The chart in attach mode: add-ons, banner, icons, template; Coder's settings, users, database and route untouched |
| `./upgrade verify` | `./install verify` + every user, owner, template and running workspace still there |
| `./upgrade all` | All of the above in order, stopping at the first problem |

Existing templates that need Terraform providers the bundle's mirror lacks are named by `./upgrade plan`; rebuild with
`EXTRA_PROVIDERS="hashicorp/aws:5.100.0 ..." ./build-bundle`. Coder's breaking changes 2.20-2.37 are in
`docs/coder-breaking-changes.md`. Rehearsal: `test/e2e-upgrade` (hub + spoke kind clusters, existing Coder 2.36.0).

## Upgrades and re-installs

Unpack the new bundle next to the old one, copy `install.conf` (and `ca.crt`, `values-local.yaml`) over, run
`./install all`. The database, the announcement receipts, the uploaded logo, users' certificates and workspaces all
survive: they live in the database and in PersistentVolumes the chart never deletes.

**Coder upgrades change its database schema and can't be rolled back.** Back up first:
`kubectl -n coder exec coder-db-1 -c postgres -- pg_dump -Fc -d app > coder-db-$(date +%F).dump`
(the primary may be `coder-db-2`: `kubectl -n coder get cluster coder-db -o jsonpath='{.status.currentPrimary}'`).

## Things worth knowing

* **Break-glass admin.** The chart's bootstrap admin (`coder-admin`, an owner) keeps a password even though password
  sign-in is off: Coder lets owners use it through the API so nobody is locked out when Keycloak is down.
  `./install status` shows how to read it. The chart's Job uses it to create the owners and push the template.
* **Owners.** Each `ADMINS` entry is created as a Keycloak user with the owner role; Coder links them on their first
  sign-in by email, so the email must be the one Keycloak sends. Add people later by adding them to `ADMINS` and
  running `./install deploy` (or in Coder › Users).
* **Coder settings** come from the chart values (a ConfigMap Coder reads), not from the Keycloak view: General ›
  Keycloak checks the setup and repairs Keycloak's side and the Secrets, but Coder's own settings change through
  install.conf / values-local.yaml + `./install deploy`.
* **The UI add-ons depend on Traefik.** They are rewrites of Coder's JavaScript made by Traefik's rewrite-body
  plugin. If the plugin is missing, Coder still works but shows its stock menus. After any Coder upgrade,
  `./install verify` shows whether every rewrite still applies.
* **Default logo and avatar.** The bundle's `defaults/` (`logo.<ext>`, `avatar.<ext>`, saved on the connected side
  with `../coder-ui-updates/coder-ui-updates export-defaults`) becomes two ConfigMaps at `./install deploy`. A fresh
  installation starts with that logo (General › Customize changes or resets it); every user without a picture of
  their own shows that avatar. Each image can be at most 1 MB. They are kept out of the chart: Helm stores the whole
  chart in its release record, which is limited to 1 MiB.
* **Terminal in the workspace page.** Each agent has a "Terminal" drop-down under "Logs" with the agent's terminal in
  the page. With "End session when closed" (the default) collapsing it ends the shell; otherwise the session stays
  until "End session" or the agent's idle timeout.
* **Workspaces** run in the release namespace only (the template refuses any other; Coder's service account can't
  create pods elsewhere). They pull the workspace image from `REGISTRY` and need no internet.
* **Uninstall**: `helm -n coder uninstall coder-platform` (and `helm -n cnpg-system uninstall cnpg`). The database
  cluster, PersistentVolumeClaims and the operator namespace are kept on purpose; delete them by hand to start over.

## Building the bundle (connected side)

In the source repository (`data/apps/argocd/coder/coder-platform`):

```bash
./build-bundle                  # -> dist/coder-platform-airgap-<version>.tar.gz (+ .sha256), ~1.5 GB
./test/e2e-kind up && ./test/e2e-kind airgap && ./test/e2e-kind install   # full rehearsal on an offline kind cluster
node test/browser-e2e.mjs <dir chromium can read>                          # Keycloak sign-in, add-ons, a workspace
./test/e2e-kind down
```

The chart is built from the same sources as the running platform: `./sync-sources` copies the dashboard add-ons
(`../coder-ui-updates/resources`) and the banner chart (`../banner-update/banner-chart`) into it, and `build-bundle`
runs it before packaging (`./sync-sources --check` reports drift without changing anything). Versions (Coder, images, tools) are pinned at the top of `build-bundle`.

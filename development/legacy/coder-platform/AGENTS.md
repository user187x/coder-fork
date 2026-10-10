# Brief for an AI assistant helping with this bundle

You are helping an operator install coder-platform on an air-gapped Kubernetes cluster from this bundle. Read
README.md first. There is no internet on this side: never suggest downloading anything; everything needed is here.

## What is where

* `install` (bash): the only entry point. `./install --help`. Reads `install.conf` (copied from
  `install.conf.example`); environment variables override it. Writes only to `generated/`.
* `charts/coder-platform-*.tgz`: the Helm chart (Coder, CloudNativePG cluster, dashboard add-ons, banner, template).
  `charts/cloudnative-pg-*.tgz`: the database operator, installed as its own release `cnpg` in `cnpg-system` by
  `./install deploy` when DEPLOY=helm.
* `images/*.tar` + `images/images.txt` (file -> path under `$REGISTRY`), pushed by `./install images` with `tools/crane`.
* `tools/`: static helm, kubectl, crane, jq for linux/amd64 (used when the host lacks them).
* `traefik-plugin/`: enables Traefik's rewrite-body plugin from local source (`enable-plugin --airgap`). Exit 4 = Traefik
  is managed by Argo CD/Flux; `gen-airgap-plugin-values` prints the values to add there.
* `verify/coder-ui-updates/`: the dashboard add-on checks `./install verify` runs (48 checks through the Gateway).

## Order of operations

`./install preflight` (read-only) -> `images` -> `secrets` -> `plugin` -> `deploy` -> `verify`, or `./install all`.
Every step is idempotent. `--yes` never prompts (secrets then come from `OIDC_CLIENT_SECRET` / `REGISTRY_PASSWORD`).
`--force` only affects `secrets` (replace a CA/secret whose content differs) and `images` (re-push).

## Reading failures

* preflight FAIL lines must be fixed; WARN lines are information (DNS not resolvable *from the install host* is common
  and fine if browsers and the cluster resolve it).
* `deploy` failed: `./install status` (pods, bootstrap Job log). Typical causes:
  - ImagePullBackOff: the nodes can't pull from `$REGISTRY` (auth -> `REGISTRY_USERNAME`; plain HTTP -> the nodes'
    container runtime must allow it; private CA -> the nodes must trust it). Fix, then re-run `./install deploy`: it
    restarts Deployments whose earlier rollout timed out.
  - Coder CrashLoopBackOff: `kubectl -n coder logs deploy/coder`. Keycloak unreachable or untrusted from inside the
    cluster (issuer URL, DNS, `ca.crt`) is the usual one; Coder refuses to start without its OIDC provider.
  - bootstrap Job failed: its log names the step. It signs in as the bootstrap admin (Secret `coder-init-auth`) once,
    keeps an API token in Secret `coder-platform-automation` and replaces it on every run; if the token expired it
    turns password sign-in on briefly (ConfigMap `coder-platform-auth`, one Coder restart) and off again.
* `verify` FAIL on dashboard checks with everything else OK: Traefik's rewrite-body plugin isn't loaded
  (`./install plugin`), or Coder was upgraded to a version the rewrites don't match (needs a newer bundle).

## Existing Coder (./upgrade, UPGRADE-EXISTING.txt)

* `./upgrade discover` and `./upgrade plan` are read-only; start there. BLOCK lines must be fixed, WARN lines inform.
* The chart runs in attach mode (ATTACH=true): it never changes Coder's settings, users, database or route. Coder
  itself is upgraded by `./upgrade coder` through Coder's own Helm release or Argo CD Application (the hub's, with
  ARGOCD_CONTEXT).
* Templates that won't build with the new image are found by `./upgrade plan` (static) and `./upgrade tfcheck`
  (terraform init in the cluster); the fix is a new bundle with EXTRA_PROVIDERS on the connected side.
* Keycloak groups: Coder compares group names exactly. Nested Keycloak groups arrive as /Parent/Child with the
  mapper's "Full group path" on. After a Keycloak group rename, Coder's CODER_OIDC_ALLOWED_GROUPS / mappings must be
  renamed where Coder is deployed from; General > Keycloak shows mismatches once connected.
* Rollback after `./upgrade coder`: UPGRADE-EXISTING.txt, section ROLLBACK (database restore + helm rollback).

## Things not to do

* Don't delete the `coder-db` Cluster, its PVCs, or `coder-platform-auth` / `coder-platform-automation` casually:
  the database is all of Coder's state; the other two are how the bootstrap Job gets in.
* Don't downgrade Coder: its database migrations are one-way. Back up first (README, "Upgrades").
* Don't enable the rewrite-body plugin twice (download and local): Traefik then disables all plugins.
* Don't edit Coder's settings through General > Keycloak expecting them to stick: they come from install.conf /
  `values-local.yaml` + `./install deploy`.

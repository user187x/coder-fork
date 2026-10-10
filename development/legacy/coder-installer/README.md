# coder-installer

Everything needed to put Coder back exactly as it runs on the minikube cluster (captured 2026-10-08): the forked
Coder `2.0.0-platform.4` with all its dashboard and server changes, its database, the dashboard add-on service, the
announcement banner, the workspace template and its icons.

```
coder-installer/
  chart/                 Helm chart "coder-installer" (release coder): Coder, database, add-ons, workspaces, Secrets
    charts/              the upstream coder chart 2.37.3 (vendored)
    files/ui/            the add-on service's code, pages and manifests (as served on the cluster)
    files/workspace/     the workspace template (*.tf) and its icons
  banner/                the announcement banner chart 0.4.1 and its values (release coder-banner)
  operators/             the CloudNativePG operator chart 0.29.0 (installed only when the cluster lacks it)
  defaults/              the add-ons' default logo and avatar (ConfigMaps made by install.sh)
  install.sh             prerequisites check, operator, image, default images, both releases, optional restore
  backup.sh              Secrets + data of the running installation into a backup directory
  restore.sh             a backup's data into an installed release
```

## Reinstall in three commands

```sh
./backup.sh                              # on the running cluster: writes backups/<date-time>/ (keep it private)
# ... new or wiped minikube, prerequisites below in place ...
./install.sh --backup backups/<date-time>
```

`install.sh --backup` uses the backup's `secrets.values.yaml` for the chart and then restores its data. Without
`--backup` you get the same installation with an empty database; the template push Job then creates the first owner
from Secret `coder-init-auth` and pushes the template.

Plain Helm works too (it is what `install.sh` runs):

```sh
helm upgrade --install coder ./chart -n coder --create-namespace -f backups/<date-time>/secrets.values.yaml --wait --timeout 20m
helm upgrade --install coder-banner ./banner/coder-banner-0.4.1.tgz -n coder -f banner/values.yaml --wait
./restore.sh backups/<date-time>
```

## What it reproduces

`chart/values.yaml` is the running installation; `helm template` of both releases was compared field by field with
the cluster's 56 objects and matches them. Two intended differences:

- The add-on service runs with `ARGOCD_APP` empty. On the cluster it may edit the `coder` Argo CD Application's
  `helm.values`, but that editor only understands the coder chart's own layout (`coder.env`), not this chart's
  (`coder.coder.env`). General > Keycloak still checks everything and repairs Keycloak's side and the Secrets.
- The database's PostgreSQL image is pinned (`ghcr.io/cloudnative-pg/postgresql:18.4-system-trixie`, what it runs),
  so a newer operator can't change it.

The banner is a release of its own, as on the cluster: with it inside, the release record would pass Helm's 1 MiB
limit (the add-on files alone are 850 KB). The chart's record is about 870 KB.

Not reproduced: the Secrets `coder-server-tls`, `coder-ca-cert` and `cloudflare-token` in namespace `coder` (nothing
uses them), the stale `coder-workspace` release in namespace `default`, and the workspaces' home volumes (back them up
separately if a workspace's files matter).

## Prerequisites (shared with other apps, not part of this installer)

- Traefik v3 with the Gateway API provider, Gateway `traefik/traefik-gateway` serving `*.grey-marshall.com` with the
  wildcard certificate, and the `rewrite-body` plugin (the banner chart's middlewares use it; Coder's pages are not
  rewritten: the forked dashboard has the add-ons built in, `uiUpdates.nativeDashboard`)
- StorageClasses `standard-k8s` (database) and `standard` (add-on data)
- Keycloak at `https://keycloak.grey-marshall.com` (realm `master`, client `coder`) in namespace `keycloak`
- DNS for `coder.grey-marshall.com` and `*.grey-marshall.com` pointing at Traefik
- Images reachable from the nodes: `harbor.grey-marshall.com/library/python:3.13-alpine`,
  `nginxinc/nginx-unprivileged:1.30.5-alpine`, `ghcr.io/cloudnative-pg/*`

`install.sh` checks the Gateway, the CRDs, the storage class and Keycloak's namespace before it changes anything.

## The Coder image

`ghcr.io/user187x/coder-platform:2.0.0-platform.4` was built locally from the fork (`~/workspace/coder`, commit
`9c74cd2b1`) and loaded into minikube; it is not on a registry. `install.sh` takes it from minikube, from Docker, or
from `<backup>/coder-image.tar` (`./backup.sh --image` saves it). To rebuild it:

```sh
cd ~/workspace/coder
./scripts/build_platform_image.sh --arch amd64 --image ghcr.io/user187x/coder-platform --tag 2.0.0-platform.4
minikube image load ghcr.io/user187x/coder-platform:2.0.0-platform.4
```

## Reinstalling over the current cluster

Today Coder belongs to the Argo CD Applications `coder` and `coder-banner` and to the add-on script; Helm can't take
over their objects, and `install.sh` stops if it finds them. To switch this cluster to the installer: take a backup,
delete both Applications (with their resources) and the add-on (`../coder-ui-updates/coder-ui-updates remove`),
then run `./install.sh --backup <dir>`. The database Cluster and Secrets are kept by `helm.sh/resource-policy: keep`
once the chart owns them; delete the PVCs `coder-db-*` yourself only if you mean to start from the backup.

## Changing the address

Helm can't pass this chart's values down to the coder subchart, so the host is in several places of
`chart/values.yaml`: `coderHost`, `wildcardHost`, the `CODER_ACCESS_URL`, `CODER_WILDCARD_ACCESS_URL`,
`CODER_NOTIFICATIONS_WEBHOOK_ENDPOINT` and `CODER_OIDC_ISSUER_URL` env and `coder.coder.httproute`, plus `coder.host`
in `banner/values.yaml`.

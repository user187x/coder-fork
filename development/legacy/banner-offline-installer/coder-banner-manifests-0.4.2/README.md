# Coder banner 0.4.2: plain Kubernetes manifests (no Helm)

This is the `coder-banner` chart from `coder-banner-airgap-0.4.2` rendered to plain YAML, one object per file, so it can
be applied with `kubectl` alone. Rendered with:

```
helm template coder-banner coder-banner-0.4.2.tgz -n coder \
  --set coder.host=coder.example.com --set image.registry=registry.internal:5000 \
  --set gateway.sectionName=https --set banner.enabled=false --set-string banner.message=
```

The only post-processing: `managed-by: Helm` became `managed-by: kubectl` and the Argo CD sync-wave annotations were removed.

## Quick install: `./install.sh`

```bash
./install.sh --dry-run    # ask, render, have the API server validate everything; changes nothing
./install.sh              # the same, then install and verify
```

Needs `kubectl` and `curl` (plus `docker`, `podman` or `skopeo` if it should push the image). It asks for everything the
files below leave as a placeholder or default, suggesting answers from the cluster (Coder's own HTTPRoute, the Gateway's
listeners, Traefik's Deployment). Press Enter to accept a suggestion:

| Asked | Suggested from |
|---|---|
| Coder namespace, Service, port, **hostname** | the Service labelled `app.kubernetes.io/name=coder` and Coder's HTTPRoute |
| **URL path** of the banner (default `/__banner`; admin page = `<path>/admin`) | rejects paths Coder uses itself (`/api`, `/login`, ...) |
| Coder roles allowed to publish, label of the user-menu link | `owner`, `Banner` |
| Gateway, listener | Coder's route, else the Gateway's only HTTPS listener |
| Image, pull secret, and whether to push `images.tar` from the airgap bundle | |
| Traefik namespace / Deployment, and whether to patch it | the Gateway's namespace |

It then renders the files into `rendered/` and checks that no object belonging to someone else would be overwritten.
The API server dry-runs everything. After that it pushes the image, patches Traefik (and rolls Traefik back if the plugin
does not load), applies the banner, waits for it, and checks through Traefik that the loader, `banner.json` and the menu
link work. A failed run or `--dry-run` never replaces `rendered/`, which always holds what is installed.

Re-run or upgrade with the same answers: `set -a; . rendered/answers.env; set +a; ./install.sh -y`. Traefik is left
alone when the plugin is already there, and a published banner is kept. Every answer can also be set in the environment
(`./install.sh --help`).

The sections below are the same steps by hand.

```
coder-banner/                                namespace coder: everything the banner adds, all named coder-banner*
  01-serviceaccount.yaml
  02-role.yaml                               get/update/patch on the ONE ConfigMap coder-banner-state
  03-rolebinding.yaml
  04-configmap-state.yaml                    what admins publish (metadata only, see "Re-applying")
  05-configmap-content.yaml                  chart defaults: hidden, empty message
  06-configmap-app.yaml                      server.py, live.py, banner.js, admin page
  07-configmap-vendor.yaml                   Anime.js 4.5.0 (MIT), text effects
  08-configmap-emoji.yaml                    emoji picker data
  09-deployment.yaml                         python:3.13-alpine running /app/server.py
  10-service.yaml                            ports 80 (http) and 8081 (WebSocket live channel)
  11-middleware-identity-encoding.yaml       strip Accept-Encoding / conditional headers for HTML loads
  12-middleware-inject.yaml                  rewrite-body: add <script src="/__banner/banner.js"> before </head>
  13-middleware-chain.yaml                   coder-banner = identity-encoding -> inject
  14-middleware-compress.yaml                re-compress the rewritten JS
  15-middleware-menu-rewrite.yaml            rewrite-body: "Codernauts" menu item -> "Banner" (admins only)
  16-middleware-menu-chain.yaml              coder-banner-menu = compress -> identity-encoding -> menu-rewrite
  17-httproute.yaml                          HTTPRoute coder-banner on traefik/traefik-gateway, listener https
traefik-plugin/                              namespace traefik: the one change to an existing object
  01-configmap-rewrite-body-plugin.yaml      plugin source (traefik/plugin-rewritebody v0.3.1, Apache-2.0)
  02-traefik-deployment-patch.json           JSON patch that adds 1 arg, 1 volumeMount, 1 volume to Traefik
```

Nothing here is a CRD, a Gateway, a GatewayClass, a TLS Secret, or Coder's own HTTPRoute, Deployment or Service.

## 1. Set your values

The files carry placeholders. Change them in place before applying:

| What | Placeholder | File(s) |
|---|---|---|
| Coder hostname | `coder.example.com` | `17-httproute.yaml` |
| Banner image (any image with `python3` 3.8+) | `registry.internal:5000/library/python:3.13-alpine` | `09-deployment.yaml` |

```bash
cd coder-banner-manifests-0.4.2
sed -i 's/coder\.example\.com/coder.YOURDOMAIN/' coder-banner/17-httproute.yaml
sed -i 's#registry\.internal:5000/library/python:3\.13-alpine#YOUR-REGISTRY/library/python:3.13-alpine#' coder-banner/09-deployment.yaml
```

The image is `images/images.tar` in the airgap bundle (`docker load -i`, tag, push). Needs a pull secret? Add
`imagePullSecrets: [{name: <secret>}]` under `spec.template.spec` in `09-deployment.yaml`.

These are the rendered defaults. They are right for this project's cluster; if yours differs, edit these too:

| Default | Where it appears |
|---|---|
| Coder namespace `coder` | `metadata.namespace` in every `coder-banner/` file; `CODER_URL` in 09; RoleBinding subject in 03 |
| Coder Service `coder`, port `80` | `CODER_URL` in 09; the three Coder `backendRefs` in 17 |
| Gateway `traefik/traefik-gateway`, listener `https` | `parentRefs` in 17 |
| Traefik in namespace `traefik`, Deployment `traefik`, container `traefik` at index 0 | `traefik-plugin/` (the patch's `test` op fails safely if container 0 is not `traefik`) |

## 2. Check the cluster first (read-only)

```bash
# Coder's Service and own route (the banner route sits beside it, same host)
kubectl -n coder get svc coder
kubectl -n coder get httproute -o custom-columns=NAME:.metadata.name,HOSTS:.spec.hostnames,PARENT:.spec.parentRefs

# Listener names on the Gateway: the banner route binds to "https"
kubectl -n traefik get gateway traefik-gateway \
  -o jsonpath='{range .spec.listeners[*]}{.name}{"\t"}{.protocol}{"\t"}{.port}{"\t"}{.allowedRoutes}{"\n"}{end}'

# None of the banner's objects should exist yet (all "NotFound")
kubectl -n coder get sa,cm,deploy,svc,role,rolebinding,httproute,middleware.traefik.io -o name | grep coder-banner

# Traefik: args, and anything that would conflict with the plugin
kubectl -n traefik get deploy traefik -o jsonpath='{.spec.template.spec.containers[0].name}{"\n"}{range .spec.template.spec.containers[0].args[*]}{@}{"\n"}{end}'
```

Stop and sort it out before step 3 if:

* **An arg starts with `--experimental.plugins.rewrite-body.`** (the download variant of the same plugin). Traefik refuses a
  plugin name defined twice and then turns off **all** plugins while still reporting Ready. Remove that arg (or that
  entry from Traefik's Helm values) first.
* **An arg starts with `--configFile`.** Traefik then ignores command-line args: put the plugin in that file instead
  (settings: `support/traefik/rewrite-body-plugin-airgap.yaml` in the bundle).
* **Traefik's Deployment is managed by Argo CD or Flux** (`argocd.argoproj.io/instance` label, `tracking-id` annotation,
  or a Flux label). It would revert the patch. Put the plugin into Traefik's values instead, using
  `support/traefik/rewrite-body-plugin-airgap.yaml`. Skip step 3; `coder-banner/` still applies as it is.
* **Traefik is older than v3.** The plugin was tested with v3.3 to v3.7.

## 3. Enable the plugin in Traefik (the one change to an existing object)

```bash
kubectl apply --server-side --field-manager=traefik-plugin -f traefik-plugin/01-configmap-rewrite-body-plugin.yaml

# Preview: the API server validates the patch, nothing changes
kubectl -n traefik patch deploy traefik --type=json --patch-file traefik-plugin/02-traefik-deployment-patch.json \
  --field-manager=traefik-plugin --dry-run=server -o name

kubectl -n traefik patch deploy traefik --type=json --patch-file traefik-plugin/02-traefik-deployment-patch.json \
  --field-manager=traefik-plugin
kubectl -n traefik rollout status deploy/traefik --timeout=180s

# Traefik must say "Plugins loaded". "Plugins are disabled" means it is up but loads NO plugins.
kubectl -n traefik logs deploy/traefik | grep -i plugin
```

Apply the patch **once** only: the `-` path appends, so a second run adds duplicates. This makes Traefik start a new pod.
With the Traefik chart default (RollingUpdate, `maxUnavailable: 0`) the old pod serves until the new one is Ready, but
long-lived connections (web terminals, etc.) reconnect once. Do it in a quiet window.

If Traefik does not come back: `kubectl -n traefik rollout undo deploy/traefik`.

## 4. Apply the banner

```bash
kubectl apply --server-side --field-manager=coder-banner -f coder-banner/
kubectl -n coder rollout status deploy/coder-banner
kubectl -n coder get httproute coder-banner -o jsonpath='{range .status.parents[*].conditions[*]}{.type}={.status} {.reason}{"\n"}{end}'
```

The route must show `Accepted=True` and `ResolvedRefs=True`. `kubectl apply -f <dir>` applies files in name order, so the
ConfigMaps and RBAC exist before the Deployment and the Middlewares before the route. Server-side apply is used because
three ConfigMaps are 90-120 KB each; client-side apply would copy each into a `last-applied-configuration` annotation.

If the plugin is missing, Coder keeps working: Traefik drops only the banner's routes and requests fall through to
Coder's own route.

## 5. Verify and use

```bash
curl -s https://coder.YOURDOMAIN/ -H 'Accept: text/html' | grep -o '/__banner/banner.js'   # the loader is injected
curl -s https://coder.YOURDOMAIN/__banner/banner.json                                      # current banner (hidden, empty)
```

Open `https://coder.YOURDOMAIN/__banner/admin` as a Coder **owner**, write a message, then **Publish**. It appears in every
open Coder tab at once. The user menu shows **Banner** instead of "Codernauts" after one hard refresh (Ctrl+Shift+R).

It starts **hidden and empty**: nobody sees a banner until an admin publishes one.

## Re-applying / upgrading

`kubectl apply --server-side -f coder-banner/` is safe to repeat. `04-configmap-state.yaml` has no `data`, so what an admin
published is kept. Never `kubectl replace` or delete that ConfigMap; that erases the published banner.

After a Coder upgrade, check the menu still says "Banner". The menu rewrite is tied to Coder v2.36.0's minified JS. If it
no longer matches, the menu goes back to stock and nothing else is affected.

A later `helm upgrade traefik` rewrites Traefik's Deployment and drops the plugin arg: repeat step 3 (patch only; the
ConfigMap stays). With Helm 4, use `--force-conflicts` because of the `traefik-plugin` field manager.

## Removal (always in this order)

```bash
# 1. The banner. Coder is back to stock within seconds; Traefik does not restart.
kubectl delete -f coder-banner/ --ignore-not-found

# 2. The plugin, only if the banner will not be used again. Find the indexes of the three additions...
kubectl -n traefik get deploy traefik -o jsonpath='{range .spec.template.spec.containers[0].args[*]}{@}{"\n"}{end}' | grep -n rewrite-body
kubectl -n traefik get deploy traefik -o jsonpath='{range .spec.template.spec.containers[0].volumeMounts[*]}{.name}{"\n"}{end}' | grep -n rewrite-body
kubectl -n traefik get deploy traefik -o jsonpath='{range .spec.template.spec.volumes[*]}{.name}{"\n"}{end}' | grep -n rewrite-body
#    ...then remove them. grep -n counts from 1 and JSON patch counts from 0, so subtract 1 from each line number:
kubectl -n traefik patch deploy traefik --type=json -p '[
  {"op":"remove","path":"/spec/template/spec/containers/0/args/<ARG_INDEX>"},
  {"op":"remove","path":"/spec/template/spec/containers/0/volumeMounts/<MOUNT_INDEX>"},
  {"op":"remove","path":"/spec/template/spec/volumes/<VOLUME_INDEX>"}]'
kubectl -n traefik rollout status deploy/traefik
kubectl delete -f traefik-plugin/01-configmap-rewrite-body-plugin.yaml
```

Remove the banner before the plugin: while a plugin Middleware exists, Traefik logs errors for it.

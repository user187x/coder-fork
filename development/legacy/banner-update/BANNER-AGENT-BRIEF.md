# Agent brief: the Coder banner, on an air-gapped cluster

You are an AI assistant helping a person deploy or operate the **Coder banner** on an **air-gapped Kubernetes cluster**.
Read this file completely before you run anything. `INSTALL.md` is the guide for humans; this file gives you the intent, the
limits, and what to do when something goes wrong. It ships inside the bundle as `AGENTS.md`.

## TL;DR

1. **Purpose.** Show an admin-editable announcement banner (plus a "Banner" item in the user menu) on the Coder dashboard. It
   works through Traefik: a plugin adds one `<script>` line to Coder's HTML. **Coder itself is never modified.**
2. **Prime directive.** The target cluster already runs a working, TLS-terminating Traefik Gateway, with other applications'
   HTTPRoutes and Middlewares. **Nothing that already exists may be disturbed.** Treat everything outside the two
   exceptions below as strictly read-only: that is the requirement, not a nicety.
3. **Exactly two things may change.**
   (a) The banner's own objects, in Coder's namespace, all named `coder-banner*` (chart `coder-banner`).
   (b) **One additive patch to Traefik's running Deployment**: one argument, one volumeMount and one volume for the
   `rewrite-body` plugin, plus a ConfigMap holding the plugin's source. Done only by `traefik-plugin/enable-plugin --airgap`.
4. **Method.** Read-only look, then ask the user for what you cannot discover, then dry run, then the user says go, then
   deploy, verify, and finally *prove* nothing else changed (`existing-objects`).
5. **When unsure, stop and ask.** Everything here is reversible and nothing is urgent.

## Where you are

You are in an unpacked bundle `coder-banner-airgap-<version>/` on a machine with `kubectl` access to the air-gapped cluster.
There is **no internet**: do not `helm repo add`, `pip install`, `docker pull` from the internet, or fetch anything.

| File | What it is |
|---|---|
| `banner-guide` | Interactive, step-by-step installer (modes `deploy`, `check`, `remove`, `explain`; `--dry-run` changes nothing) |
| `preflight-banner` | Read-only checks before installing (exit 1 on any FAIL) |
| `existing-objects` | `save FILE` / `compare FILE`: proves no existing CRD, Gateway, route or middleware changed |
| `traefik-plugin/enable-plugin`, `disable-plugin` | The additive Traefik patch and its exact reverse (`--diff-only` = preview) |
| `install-banner-chart`, `uninstall-banner-chart`, `verify-menu`, `banner-set` | Helm install / removal / end-to-end check / change chart defaults |
| `coder-banner-<version>.tgz`, `argocd/coder-banner-application.yaml` | The chart, and an Argo CD Application for GitOps installs |
| `images/images.tar` | The banner's container image `python:3.13-alpine`, **linux/amd64 only** |
| `SHA256SUMS`, `INSTALL.md` | Integrity list; the human guide |

## Hard rules: never do these

1. **Never touch Traefik's Helm release or re-render Traefik**: no `helm install/upgrade/uninstall/rollback traefik`.
2. **Never apply, replace or delete** Gateway API or Traefik CRDs, the GatewayClass, the Gateway, or TLS Secrets.
3. **Never modify or delete an HTTPRoute or Middleware that is not the banner's** (`coder-banner*`). Never hand-write
   routing objects into the cluster.
4. **Never bypass a guard** (`TRAEFIK_ALLOW_GITOPS=1`, `TRAEFIK_ALLOW_OLD=1`, `--force`, `--force-conflicts`) unless the user
   explicitly approves *after* you explained the risk in plain words.
5. **Never publish or change a banner yourself.** It appears at once in every open Coder tab of every user. Publishing is the
   admin's job at `https://<coder host>/__banner/admin` (a Coder *owner*). `banner-set` changes what all users see: same rule.
6. **Never answer a "change something" prompt of `banner-guide` (or run without `--dry-run`) without the user's explicit go-ahead
   in this conversation.** Never set `BANNER_GUIDE_ASSUME_DEFAULTS=1` for a real run.
7. **Never "fix" an UNEXPECTED difference or a FAIL by editing other people's objects.** Report it and ask.
8. Do not print secrets (kubeconfig, registry credentials, TLS keys).

## What gets installed (so you can explain it)

- `HTTPRoute/coder-banner` beside Coder's own route on the same host: HTML page loads go through a middleware chain that adds
  `<script src="/__banner/banner.js">`; `/__banner/*` goes to the banner service; workspace apps (`/@...`) are passed through.
- 6 Traefik `Middleware`s `coder-banner*`, a `Deployment` + `Service` `coder-banner` (small Python server), 5 ConfigMaps
  (`coder-banner-state` holds what admins publish), a ServiceAccount, and a Role/RoleBinding limited to that one ConfigMap.
- **A fresh install starts hidden and empty** (`banner.enabled=false`, empty message): nobody sees anything until an admin
  publishes. An upgrade keeps whatever is showing. The guide does this automatically; a by-hand install must pass
  `--set banner.enabled=false --set-string banner.message=`.
- The route attaches to **one Gateway listener** (`gateway.sectionName`, normally the HTTPS one), so it can never answer in place
  of an HTTP->HTTPS redirect route. Empty means every listener.
- If a prerequisite is missing (plugin not loaded, image not pullable) Coder keeps working: only the banner is absent.

## Standard procedure

1. **Verify the bundle.** `sha256sum -c coder-banner-airgap-<v>.tar.gz.sha256` (next to the tarball), unpack, then
   `sha256sum -c SHA256SUMS --quiet`. Compare the tarball's checksum with the one the user was given separately.
2. **Confirm the cluster.** `kubectl config current-context` and `kubectl get nodes`. **Ask the user to confirm this is the
   air-gapped cluster.** Check the nodes are amd64: `kubectl get nodes -o jsonpath='{.items[*].status.nodeInfo.architecture}'`
   (otherwise the image will not run: the banner pod fails, Coder is unaffected).
3. **Collect facts** (discover read-only, ask the user for the rest): Coder namespace, hostname, Service name and port; the
   Gateway name, namespace and listeners; Traefik's namespace and Deployment; the internal registry (and pull secret) for the
   image; whether Argo CD/Flux manages Traefik or will install the banner. Do not assume the defaults (`coder`, `traefik`,
   `traefik-gateway`, `coder.xxx.com` are only this project's dev values).
4. **Look, read-only.**
   `CODER_HOST=<host> GATEWAY_SECTION=<listener> BANNER_IMAGE=<registry>/library/python:3.13-alpine ./preflight-banner`
   then `./traefik-plugin/enable-plugin --airgap --diff-only`. Read every FAIL/WARN aloud to the user (table below).
5. **Rehearse.** `./banner-guide --dry-run --mode deploy`. Nothing is changed.
6. **Deploy, only after the user says go.** `./banner-guide --mode deploy`. It is interactive and asks before every change;
   suggested answers are in `[brackets]`. Best run by the user in their own terminal (an agent's shell is often not interactive), so they answer each prompt.
   It: loads the image into the registry (choice 1) or uses one already there (2/3); records what exists
   (`banner-guide-before.snapshot`); shows and applies the Traefik patch; runs preflight; installs (Helm, or an Argo CD
   Application); verifies; then compares against the record.
7. **Verify.** `./banner-guide --mode check`, `./verify-menu`, and
   `./existing-objects compare banner-guide-before.snapshot` (must end `OK: no existing ...`). Confirm
   `kubectl -n <coder ns> get httproute coder-banner -o yaml` shows `Accepted=True`.
8. **Hand over.** Tell the user: how to publish (admin page, as a Coder owner, one hard refresh for the menu item), how to
   remove it (below), and the caveats (below).

## Reading the results

| You see | It means | You do |
|---|---|---|
| preflight `FAIL ... already exists and does NOT belong to the banner` | Some object the banner would create already exists under another owner | **Stop.** Do not delete it. Tell the user which object; they decide (rename via chart values, or ask the owner) |
| preflight `FAIL ... no listener named 'X'` / `only accepts routes from namespace` | Wrong `gateway.sectionName`, or the listener will not accept a route from Coder's namespace | Pick the correct listener with the user; never edit the Gateway |
| preflight `WARN existing redirect route(s) ...` | An HTTP->HTTPS redirect exists and the banner route could shadow it | Bind the banner to the HTTPS listener (`GATEWAY_SECTION`, `gateway.sectionName`) |
| `enable-plugin` exit **4**: "managed by a GitOps controller" | Argo CD/Flux would revert a direct patch | Do **not** override. Give Traefik's owners `traefik-plugin/rewrite-body-plugin-airgap.yaml` (a values file); when Traefik loads the plugin, continue |
| `enable-plugin` exit **3**: "--configFile" | Traefik reads a config file and ignores CLI args | The plugin settings must go into that file; involve Traefik's owners |
| `enable-plugin` exit **5**: Traefik older than v3 | Plugin untested there | Ask; do not set `TRAEFIK_ALLOW_OLD=1` on your own |
| `enable-plugin` exit **2** or "API server REJECTS" | A different local `rewrite-body` plugin is defined, or the change is invalid | Show the message; do not force anything |
| "Traefik is up but DISABLED its plugins" / log `Plugins are disabled` | Plugin rejected (e.g. a duplicate name); Traefik stays Ready but no banner can appear. The script rolls back | Read the reason line; a duplicate `experimental.plugins.rewrite-body` in Traefik's own values is the usual cause |
| Banner pod `ImagePullBackOff` | Registry/pull secret/architecture | Check `image.registry`, `imagePullSecrets`, that the image was pushed, that nodes are amd64. Coder is unaffected |
| `verify-menu`: page has no `/__banner/banner.js` | Plugin not loaded, route not `Accepted`, or Middlewares missing | `./banner-guide --mode check`; look at the route status and Traefik's log (`kubectl -n <traefik ns> logs deploy/<traefik> \| grep -i plugin`) |
| `verify-menu`: menu not rewritten, but banner works | Coder's version differs from the tested one (v2.36.0); the menu shortcut simply stays stock | Fine; mention it to the user |
| Admin page "Not allowed" / "Could not verify your session" | User is not a Coder *owner*, or the banner pod cannot reach Coder's Service | Check `admin.roles`, `coder.service.name`/`port` |
| `existing-objects compare` prints `UNEXPECTED ...` | Something that existed before changed (maybe another team, maybe Argo CD; the tool compares spec + generation) | Do not "repair" it. Report the object; inspect it read-only |
| HTTP no longer redirects to HTTPS | The banner route is attached to the HTTP listener | `helm upgrade ... --set gateway.sectionName=<https listener>` (or the Argo value) |

## Removal and rollback (always in this order)

1. **The banner (fast, no Traefik restart, Coder returns to stock in seconds):** `./banner-guide --mode remove`, or
   `./uninstall-banner-chart` (Helm), or for Argo CD `kubectl -n argocd delete application coder-banner` then
   `./uninstall-banner-chart --verify-only`. It proves Coder's page and JavaScript are stock again.
2. **The Traefik plugin, only if the banner will not be used again:** `./uninstall-banner-chart --remove-plugin` or
   `./traefik-plugin/disable-plugin` (refuses while a plugin Middleware still exists). It removes just the plugin argument,
   mount, volume and ConfigMap; Traefik starts one new pod.

## Facts and limits you should be honest about

- **Tested:** Coder v2.36.0; Traefik v3.7.12; the Traefik patch on chart versions 41.4.0, 40.0.1, 37.0.0, 35.0.1 (Traefik v3.3 to v3.7);
  the safety checks against real and simulated objects.
- **Not tested:** this actual cluster; a live Argo CD/Flux-managed Traefik (the guard is tested with a simulated marker only); a
  live HTTP->HTTPS redirect route (the shadowing risk comes from Gateway API precedence rules, and is prevented by binding
  to the HTTPS listener).
- The Traefik patch makes Traefik start one new pod. With the chart default (rolling update, `maxUnavailable: 0`) the running
  pod keeps serving until the new one is Ready: no outage is expected, but long-lived connections (terminals) reconnect once.
  The preview prints this Deployment's real strategy; if it is `Recreate`, warn the user and pick a quiet moment. A rollout
  that does not become Ready in 180 s is undone automatically.
- Traefik installed with Helm: a later `helm upgrade traefik` (Helm 4: use `--force-conflicts`) removes the plugin; re-run
  `./traefik-plugin/enable-plugin --airgap`. If Traefik's owners add the plugin to their own values instead, use
  `traefik-plugin/rewrite-body-plugin-airgap.yaml` and **no** download entry (a duplicate name disables all plugins).
- Needs on your side: `kubectl`, `helm` 3.14+ or 4, `jq`, `curl`; cluster-admin rights; a way to load an image
  (`docker`, `podman`, `skopeo`) if the user's registry needs it.

## What to report at the end

Say plainly: which objects were created (names, namespace), that Traefik's Deployment received the one additive patch (paste the
`+ arg / + volumeMount / + volume` lines), the output of `existing-objects compare`, the banner's state (hidden until published),
how to publish, how to remove, and anything you skipped or could not verify. Do not claim more than you checked.

# Changelog

## 1.12.2 – 2026-10-11

* **Desktop (noVNC) locked down;** the "you are exposing your VNC server to all users" warning is gone.
  * TigerVNC's X server now starts directly (not through `vncserver`) and listens on no TCP port, only on a Unix socket
    that only the workspace user can open (`/tmp/coder-vnc/rfb.sock`: directory 0700, socket 0600).
  * websockify serves noVNC on `127.0.0.1:6901` only (it listened on every interface, so the password-less desktop was
    reachable from the cluster network) and connects to that socket. The only way in is Coder's app proxy.
  * The X display has an authorization cookie (`~/.Xauthority`).
  * Takes effect when a workspace restarts.

## 1.12.1 – 2026-10-11

* **No gap when Coder rolls out:** the old Coder pod keeps serving for 15 s after it is taken out of the Service
  (`preStop` sleep), so Traefik stops sending it requests before Coder closes its server. Until now those requests got
  a 502, which showed as the health warning `EWS01` during a deploy. The new pod already started first and had to be
  ready before the old one stopped. Tested with a restart under a request every 0.2 s: all 766 returned 200.

## 1.12.0 – 2026-10-11

Coder image `2.0.0-platform.11-airgap` (fork `92daaa856`).

* **Agent terminal drop-down:**
  * When the terminal terminates (its shell exits, the connection drops, or End session), the drop-down rolls back up
    and starts over: the next open is a new session.
  * "End session when closed" is a slider toggle.
  * The custom command is on by default and runs btop.
  * Controls in three groups: the text size buttons and size in a small oval on the left, the custom command centred,
    the session controls on the right.
  * The terminal's columns x rows show as a very faint overlay pinned to its top-right corner.
  * The pop-out window has a button that opens the session in a separate browser window (Coder's terminal page, same
    session).
* **btop refreshes every 100 ms** instead of its default 2000. The workspace start sets it for users without a btop
  configuration, or whose configuration still has btop's default; a value someone chose is left alone.

## 1.11.0 – 2026-10-11

Workspace image `ghcr.io/user187x/coder-workspace:1.2.0` (Coder image unchanged: `2.0.0-platform.10-airgap`).

* **k9s and kubectl in every workspace:** a new self-contained package, `k8s-devtools` 1.0.0
  (`development/workspace-image/packages/`, 48 MB), built by `development/tools/build-k8s-devtools.sh` from the
  developer's own machine:
  * the exact custom k9s binary (v0.50.18, commit `6dbf571c…-patched`) and its configuration: settings, aliases, the
    copy-secret plugin (with `k9s-copy-secret` on PATH) and all skins. Per-machine state (`clusters/`,
    `screen-dumps/`, `benchmarks/`, backups) is left out. `k9s` gives users without a k9s configuration the bundled
    one on first run; an existing one is never touched;
  * the exact kubectl binary (v1.36.0);
  * bash completion for both, now enabled for every interactive shell (`/etc/bash.bashrc`; the default `~/.bashrc` is
    unchanged).
* kubectl and k9s need a cluster to talk to, via a kubeconfig in `~/.kube/config` or `KUBECONFIG`. Workspaces get no
  cluster access of their own (no extra permissions). minikube is not included: running it inside a workspace needs a
  privileged pod or Sysbox nodes.

## 1.10.2 – 2026-10-11

Coder image `2.0.0-platform.10-airgap` (fork `f6ebdb50e`).

* **Workspace card borders:** only just noticeable, and slower.
  * Every layer is at 45% strength.
  * The ready ring is 2 px wide and takes 9 s per turn (it was 4 px and 2 s).
  * The electric border runs at a third of its pace (18 s per cycle) with less than half the displacement, and a fainter
    sheen and glow.
  * Strength and ring speed are the CSS variables `--fx-strength` and `--fx-ring-turn` in `agentBorder.css`.

## 1.10.1 – 2026-10-11

* **Workspaces button icon:** green. The navbar's icon is now the chart's `files/ui-updates/workspaces-icon.png`, which
  Traefik serves at `/workspaces-icon.png` from the platform service, so changing it takes a chart upgrade, not a new
  Coder image. The fork's built-in copy is green too (fork `542e5e2b5`, in the next Coder image). The new icon was first
  committed in `3a89b21` (with a FORK.md note).

## 1.10.0 – 2026-10-11

Coder image `2.0.0-platform.9-airgap` (fork `019d9745b`) and workspace image `ghcr.io/user187x/coder-workspace:1.1.0`.

* **Workspace image 1.1.0:** btop, bat (`bat` links to Ubuntu's `batcat`), vim, eza, fzf, grc, jq and yq installed (tmux
  already was). yq is Ubuntu's package: the jq wrapper for YAML (kislyuk/yq, jq syntax), not the Go yq.
* **Default `~/.bashrc`** (`development/workspace-image/shell/bashrc`): every workspace start installs it for users who
  have no `~/.bashrc`, or still Ubuntu's stock one; an edited `~/.bashrc` is left alone. Also installed when missing:
  `~/.profile` (so login shells read `~/.bashrc`) and `~/.config/vim/vimrc`, which the new `.bashrc` points Vim at
  (`VIMINIT`): Vim's defaults, then `~/.vimrc` if there is one.
* **Agent terminal:** a "Custom command" toggle. It's off by default; turned on, its command (btop until changed) is
  editable, new sessions run it first and continue in the shell when it exits, and "Run" starts one at once. The text
  size buttons are Font Awesome's plus-circle / minus-circle.

## 1.9.0 – 2026-10-10

Coder image `2.0.0-platform.8-airgap` (fork `a68ffe61e`).

* **Workspace page:** the agent card's solid green border is replaced by an animated border that shows the workspace's
  state:
  * **Initializing** (starting, agent connecting, startup scripts running): the electric border of
    `update-todos/workspace-loadding.html`, in green.
  * **Pending or a problem** (build queued, a startup script or module failed or timed out, the agent timed out
    connecting, a dev container error): the same border in fiery orange.
  * **Failed** (failed build, or the agent of a running workspace disconnected): the same border in red.
  * **Ready**: the rotating green border of `update-todos/workspace-border.html`.

  Stopped, stopping and deleted workspaces keep Coder's border. Nothing in the layout moves between states, the card
  (its logs, its terminal) is never remounted, and with reduced motion nothing animates.

## 1.8.0 – 2026-10-10

Coder image `2.0.0-platform.7-airgap` (fork `d7a068929`).

* **Breadcrumb:** Admin Settings > *category* names the category open (Health, Authentication, Customize, ...) instead
  of a fixed "General", which stays for pages outside the categories.
* **Fixed: the end of long pages was hidden** behind the deployment stats bar and the Chats dock (e.g. "Source Code
  Pro", the last terminal font in Customize). Every page that scrolls ends with room to spare; full-height pages (the
  workspace page) are unchanged.
* **HUD:**
  * Fixed: Highest CPU / Highest memory showed "—" without metrics-server, even with workspaces running. They fall back
    to what the running workspaces were built with, then to what their pods request (the hint says which).
  * Fixed: a tie quietly showed one account. It now shows the first account and the number tied ("admin +1 tied").
  * Every figure opens what is behind it:
    * the top account's workspace (CPU, memory, GPUs, ping, time online)
    * that account's workspaces (most workspaces, most failures)
    * the failing workspace
    * the running workspaces, User Quota, or Workspace Health
  * Moving figures to the top is a **Sort** menu at the top right of the totals bar, with Reset order.
* **SSH key from the .p12:**
  * Settings > Certificates can make the certificate's private key your Git SSH key: at upload ("Also use its private
    key as my SSH key"), or with "Use as SSH key..." (Settings > SSH keys > "Use P12..." leads there).
  * The key is decrypted in the browser, both AES and the legacy 3DES of many CAs. The password is asked for only when
    the file has one, is never sent, stored or logged, and is cleared once the key is out.
  * Coder stores the key as it stores the keys it generates (new endpoint `PUT /api/v2/users/{user}/gitsshkey/import`:
    RSA 2048+, ECDSA P-256/384/521, Ed25519).

## 1.7.0 – 2026-10-10

Coder image `2.0.0-platform.6-airgap` (fork `a65bc39b7`) and a new workspace image `ghcr.io/user187x/coder-workspace:1.0.0`.

* **Fixed: `core.<pid>` files in every workspace home.** The template installed `xfce4-goodies` at every start, which
  pulls in light-locker; the screen locker needs a display manager, crashed at each desktop start, and with unlimited
  core dumps left a ~45 MB `core.<pid>` each time. The workspace image leaves light-locker (and LightDM) out and does not
  start the XFCE screensaver; the pod starts the agent with `ulimit -c 0`; the startup script removes the core dumps
  earlier workspaces left (only `core.<digits>` files that are ELF core dumps).
* **Workspace image** (`development/workspace-image`, `tools/build-workspace-image.sh`): enterprise-base with the desktop
  preinstalled (no `apt-get` at start, so it also works offline), JetBrains Mono Nerd Font as the desktop's and its
  terminal's font and the default monospace, and tmux 3.8 with the bundled configuration (`tmux-installation.deb`; set
  up for each user without one). `codercom/enterprise-base` is no longer shipped. The startup script starts the desktop
  first, and a failed marimo download (no internet) no longer stops the rest of it.
* **Web terminal:** JetBrains Mono Nerd Font is bundled and the default terminal font. The agent terminal drop-down has
  a Pop out button (a floating window with the same session; closing it leaves the drop-down collapsed) and text size
  buttons that start at the largest size fitting 80x24 (btop's minimum) and show the columns x rows.
* **Global Workspace Scheduler** (Health > User Quota): require start and stop times on every workspace (each user's, or
  one schedule for all), a maximum active time, ignore admins, exclude users. Enforced by Coder on create and on every
  schedule change; users fix existing workspaces in a dialog before going on; the create form asks for the schedule.
* **Monitoring:** a security posture panel - zero-trust checks (privileged and dormant accounts, password sign-in, MFA in
  Keycloak, API tokens, shared ports and apps, pod isolation, network policies, image provenance) and runtime signals
  (restarts and OOM kills, failed workspaces, disconnected or outdated agents, workspaces that never stop).
* **HUD** (navbar, `/hud`, admins): totals, top accounts and health figures updated in place every 10 s, with changes
  and sparklines; click a figure to move it to the top.
* `minikube-support`: every custom image is loaded (`minikube ssh` read the rest of the image list from stdin, so only
  the first one was), and a build is recognised by its creation time and layers rather than the image ID, which differs
  between Docker's containerd image store and the nodes' (images were reloaded, and their pods restarted, every time).

## 1.6.0 – 2026-10-10

* Coder image `2.0.0-platform.5-airgap` (fork commit `ea80521c2`). The Admin menu starts with Settings and an add row
  (+) only (no more fixed Accounts / Health links). The + opens General's categories not linked yet (Accounts, Super
  Intelligence, Announcement, Authentication, Classification, Customize, Health, Monitoring, Network, Notifications,
  OAuth2 Applications, Observability, Overview, Persistence, Security); each quick link shows its category on the left
  and a remove button and a drag handle on the right (drag, or Enter + arrow keys + Enter, to reorder). Saved per user
  at once; as many links as there are categories (server limit raised from 8 to 32).
* `minikube-support`: an image rebuilt under the same tag is reloaded (compared by image ID), and `./make --deploy`
  restarts the Deployments that run it.

## 1.5.0 – 2026-10-10

* No nginx: the workspace template's icons (`/__icons/<file>`) are served by the platform service
  (`coder-ui-updates`) from the icons ConfigMap, and Traefik's Gateway routes `/__icons/` to it
  (`coder-workspace.icons.backend`). The nginx Deployment and Service and the `nginxinc/nginx-unprivileged` image are
  gone (one pod and ~24 MB fewer). Responses: images only, plain file names, `Cache-Control: public, max-age=3600`,
  ETag, and SVGs sandboxed by a Content-Security-Policy. Upgrading removes the old Deployment and Service.

## 1.4.0 – 2026-10-10

* `./make --deploy`: guided install for Minikube or EKS. Asks the domain (derives `coder.`, `*.`, `keycloak.`),
  creates self-signed certificates or checks the ones you bring (pair, chain, hostnames, expiry; DER and
  encrypted keys converted), then installs the prerequisites, Secrets and chart. Answers are remembered.
* Minikube: the sudo steps (tunnel, `/etc/hosts`, trusting the CA) are planned and agreed to up front; sudo asks
  for the password once, then and there, and the steps run at the end unattended. Steps already done are skipped;
  without sudo the exact commands are printed.
* `./make --deploy` writes a log (`.deploy/deploy.log`, follow it with `tail -F`) and, when a step fails, names the
  failing command and the log instead of exiting silently. Fixed: the deploy exited silently (exit 1) before
  starting the tunnel when no tunnel was running yet.
* `minikube-support`: profile, `local-path` storage as default (minikube's hostpath class breaks CloudNativePG
  replicas on multi-node clusters), image loading, CoreDNS rewrite of `*.<domain>` to Traefik, a development
  Keycloak with client `coder`, `minikube-expose-services` / `minikube-sync-hosts` (profile-aware versions of the
  ~/devbox functions; own `/etc/hosts` block), `trust-ca`. Traefik keeps the in-cluster IP `coder.<domain>` already
  resolves to, so existing DNS keeps working.
* `x509-support`: CA (with keyUsage, accepted by strict TLS clients), wildcard server certificate, CA bundle,
  client certificates, and the checks above. Adapted from ~/devbox/data/util/x509/pki-toolkit.
* Python images default to `docker.io/library/python` (no longer the reference cluster's Harbor).
* `install.sh`: works with Helm 4 (no `--show-resources`), quieter output.

## 1.3.0 – 2026-10-10

* Default admin `admin` / `admin@admin.com` / password `admin` (`global.firstUser`), created on a fresh database. The
  bootstrap Job sets the password once (Coder's API rejects weak passwords, so it writes the hash directly); later
  syncs never reset it. Change it after the first sign-in.
* Password sign-in on by default (`global.disablePasswordAuth: "false"`): the admin uses its password, everyone else
  Keycloak. `"true"` keeps 1.2.0's Keycloak-only behaviour (admin = the Keycloak account with `firstUser.email`).
* The template-push token is minted on every sync, so pushes no longer depend on the admin's password.
* Banner: the unreleased coder-banner 0.4.3 (optional `gateway.sectionName`, `nodeSelector`), starting blank
  (hidden, no message) until an owner publishes one.
* Development baseline in git: `development/tools/build-bundle.sh`; private material kept outside the repository.

## 1.2.0 – 2026-10-10

Verified end to end in an air gap (DEPLOY.md on a kind cluster with egress blocked; 19/19 sign-in, add-on and
workspace checks).

* Coder image `2.0.0-platform.4-airgap`: the fork's image plus a Terraform provider mirror. Without it every template
  push and workspace build fails offline.
* With password sign-in disabled, the bootstrap Job links the first owner to the Keycloak account with
  `global.firstUser.email` and mints a short-lived token for the template push (Coder blocks every password login).
* `agent-audit.tf` fixed (`%{` escaped) and made optional (`coder-workspace.agentAudit`, off).
* Every `checksum/*` annotation quoted (an all-digit hash was rejected by Argo CD).
* `install.sh` uses server-side apply (the 1 MB default-logo ConfigMap exceeded the annotation limit).
* Python from `docker.io/library/python` instead of the private Harbor.
* Gateway API v1.5.1 CRDs added to `prereqs/`; Argo CD Application uses server-side diff (HTTPRoutes stayed OutOfSync).
* Every image bundled: `images/custom/` (the fork's image, on no public registry) and `images/public/{chart,prereqs,platform}/`,
  pinned by digest in `images.txt`; `mirror-images.sh push/save/verify/list`; a `-slim` tarball without the public images.

## 1.1.0 – 2026-10-09

* `global:` settings (domain, hosts, registry, pull secrets, Gateway, OIDC, first user, secret names) drive every
  component, so the Argo CD Application can set them; templated values in the vendored subcharts ("platform patch").
* Argo CD Application (`argocd/coder-airgapped-application.yaml`) installing the chart from an OCI registry.

## 1.0.0 – 2026-10-09

* First capture of the deployment running on minikube (Argo CD `coder` + `coder-banner`, Helm `coder-workspace`,
  `coder-ui-updates`) as one umbrella chart, render-verified against the live objects.

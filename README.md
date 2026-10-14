# coder-airgapped

A complete, self-contained **Coder** platform for **air-gapped Kubernetes** (EKS, or any cluster, Minikube for
development): a fork of Coder Community Edition with a reworked dashboard, an add-on platform service, an
announcement banner and a full-featured workspace template, packaged as **one Helm chart and one bundle that carries
every container image it needs**. Nothing is downloaded during the install, and nothing at run time reaches outside
your network.

| | Current release: [**v1.34.0**](https://github.com/user187x/coder-fork/releases/latest) |
|---|---|
| Coder server | `ghcr.io/user187x/coder-platform:2.0.0-platform.31-airgap`, upstream Coder `main` at `70bc2e554` (after v2.38.0) + 84 fork commits ([`user187x/coder`](https://github.com/user187x/coder)), with a Terraform provider mirror |
| Workspace image | `ghcr.io/user187x/coder-workspace:1.8.3` (desktop, IDEs, notebooks, Kubernetes tools; nothing fetched at start) |
| Image tools | `ghcr.io/user187x/coder-image-tools:1.0.0` (the platform service, BuildKit helpers, trivy with its databases) |
| Rollback image | `ghcr.io/user187x/coder-upstream:2.38.0-main.70bc2e554-airgap`: pure upstream Coder at the fork's base |
| Bundle | 17 images pinned by digest, ~4 GB (`-slim`: the 4 custom images only, ~2.6 GB) |

**Contents:** [Quick start](#quick-start) · [Architecture](#architecture) · [Features](#features) ·
[Security](#security) · [Testing and verification](#testing-and-verification) · [Build a release](#build-a-release) ·
[Upgrades and rollback](#following-upstream-and-rolling-back) · [Known limits](#known-limits) ·
[Repository layout](#repository-layout)

---

## Quick start

Download the release's parts, verify and join them, then install from inside the air-gapped network:

```bash
sha256sum -c coder-airgapped-1.34.0.parts.sha256           # every part: OK
cat coder-airgapped-1.34.0.tar.gz.part-0* > coder-airgapped-1.34.0.tar.gz
sha256sum -c coder-airgapped-1.34.0.tar.gz.sha256          # the bundle: OK

tar -xzf coder-airgapped-1.34.0.tar.gz && cd coder-airgapped-1.34.0
./make --deploy
```

`./make --deploy` asks only what it cannot work out: Minikube or EKS (or any cluster `kubectl` reaches), your
domain, certificates (new self-signed ones or your own), and on EKS the registry to push the images to, the storage
class, your Keycloak / OIDC provider, and Helm or Argo CD. It then installs everything and prints the URL and the
first sign-in (`admin@admin.com` / `admin`; change the password right away).

Your images in Harbor first? `./make --push-images harbor.example.com/<project>` pushes every image and chart there:
it helps log your docker and helm in to Harbor (and Docker Hub), checks or creates the project, and checks every push.

The step-by-step manual install, Argo CD, and the optional pieces are in
[`helm-chart/DEPLOY.md`](helm-chart/DEPLOY.md); every chart value is in [`helm-chart/README.md`](helm-chart/README.md).
The installing machine needs `docker`, `helm` and `kubectl` (`minikube` for Minikube, `aws` for ECR).

---

## Architecture

```
                         ┌──────────────────── namespace coder ─────────────────────────────────┐
 browser ─ HTTPS ─▶ Traefik ─▶ Coder server (fork) ── CloudNativePG (2 instances, coder-db)     │
  (Gateway API,     │          │  dashboard, API, provisioners (Terraform + provider mirror)    │
   wildcard TLS)    │          └─▶ workspaces (pods): agent, desktop, code-server, File Browser, │
                    │               marimo, terminal; home on its own volume                    │
                    ├─▶ /__coder-ui/  platform service coder-ui-updates (HUD, health, images,  │
                    │                 quotas, scheduler, notes, chat, certificates, ...)        │
                    │                 └─ image builder: BuildKit rootless Jobs, trivy scans,    │
                    │                    its own registry (coder-image-registry)                │
                    ├─▶ /__banner/    announcement + classification banners                    │
                    └─▶ /__icons/     template icons (served by the platform service)          │
                         Keycloak (OIDC, certificate sign-in) · metrics-server (where missing)    │
                         └───────────────────────────────────────────────────────────────────────┘
```

| Part | Where it comes from |
|---|---|
| Coder server and dashboard | the fork, [`user187x/coder`](https://github.com/user187x/coder) ([`development/coder-fork/FORK.md`](development/coder-fork/FORK.md)) |
| Platform service `coder-ui-updates` | [`helm-chart/coder-airgapped/files/ui-updates/`](helm-chart/coder-airgapped/files/ui-updates/) (Python, standard library only) |
| Workspace template **UBUNTU** (`ubuntu-vnc-desktop`) | [`charts/coder-workspace/files/`](helm-chart/coder-airgapped/charts/coder-workspace/files/): `main.tf` and its scripts in `scripts/` |
| Workspace image | [`development/workspace-image/`](development/workspace-image/) |
| The chart, installers, images list | [`helm-chart/`](helm-chart/), the deliverable and source of truth |

The full feature list, with where each piece lives and how it is tested, is
[`docs/FEATURES.md`](docs/FEATURES.md).

---

## Features

### Air-gapped by design

- **One bundle, everything in it:** the chart, its installers (`make`, `install.sh`, `mirror-images.sh`), the
  prerequisites (Gateway API CRDs, Traefik, CloudNativePG) and every image as a `docker save` archive, each pinned by
  digest in `images.txt` and checksummed in `SHA256SUMS`.
- **Guided install** for Minikube, EKS or any cluster, with **Helm or Argo CD** (an Application with server-side
  apply and diff, sync waves and hooks ordered so the first owner and the template always come up).
- **Terraform without the internet:** the Coder image carries a provider mirror, so template pushes and workspace
  builds never reach `registry.terraform.io`.
- **No phoning home:** telemetry, the update check, trials and the premium funnel, STUN servers and the
  GitHub/GitLab default logins are removed from Coder. code-server, Firefox and marimo run with their own telemetry
  and update checks off.
- **Workspaces start offline:** code-server, File Browser, marimo with a data stack, the desktop and every tool are in
  the workspace image.
- **Coder runs without Keycloak:** it starts, and password sign-in works, before Keycloak is set up or while it is
  down. Keycloak sign-in starts working the moment it answers.

### Sign-in and identity

- Keycloak OIDC with **certificate (PKCS#12) sign-in**; the installer's admin signs in with a password.
- **Settings › Certificates:** each user uploads their `.p12`; it is installed in their workspaces as
  `~/.cert/<user>.p12`. Its fingerprints (SHA-256 and MD5) are recorded, and its private key can become their SSH key.
- **Admins' `kubectl` and `k9s` on the cluster** from their workspaces, each signed in as themselves (Keycloak device
  login), with the cluster-admin group kept equal to Coder's owners.
- A sign-in page with the deployment's name as a traced outline, background images and animated backgrounds; its
  title, text effect (traced outline, neon or plain), colour, speed and messages (one for signing in, one for right
  after signing out) are set in Customize.

### Workspaces

- **Workspace page:** animated borders by state with a pulse in the theme's colour; apps and the terminal open only
  once the workspace is ready (a real-progress "Workspace initializing" panel); its apps centred in equal columns
  across the card, as wide as the metrics under them; reorderable, embeddable app panels that pop out to a window and
  back, added from a half-moon "+" tab in the theme's colour.
- **Live metrics in two groups**, Workspace and Cluster, on one line, in the same order: CPU, RAM, Disk and Ø Load (the load average) each. They show
  live bar graphs coloured by magnitude, a gauge and a percentage chip, with a Metrics Interval menu (1 s to 5 min, or
  off), every reading to one decimal place. The workspace's own load average is computed the kernel's way from its
  own threads.
- **Network lights:** RX ↓ (green) and TX ↑ (blue) by the workspace's name, lighting up like a network card's LEDs
  when it receives or sends data. Hovering over them shows a live graph of the last minute, received and sent side
  by side, with the axis scaled from B/s up to GB/s. While Lock Down cuts the network, both are red and still, and
  the graph shows no traffic.
- **Terminal:** tmux with btop, a pop-out window, text size, a custom command, JetBrains Mono Nerd Font.
- **Per-user quotas** and a **global workspace scheduler** (required start/stop schedules, enforced).
- **Home volumes:** backups (S3 or download) and disk growth (in place, or a copy to a bigger volume).
- **Lock Down:** admins jail any workspace, for instance one where an agent runs away. There are four modes:
  - **Network:** only Coder stays reachable;
  - **Sudo Command:** nobody in it can use sudo;
  - **Complete:** both;
  - **Freeze:** Complete, and everything in it paused where it is (its agent too), with a snapshot of its disk.

  It works live on a running workspace: with the first three, its processes keep running, and its terminal and apps
  still open, so admins can inspect what the agent is doing. On a stopped workspace it applies from the next start.
  Every mode comes back with each restart until an admin lifts it.
- Admins' **post-it notes** on workspace pages; workspace icons; an agent Update pill.

### The workspace template and image

- **UBUNTU** (`ubuntu-vnc-desktop`, with the Ubuntu icon):
  - an XFCE desktop in the browser (TigerVNC + noVNC);
  - Firefox (Mozilla's build, with offline policies);
  - code-server, File Browser and marimo (Python notebooks, with in-notebook tkinter windows);
  - tmux, btop, bat, eza, fzf, jq, yq and Vim;
  - k9s, kubectl and kubelogin;
  - the JetBrains Mono Nerd Font.
- **The template's scripts are files** (`files/scripts/`, formatted with `shfmt -i 1`): the startup script, the
  certificate install, the agent activity monitor, and every metric as a function of one `metadata.sh`.
- **The image documents itself:** each build records what it adds over its base in
  `/usr/share/coder-workspace/addons.json`: every program with the version it reports, the apt packages asked for,
  and marimo's Python packages.

### Templates and the image builder

- **Template Overview** with its resources, Details (Coder's insights) and README; Source Code with versions (squash
  ranges, Promote); Export as ZIP with everything the template is made of: its Terraform, scripts and icons.
- **Templates › Images, an image builder in the cluster** (BuildKit rootless). You can add:
  - apt packages (checked as you type) and uploaded `.deb` files;
  - installation scripts and removals;
  - hardening options;
  - an XFCE, GNOME or no desktop;
  - dotfiles.

  Each build has a review step with its Dockerfile and shows its progress; built images can be downloaded and backed
  up to S3.
- **Every image is scanned offline**, built ones and the workspace image the template runs (listed first, read-only,
  "In use by workspaces"). See [Image security](#image-security).

### Administration and monitoring

- **HUD:** a live, sortable platform overview with graphs (users, workspaces, CPU, memory, GPU, top users,
  failures) and the Coder server's **System Uptime**.
- **Health, monitoring and security posture** views; network and persistence maps.
- **Banners:** classification banners (e.g. UNCLASSIFIED), and an announcement banner with:
  - effects: typewriter, crawl and fonts;
  - a "Viewed" receipt with the user's certificate fingerprint;
  - a terminal-style full view (with an easter egg or two).
- **Inbox:** Clear all (asks twice) beside Mark all as read.
- **Chat** between users and admins (with screenshots), and the admins' chat console.
- Admin quick links, an arrangeable top bar, logo and avatars (uploaded, gallery, defaults).
- **Agent Activity** (optional): every command run in a workspace, for review.
- metrics-server installed where the cluster has none.

---

## Security

### Air gap and supply chain

- **No outbound traffic:** the dashboard is crawled in a browser to check that no request leaves the deployment's
  domain, and the end-to-end test installs and runs on a cluster whose internet egress is blocked.
- **Every image pinned by digest** (`images.txt`) and every archive checksummed (`SHA256SUMS`); no floating tags
  (enforced by the static checks); release parts and bundles carry SHA-256 files.
- **Downloads at build time are verified:**
  - code-server, File Browser and the `shell-quote` replacement by their published SHA-256 / SHA-512;
  - Firefox through Mozilla's signing key, checked by fingerprint.
- **Terraform providers** come from the image's own mirror (`TF_CLI_CONFIG_FILE` allows nothing else).

### Identity and access

- **The first owner is always the installer's:** OIDC sign-ins are refused while Coder has no user
  (`CODER_PLATFORM_FIRST_OWNER=bootstrap`), so the first person through Keycloak cannot become owner.
- **Keycloak** with client certificates (PKCS#12).
- Coder's own protections are kept: sign-in rate limits, and RBAC with owner, template-admin and member roles.
- **Admins' cluster access** is personal and short-lived:
  - each admin signs in to Kubernetes as themselves (`keycloak:<user>`), through Keycloak's device login;
  - the tokens live in `/tmp` and last only as long as the Keycloak session;
  - no long-lived credential sits in any workspace;
  - someone who stops being an owner loses the kubeconfig at the next start.
- **The platform service checks every caller:**
  - it validates the Coder session against Coder itself;
  - it enforces each view's role (admins, template admins, the owner);
  - it refuses cross-site requests: changes need its `X-Requested-With` header and a JSON body, which a
    form on another site cannot send.

### Secrets and certificates

- **A certificate's password is never sent or stored:** it is checked in the browser only.
- **Certificates reach only their owner's workspaces:** stored as the user's own Coder secret, written by Coder's
  agent to the owner's workspaces only, installed as `~/.cert/<user>.p12` with mode `0600` in a `0700` directory.
  A certificate deleted in Coder is removed from `~/.cert` at the next start.
- **TLS end to end:** a local PKI (`x509-support`: CA, wildcard server certificate, client certificates); workspaces
  trust the cluster CA alongside the public roots.
- **Installer-generated passwords** stay out of Helm values and the release record (Secrets, ignored by Argo CD's
  diff).

### Workspace isolation

- Workspace pods run as a **non-root user** (uid 1000, `runAsNonRoot`), only in the `coder` namespace (the template
  refuses others), with **core dumps disabled** for everything the agent starts.
- **Nothing in a workspace listens on its network:**
  - the desktop's VNC server listens only on a Unix socket (directory `0700`, socket `0600`), with no TCP port;
  - every app (code-server, File Browser, marimo, websockify) and the agent's own ports listen on `127.0.0.1`.
  - The only way in is Coder's authenticated app proxy, with every app shared with its owner only.
- **Home volumes are protected:** their storage class can never change after creation, and they only ever grow, so
  a template change cannot replace (empty) anyone's home.

### Lock Down (containing a workspace)

- **Network:** firewall rules in the workspace pod's own network namespace refuse everything in or out except
  loopback, DNS and Coder; connections already open stop at once, and IPv6 is blocked entirely. The agent's
  connection to Coder is kept, so admins can still reach the workspace.
- **Sudo Command:** a last sudoers rule denies every command to everyone. A process that is root already stays root,
  and nothing else in the workspace can become root.
- **It can't be undone from inside the workspace:**
  - the workspace container has no `NET_ADMIN`, so not even root there can change its firewall;
  - the setting lives outside the workspace, in a ConfigMap only the platform service writes.
- **It applies live:**
  - a running workspace is locked by a short-lived helper container, with no restart and nothing killed, so the
    evidence stays in place;
  - a stopped one is locked at start, before anything runs;
  - a watcher keeps every running workspace as the setting says.
- **Freeze:** every process of the workspace container is paused (SIGSTOP), Coder's agent too, so no session opens in
  it; its PID 1 only waits for its children. Its home volume is snapshotted while everything is paused: a
  VolumeSnapshot where the cluster supports it (EKS with the EBS CSI driver and a VolumeSnapshotClass), else a copy in
  a new volume made on the workspace's node. The processes that were running are recorded with it. Lifting it resumes
  exactly the paused processes. The snapshot is kept until an admin deletes it or freezes the workspace again.
- **It survives restarts:** Network and Sudo are set at start before anything runs, Freeze within seconds of the
  start, until an admin lifts the lock.
- **It's visible:** everyone who sees a locked workspace, its owner included, gets a notice of what is locked, by
  whom and since when; its network lights turn red. Only admins can set or lift it, and every change is logged.

### Image security

- **Every image is scanned offline** by trivy (vulnerabilities and secrets) plus the image's own checks:
  - a non-root default user;
  - no account without a password, and root locked;
  - no passwordless sudo;
  - few setuid programs, and no world-writable paths;
  - no baked SSH host keys, private keys or shell history;
  - whether compilers are present.
- **Each scan gets a grade** (A to F) and a **FIPS 140 readiness** check: a validated OpenSSL module, OpenSSL's
  default provider, the TLS floor, SSH ciphers and other crypto libraries.
- **Hardening in the builder:**
  - strip docs and caches;
  - no sudo for the workspace user, and root locked;
  - no setuid bits, and no compilers;
  - umask 027 and no core dumps;
  - hardened SSH;
  - FIPS-aligned crypto settings.
- **Security data stays current in an air gap:** trivy's vulnerability and Java databases and the FIPS rules are
  loaded from a file made outside (`build-security-data.sh`). Each report records which data graded it, and every
  image can be rescanned.
- **The workspace image is tracked and scanned too**, and its findings are acted on. CVE-2026-102422 (critical,
  `shell-quote` in code-server) is fixed in workspace image 1.8.3, so its scan shows no critical finding.

### Data protection and rollback

- The database, the add-on volume and the generated Secrets carry `Prune=false,Delete=false`: deleting the Argo CD
  Application never deletes data.
- **The fork adds no database migration**, so a rollback to pure upstream Coder (the rollback image in the bundle)
  reads the same data, and running workspaces keep working
  ([`development/coder-fork/ROLLBACK.md`](development/coder-fork/ROLLBACK.md)).
- PostgreSQL runs as two CloudNativePG instances.

### Audit and policy aids

- **Agent Activity** (optional `agent-audit.tf`): every program run in a workspace, with its arguments, working
  directory and parent, recorded and sent off the workspace at once. The workspace's agent token authenticates it,
  so records cannot be forged for another workspace.
- **Classification banners**, announcement receipts (who saw and who confirmed each announcement), quotas and
  enforced schedules.

---

## Testing and verification

Every capability is tested at the level it lives at, and the suite says whether Coder's own features still work
next to the fork's. [`development/tools/verify/`](development/tools/verify/) runs the suites and writes a report;
[`development/tools/airgap-e2e/`](development/tools/airgap-e2e/) is the air-gapped install test.

| Layer | What it proves | How |
|---|---|---|
| **Static** (`chart_checks.py`, 31 checks) | The chart renders for Minikube, EKS, a private registry, Keycloak-only and admins' kubectl, plus: every image pinned and archived; no floating tags; Keycloak's Secrets optional; nothing at workspace start needs the internet; the first owner is the installer's; Argo CD hook order; the template's scripts shipped, `shfmt -i 1` formatted and `bash -n` clean | `./run.sh static` |
| **Unit: fork** | `go test` on every package the fork changed (or upstream's whole suite), and the dashboard's vitest suite (upstream's ~4,700 tests and the fork's). New fork code comes with tests, e.g. OIDC fallback, first-owner guard, MD5, uptime, network lights, Inbox, Images | `./run.sh unit` |
| **Unit: platform service** (`platform_unit_test.py`, 16 tests) | The session cache, admin sync, image builder offline refusals and base seeding, the tracked workspace image, add-ons parsing, Coder uptime | `python3 -m unittest` |
| **Image build checks** | Every workspace image build asserts its tools, desktop, fonts and shell setup. With **no network**, it then runs Firefox, starts code-server and File Browser, and runs every cell of a marimo notebook | `build-workspace-image.sh` |
| **Air-gapped end to end** | DEPLOY.md followed from a fresh bundle on a kind cluster with **all internet egress blocked**, every image loaded from the bundle. The steps: sign-in and workspace checks (8), the image builder offline (9), Keycloak down and its Secrets missing (10) | `airgap-e2e/e2e.sh`, `step8–10.sh` |
| **Live conformance** (`live.py`) | On a real deployment with a throwaway user and workspace. **Upstream:** users, tokens, organizations, templates (offline `terraform init`), the workspace lifecycle, agent, apps through Coder's proxy, the web terminal, ports, logs, insights, health, provisioners, notifications. **Fork:** every platform service view and its access rules | `./run.sh live` |
| **Browser** (`ui.mjs`) | Every dashboard page loads; **no request leaves the domain**; no page throws; key pages render correctly. A memory soak (workspace page, HUD) requires the JS heap and the DOM to level off | `./run.sh ui` |
| **Rollback** | The deployment's database copied; pure upstream Coder and the fork run on it and see the same; the schema unchanged; a live switch to upstream and back | `./run.sh rollback`, `rollback-live.sh` |
| **Load** (`load.py`) | Hundreds of developers' and admins' dashboards polling at once, with latency percentiles and error rate | `./run.sh load` |

**Latest results** (full report: [`docs/READINESS.md`](docs/READINESS.md)):

| Check | Result |
|---|---|
| Static checks | **34/34** (1.34.0) |
| Platform service unit tests | **30/30** (1.34.0) |
| Air-gapped install, egress blocked | Argo CD Synced / Healthy in 297 s; step 8 **39/39**; image builder offline **12/12**; Keycloak down **3/3** (1.28.0) |
| Live conformance | **76/76** (1.28.0) |
| Load, ~1,050 dashboard tabs for 8 minutes | **0 errors** of 157,626 requests, worst p95 0.28 s, no restarts (1.28.0) |
| Browser memory soak, 8 minutes | JS heap +0.3 MB; no external requests (1.28.0) |
| Rollback to upstream and back | **7/7** on a database copy, **4/4** live (1.28.0) |
| Lock Down, live (Network, Sudo, Complete, Freeze, restarts) | **35/35** (1.34.0) |
| Workspace image 1.8.3 scan | **0 critical** (1.31.0) |

```bash
cd development/tools/verify
./run.sh static unit                                   # before every commit to the fork or the chart
./run.sh live ui rollback load --url https://coder.example.com --domain example.com \
    --context my-cluster --workspace /@admin/my-ws --soak-minutes 10 --load "--developers 100 --admins 5 --speed 10"
```

---

## Build a release

```bash
development/tools/build-workspace-image.sh             # the workspace image (offline checks, saved into the bundle)
development/tools/build-airgap-image.sh 2.0.0-platform.31   # the fork's Coder image + Terraform provider mirror
development/tools/build-bundle.sh                      # -> dist/coder-airgapped-<version>.tar.gz and -slim.tar.gz
```

Image archives are not in git: `images.txt` pins every image by digest, and `build-bundle.sh` fetches the missing
public ones (`mirror-images.sh save`). The Coder server is built from the fork (`scripts/build_platform_image.sh`
there; see [`development/coder-fork/FORK.md`](development/coder-fork/FORK.md)). Releases are published on GitHub in
parts of under 2 GiB, each with SHA-256 files. `main` is always release-ready in both repositories.

## Following upstream and rolling back

- [`development/coder-fork/UPSTREAM.md`](development/coder-fork/UPSTREAM.md): how the fork follows each upstream
  Coder release (merge rules, conflicts, the test gate), with no breaking changes to what the fork adds.
- [`development/coder-fork/ROLLBACK.md`](development/coder-fork/ROLLBACK.md): how to switch a deployment to pure
  upstream Coder and back, without losing or corrupting workspaces.

## Known limits

- **Apt packages in image builds need an apt mirror** (Images › Settings). Everything else in a build works
  offline, and builds that would need a download are refused up front, with the reason.
- **Security data ages** with the bundle: load newer data with `development/tools/build-security-data.sh`.
- **Admins' kubectl on EKS** needs a Keycloak that EKS's control plane can reach (otherwise an in-cluster OIDC proxy).
- **Not yet run on a real EKS cluster:** EKS is tested by renders, and the install by the air-gapped kind test.
- **Images are linux/amd64 only.**
- **The workspace image grades D:** no critical findings, but 64 high ones remain in bundled third-party software,
  as its report in Templates › Images shows.

## Repository layout

| Path | What |
|---|---|
| [`helm-chart/`](helm-chart/) | **The deliverable:** the chart, `DEPLOY.md`, the chart reference `README.md`, the Argo CD Application, `make`, `install.sh`, `mirror-images.sh`, `images.txt`, prerequisites |
| [`docs/`](docs/) | [`FEATURES.md`](docs/FEATURES.md) (everything the fork adds over Community Edition) and [`READINESS.md`](docs/READINESS.md) (the release-readiness report) |
| [`development/`](development/) | Building and testing: the workspace image, image tools, the air-gap image, `tools/` (builds, `verify/`, `airgap-e2e/`), the fork's notes (`coder-fork/`), a reference capture of the original deployment |
| [`CHANGELOG.md`](CHANGELOG.md) | What changed in each version |
| `dist/` | Built bundles (git-ignored) |

# What coder-fork adds to Coder Community Edition

The base is upstream Coder `main` at `70bc2e554` (2026-10-03, after v2.38.0), with 72 commits on top in
[`user187x/coder`](https://github.com/user187x/coder). The database schema is upstream's, with no migration added.
Everything below is on top of what Coder Community Edition does. Coder's own features (workspaces, templates,
provisioners, the agent, apps, SSH, port forwarding, audit, insights) are all kept.

**Where it lives:**
- **fork:** the Coder server and dashboard (`user187x/coder`);
- **service:** the platform service `coder-ui-updates` (`helm-chart/coder-airgapped/files/ui-updates/`), served at
  `/__coder-ui/` and `/__banner/`;
- **chart:** the Helm chart and its installers (`helm-chart/`);
- **image:** the workspace image (`development/workspace-image/`) and template
  (`helm-chart/coder-airgapped/charts/coder-workspace/files/main.tf`, its scripts in `files/scripts/`).

**Test:** where the feature is checked. `vitest` and `go test` are the fork's unit tests; `step8`/`step9` are the
air-gapped end-to-end checks (`development/tools/airgap-e2e/`); `verify` is the live suite
(`development/tools/verify/`).

## 1. Air gap and packaging

| Feature | Where | Test |
|---|---|---|
| One self-contained bundle: chart, installers and every image as `docker save` archives, pinned by digest (`images.txt`, `SHA256SUMS`) | chart | e2e step 1–3 |
| `./make --deploy`: one guided install for Minikube, EKS or any cluster (domain, certificates, registry, storage class, Keycloak, Helm or Argo CD) | chart | e2e (Argo CD path) |
| `install.sh` (Secrets, ConfigMaps, CloudNativePG) and `mirror-images.sh` (verify, push, save, chart, `seed-builder`) | chart | e2e |
| Argo CD Application with values in the Application (server-side apply and diff) | chart | e2e step 6–7 |
| Coder image with a Terraform provider mirror (`-airgap`): template pushes and builds never reach `registry.terraform.io` | chart | e2e step 8 |
| No telemetry, update check, trial or premium funnel, STUN servers or GitHub/GitLab default login: Coder contacts nothing outside the network | fork | `go test ./cli` (OIDC), egress crawl |
| Coder starts and password sign-in works before Keycloak is set up or while it is down (Keycloak's standard endpoints; other OIDC providers: password-only, then a restart once they answer). Keycloak's Secrets optional | fork, chart | `go test ./cli -run OIDC`, verify |
| Workspace image with everything preinstalled: desktop (XFCE, noVNC), Firefox, code-server, File Browser, marimo with a data stack, tmux, btop, k9s, kubectl, kubelogin, JetBrains Mono Nerd Font | image | image build checks (offline), e2e step 8 |
| Workspace template UBUNTU (`ubuntu-vnc-desktop`, Ubuntu icon): its scripts as files (`files/scripts/`, `shfmt -i 1`; the metrics as functions of `metadata.sh`), read with `file()` / `templatefile()`; a workspace load average beside the node's | chart | chart checks, step8 |
| Pure upstream Coder at the fork's base, as a rollback image in the bundle (`coder-upstream:2.38.0-main.70bc2e554-airgap`) | chart | verify rollback |
| Prerequisites in the bundle: Gateway API CRDs, Traefik, CloudNativePG; Argo CD and Keycloak images for reference | chart | e2e |

## 2. Sign-in and identity

| Feature | Where | Test |
|---|---|---|
| Keycloak OIDC with certificate (PKCS#12) sign-in; first owner bootstrap with a password | chart, service | e2e step 8 |
| Sign-in page: the deployment's name as a traced outline (ContourTitle), background images and animated or HTML backgrounds | fork | vitest, verify UI |
| Certificates: each user's PKCS#12 in Settings, installed in their workspaces (`~/.cert/<user>.p12`), with the x509 fingerprint | fork, service, image | verify |
| SSH key from the user's .p12 private key | fork | `go test ./coderd -run GitSSHKey` |
| Admins' kubectl and k9s on the cluster from their workspaces, each signed in as themselves (Keycloak device login, `keycloak:<user>`), the group synced with Coder's owners (password owners too) | chart, service, image | verify (RBAC), manual sign-in |
| Keycloak status and settings page | fork, service | vitest |

## 3. Workspaces

| Feature | Where | Test |
|---|---|---|
| Workspace page: animated agent-card borders by state, with a pulse in the theme's colour once ready | fork | vitest, verify UI |
| Apps and terminal open only once the workspace is ready, after the apps' health checks; "Workspace initializing" panel with real progress | fork | vitest, e2e step 8 |
| Reorderable drop-down panels (Logs, Terminal, any app embedded); app panels pop out to a window and back; a "+" tab adds an app panel | fork | vitest |
| Terminal: tmux with btop, pop-out window, text size, custom command, JetBrains Mono Nerd Font | fork, image | vitest |
| Workspace metrics in two groups (Workspace, Cluster) with live bar graphs coloured by magnitude, a gauge of the current reading, the percentage in a chip; Metrics Interval menu (1 s – 5 min, off); memory and disks in GB | fork, image | vitest, verify UI |
| Agent Update pill (another build, a newer template, an older agent) | fork | vitest |
| Admins' post-it notes on workspace pages and in the users table | fork, service | vitest |
| Workspace icons; home backups (S3 or download); disk growth (in place, or a copy to a bigger volume) | fork, service | verify |
| Global Workspace Scheduler: required start/stop schedules, enforced | fork, service | e2e step 8 |
| Per-user quota | fork, service | e2e step 8 |
| "Animations" switch; no Favorite; no links to Coder's online docs | fork | vitest |

## 4. Templates and images

| Feature | Where | Test |
|---|---|---|
| Template Overview: resources, Details (Coder's insights; number parameters shown without quotes) and the README; Source Code with versions (squash ranges, Promote) | fork | vitest |
| Share menu (Embed) in place of the copy buttons | fork | vitest |
| Templates › Images: an image builder in the cluster (BuildKit rootless): base, apt packages (checked as you type), uploaded .deb files, installations, removals, hardening, XFCE/GNOME/no desktop, dotfiles; review and Dockerfile; builds with progress | fork, service | step 9 |
| Every build scanned offline: trivy (vulnerabilities, secrets), users/sudo/setuid checks, FIPS readiness; reports say what graded them | service | step 9 |
| Security data kept current in an air gap: load trivy's databases and FIPS rules from a file made outside (`build-security-data.sh`), dated, rescans | service | verify |
| Built images downloadable, backed up to S3 | service | verify |
| A template renamed in the dashboard keeps getting the chart's updates | chart | verify |

## 5. Administration and monitoring

| Feature | Where | Test |
|---|---|---|
| HUD: live, sortable platform overview with graphs (users, workspaces, CPU, memory, GPU, top users, failures) | fork, service | e2e step 8 |
| Health view, monitoring and security posture (zero-trust checks, runtime signals) | fork, service | e2e step 8 |
| Network and persistence maps | fork, service | vitest |
| Classification banners (e.g. UNCLASSIFIED), announcement banner (typewriter, crawl, fonts, Viewed box; a terminal view with an easter egg) | fork, service | verify |
| Chat between users and admins, with screenshots, and the admins' chat console | fork, service | vitest |
| Admin menu quick links, breadcrumb, arrangeable top bar, Resources menu | fork | vitest |
| Logo and avatar (uploaded, a gallery, defaults) | fork, service | verify |
| Agent Activity (optional `agent-audit.tf`): every command run in a workspace | chart, service | manual |
| metrics-server where the cluster has none | chart | verify |

## 6. Removed from upstream

GitHub OAuth (default provider and device flow), GitLab login, telemetry (server, CLI, network), the update check,
trial licences and the premium funnel, and STUN defaults. Their upstream tests are removed with them. None of these
removals touch the database.

# Release readiness: coder-fork 1.27.2 → 1.28.0

What was checked, against the version on GitHub (release **v1.27.2**, published 2026-10-13 with its bundle), what was
found, what 1.28.0 (branch `release-readiness`) fixes, and what is left open.

**Method:**
1. The 1.27.2 bundle was downloaded from the GitHub release into an empty directory. Its checksums matched, and its
   contents equal the `v1.27.2` tag (`diff -r` of a fresh clone: identical).
2. Every check below ran on that bundle as the baseline, then on the 1.28.0 candidate built from the branch.
3. Tests ran on an air-gapped kind cluster (egress blocked: every image from the bundle), on the development
   Minikube, and on copies of its database.

RESULTS-SUMMARY

## 1. A self-contained deliverable for an air gap

| Finding (1.27.2) | Impact in an air gap | 1.28.0 |
|---|---|---|
| The repository alone cannot install: the three custom images (the fork's Coder, the workspace image, image tools) are on no registry, only in the release bundle | none if the release bundle is used; the latest release was 1.24.1 until 1.27.2 was published during this check | 1.27.2 published (2 parts + checksums); every release must publish its bundle |
| code-server and File Browser downloaded at every workspace start (`code-server.dev`, `raw.githubusercontent.com`) | two of the workspace's apps never start, and the workspace never becomes "ready" (its readiness waits for the apps' health checks) | both preinstalled in workspace image 1.8.0, pinned by SHA-256, checked offline at image build |
| The push Job's CLI was `ghcr.io/coder/coder:latest` (floating; v2.38.0 against the fork's server) | a re-mirrored `latest` changes the CLI under you | Coder's own image (same binary as the server); the floating image dropped (244 MB smaller) |
| Last air-gapped end-to-end run: 1.5.0, 22 releases earlier; step 8 only printed app health | regressions like the one above went unnoticed | harness scripted (`setup.sh`), step 8 asserts app health, steps 9–10 added; run on 1.27.2 and 1.28.0 (below) |

Checked and fine in 1.27.2:
- every image the chart runs is pinned by digest and bundled;
- telemetry, update checks, STUN, trial and GitHub login are removed or off;
- the dashboard makes no request outside the domain (30 pages crawled, 0 external requests);
- the Terraform providers are mirrored in the Coder image.

E2E-RESULTS

## 2. Coder without Keycloak, or before it is set up

| Finding (1.27.2) | Impact | 1.28.0 |
|---|---|---|
| Upstream Coder fetches the OIDC issuer's discovery document at startup and **exits** if it can't. The chart always configures an issuer | while Keycloak is not provisioned or is down, Coder crash-loops: **nobody can sign in, owners with passwords included** | a Keycloak issuer gets its standard endpoints without discovery: Coder starts, password sign-in works, Keycloak sign-in works once it answers. Other OIDC providers: password-only, then a clean restart when they answer. Tested: unit tests, a local server against an unreachable issuer, step 10 on kind |
| The Coder pod needs Keycloak's two Secrets to exist | the pod cannot even start before Keycloak is set up | both optional |
| `disablePasswordAuth: "true"` blocks every password, owners included | with Keycloak down, nobody signs in | documented: keep `"false"` until a Keycloak sign-in has worked (DEPLOY.md, Step 5) |

## 3. Minikube features on EKS

EKS itself was not available for this check. Instead:
- the kind cluster stands in for a non-Minikube cluster: containerd instead of Docker, no node access, a separate
  registry, egress blocked;
- the chart was rendered and checked with the EKS example values.

| Feature | On EKS | Status |
|---|---|---|
| Workspaces' home volumes | no StorageClass was set; EKS clusters may have no default class: volumes Pending, workspaces never start | **fixed**: `coder-workspace.storageClass`, set by `./make --deploy` and the example files; existing homes never replaced |
| Image builder volumes | same | **fixed** (`imageBuilder.registry/files.storageClass`) |
| Image builder base image | BuildKit pulled it from `global.registry`, which needs credentials: ECR with node roles gives a pod none | **fixed**: the builder's own registry is seeded (`make --deploy`, `mirror-images.sh seed-builder`); fallback kept |
| Built images on nodes (`localhost:5000` through the node proxy) | containerd, as on EKS | tested on kind (step 9) |
| BuildKit rootless | needs unprivileged user namespaces and unconfined seccomp/AppArmor on the build pods | works on kind; **check on your nodes**: `sysctl user.max_user_namespaces` > 0 (Bottlerocket disables them), and no Pod Security "restricted" on the `coder` namespace |
| metrics-server | the chart installs its own only when the cluster has none | fine (EKS add-on or the bundled one) |
| Admins' kubectl with Keycloak | EKS checks tokens from its control plane, so the issuer must be reachable from AWS's network | **open**: with a Keycloak only on the private network, it cannot work on EKS. Options: make the issuer reachable for EKS, or run an in-cluster OIDC proxy (Pinniped, kube-oidc-proxy; images to add to the bundle) |
| Load balancer, ECR, S3 backups | AWS APIs in an air gap need VPC endpoints (`ecr.api`, `ecr.dkr`, S3 gateway, `elasticloadbalancing`, `sts`) | documentation item |
| Node architecture | every image is amd64 | no Graviton |

## 4. The image builder without internet

IMAGEBUILDER-RESULTS

| Finding (1.27.2) | 1.28.0 |
|---|---|
| A GNOME build (or one with apt packages) with no apt mirror failed halfway through | refused before it starts, saying why and what to do |
| On EKS the base image needed registry credentials | builder's own registry seeded (above) |

Works offline with no mirror: uploaded `.deb` files, installation scripts, dotfiles and shell configuration,
hardening, FIPS-aligned settings, XFCE or no desktop, the scan (trivy with the bundled database, image checks, FIPS
readiness), and S3 backups to an S3 endpoint in the network. Needs a mirror: apt packages and GNOME. Ages: the
vulnerability database; load a newer one with `build-security-data.sh`.

## 5. Under heavy load

| Finding (1.27.2) | Impact | 1.28.0 |
|---|---|---|
| Every dashboard page waits for `/__coder-ui/boot.js` from the platform service (a render-blocking script) | an overloaded or hung platform service freezes every page for everyone | Coder's proxy gives it 3 s, then serves an empty script: the dashboard starts with its defaults |
| The platform service's session cache was cleared whole past 2,000 entries | above ~2,000 sessions every request goes back to Coder's API | least-recently-used, 20,000 entries |
| Quota and schedule checks on workspace create/start ask the platform service | they fail open (create allowed), after up to 10 s if it hangs | documented |

LOAD-RESULTS

## 6. Rollback to upstream Coder

ROLLBACK-RESULTS

## 7. Test suite

`development/tools/verify/` (README there), plus the air-gapped e2e (`development/tools/airgap-e2e/`).

UNIT-RESULTS

## 8. Features over Community Edition

[`FEATURES.md`](FEATURES.md).

## 9. Following upstream

[`development/coder-fork/UPSTREAM.md`](../development/coder-fork/UPSTREAM.md).
- A trial merge of 10 days of upstream (189 commits, migrations 612–618) conflicts in 14 files.
- The process: follow release tags, merge rather than rebase, then pass the verification gate (the full upstream Go
  suite, vitest, the e2e, and the live, UI, rollback and load suites) with no regression against the previous
  release's report.

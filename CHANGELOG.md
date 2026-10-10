# Changelog

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

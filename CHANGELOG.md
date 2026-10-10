# Changelog

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

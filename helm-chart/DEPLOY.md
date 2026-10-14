# How to deploy Coder (air-gapped)

## The quick way: `./make --deploy`

```bash
tar -xzf coder-airgapped-1.38.0.tar.gz && cd coder-airgapped-1.38.0
./make --deploy
```

It asks only what it cannot work out, and offers a default you accept with Enter:

1. **Minikube** (this machine, for development) or **EKS** (or any cluster `kubectl` points at)
2. **Your domain** – Coder becomes `https://coder.<domain>`, workspace apps `*.<domain>` (worked out for you)
3. **Certificates** – create new self-signed ones (a private CA you trust once), or point it at your own
   certificate, key and CA bundle (PEM or DER; an encrypted key is decrypted after asking for its passphrase).
   They are checked before anything is installed.
4. Minikube: whether to deploy a development Keycloak (default) · EKS: registry, image push, storage class, your
   Keycloak's issuer / client secret / CA, Helm or Argo CD

Then it does the rest: Gateway API CRDs, Traefik, CloudNativePG, Secrets, the chart, and on Minikube the
`local-path` storage and a Keycloak. At the end it prints the URL and the sign-in (`admin@admin.com` / `admin`).
Re-running it is safe; it remembers your answers.

**Minikube and sudo:** reaching the cluster from your machine needs root for up to three things – the
`minikube tunnel`, `/etc/hosts` entries, and trusting the CA. Right after you name the profile, the script lists the
ones still needed (it skips a running tunnel, names your DNS already resolves, and a CA you already trust) and asks
once. Say yes and sudo asks for your password then; the steps run at the end of the install with no further
questions. Say no and it prints the exact commands instead.

Progress shows in the terminal and in `.deploy/deploy.log` (from a second terminal: `tail -F .deploy/deploy.log`).

## Updating with the layer bundle

A cluster installed from a full bundle can take the next release as its **layer bundle**,
`coder-airgapped-<version>-layer.tar.gz`, which carries only the customization layer: this chart, the installers,
and the images changed since the previous release (the base, named in its `LAYER.json`). Images of ours come thin,
just the layers their previous version lacks; Traefik, CloudNativePG, the Gateway API CRDs, Keycloak and the public
images are not in it at all.

```bash
sha256sum -c coder-airgapped-1.38.0-layer.tar.gz.sha256
tar -xzf coder-airgapped-1.38.0-layer.tar.gz && cd coder-airgapped-1.38.0
./layer-support info                                   # what it carries, and what the cluster must have
./make --deploy                                        # asks for the folder you installed from
```

`./make --deploy` then:

1. takes your settings (certificates, answers, the client secret) from the previous install's `.deploy` folder: it
   asks for that folder once (`PREVIOUS=<folder>` with `--yes`);
2. checks, before changing anything, that the cluster has what the layer does not carry: Coder installed (Helm
   release `coder`), the Traefik and CloudNativePG charts at this release's versions (or your own Traefik /
   CloudNativePG, left as they are), the Gateway API CRDs, Minikube's Keycloak at this release's version, and every
   image not carried, on every Minikube node or in your registry (`docker login` it first). Anything missing is
   listed and the deploy stops: use the full bundle then;
3. makes each thin image whole again in this machine's docker from its base image (from the local docker, a
   Minikube node, or your registry), checking every layer by its digest, and loads or pushes only the images the
   layer carries;
4. installs the chart as usual; Traefik's and CloudNativePG's charts are left as they are (unless the layer carries
   a newer one).

A layer is made for clusters at its base release. Installed on another release, the same checks decide: it works
when everything it needs is there, and otherwise says what is missing.

`./make --deploy --yes` takes every remembered answer without asking. Helpers it uses, which also work on their own:
`./x509-support` (certificates), `./minikube-support` (e.g. `./minikube-support expose coder <domain>` after a
reboot), `./install.sh`, `./mirror-images.sh`.

### Your Harbor first: `./make --push-images`

```bash
./make --push-images harbor.example.com/coder      # <harbor host>[/<project>]
```

Takes every image the solution needs (from the bundle) and its Helm charts into your Harbor, helping along the way:

1. it reaches Harbor's API; a private CA: give the CA file, and it sets up helm (`--ca-file`) and docker
   (`/etc/docker/certs.d/<host>/ca.crt`, with sudo, asked first) to trust it;
2. it logs your **docker and helm** in to Harbor (a user, or a robot account with push rights, `robot$<name>`), and
   on request to **Docker Hub** too (the public images the bundle does not carry are pulled from there, and Docker
   Hub limits anonymous pulls). Passwords and tokens go to docker and helm on stdin, never on a command line, and the
   script never writes them anywhere;
3. it checks the project, and offers to create it (public: nothing in the cluster needs a pull secret; private: the
   cluster needs one, and the nodes credentials for the workspace pods);
4. it pushes the images (as `<harbor>/<project>/<path>`, what `global.registry: <harbor>/<project>` expects) and the
   charts (`coder-airgapped`, CloudNativePG's and Traefik's) to `oci://<harbor>/<project>/charts`;
5. it checks every image and chart is there, and remembers `<harbor>/<project>` as the registry for
   `./make --deploy` (EKS).

Non-interactive: `HARBOR_USER=robot\$coder HARBOR_PASSWORD=... DOCKERHUB_LOGIN=n ./make --push-images
harbor.example.com/coder --yes` (`PUSH_GROUPS="custom chart prereqs platform"` adds the reference Argo CD and
Keycloak images). Any other registry works too: without Harbor's API, the project steps are skipped.

## Argo CD: Coder decorated by the customization layer

The bundle also installs as **two charts in one Argo CD Application**, the customization layer decorating a plain
Coder that it can be taken off again:

| | Chart (`argocd/charts/`) | What it installs |
|---|---|---|
| 1 | `coder-airgapped-base-<version>.tgz` | The Coder installation: the upstream Coder chart running **upstream Coder** (at the fork's base, with a Terraform provider mirror), its CloudNativePG database and its first owner |
| 2 | `coder-airgapped-layer-<version>.tgz` | The customization layer: the platform service, the announcement banner, the ubuntu-vnc-desktop template and its icons, the image builder, metrics-server, admins' kubectl |
| 3 | `argocd/coder-decorated-application.yaml` | The Application with both (`argocd/coder-plain-application.yaml`: the base alone) |
| 4 | `./make --argocd` | Pushes everything to Harbor and deploys it with Argo CD (below) |

The layer's one hook into the base is `global.decorator.layer: true` in the Application's shared `global` block:
with it, the base runs the fork's Coder image with the platform's settings; without it, upstream Coder. Both charts
are packaged from `coder-airgapped/` (`./mirror-images.sh decorator-charts`), so together they are exactly that chart.

```bash
./make --argocd harbor.example.com/coder-airgapped      # <harbor host>[/<project>]; kubectl's context: the cluster with Argo CD
```

1. **Harbor:** as `./make --push-images` (above): logins, project, every image and chart (both of these, the
   prerequisites') to `oci://<harbor>/<project>/charts`, all checked;
2. **the cluster:** the Gateway API CRDs (v1.6.0, from `prereqs/`), a Gateway `traefik/traefik-gateway` (Traefik
   from `prereqs/`) and the CloudNativePG operator, each installed only when missing, after asking;
3. **the domain, certificates, OIDC provider and storage class**, then the Secrets the charts read
   (`install.sh --prepare-only`), and a pull secret if the Harbor project is private;
4. **Argo CD:** the chart repository's credentials (a Secret in `argocd`; a robot account with pull rights is
   enough; the password goes to kubectl on stdin, never into a file), Harbor's CA if it has a private one
   (`argocd-tls-certs-cm`), and the Application filled in with your values, in `.deploy/argocd-<domain>/`;
5. it applies the Application (asked first; or tells you how to add it in Argo CD's UI), waits until it is Synced
   and Healthy, and seeds the image builder.

Without the script: push with `./make --push-images`, install the prerequisites and Secrets as steps 3–5 of "By
hand" below, fill in the CHANGE-MEs of `argocd/coder-decorated-application.yaml` and `kubectl apply -f` it.

**Taking the layer off:** `./make --argocd harbor.example.com/coder-airgapped --plain` (or `kubectl apply -f
.deploy/argocd-<domain>/coder-plain-application.yaml`). Argo CD prunes everything the layer added and restarts Coder
as upstream Coder; the database, users, templates, workspaces and their homes stay. Running workspaces keep running
on their image. Without the layer, Coder has no platform service, banner or template push; the fork's own dashboard
features go with its image. The layer's own data is kept too: the platform service's volume and log database
password, and the image builder's two volumes (built images, uploads) may never be pruned (`Prune=false`), so
`--plain` takes them out of the Application (Argo CD then reports it Synced) and they wait, as they are, for the
layer. **Putting it back:** `./make --argocd …` again (or apply the decorated file): Argo CD adopts those again,
data intact.

**Updating:** carry the next release's layer bundle (or full bundle), run `./make --argocd …` from it: it pushes the
new images and charts and moves the Application to the new version.

Notes: the nodes pull the images straight from Harbor, so a private project also needs the nodes' container runtime
to have credentials (the workspace pods have no pull secret of their own), and a private CA must be trusted by the
nodes. `ARGOCD_REPO=<host>/<project>/charts` tells Argo CD another address for the charts (e.g. Harbor through an
internal name). Non-interactive: `HARBOR_USER=... HARBOR_PASSWORD=... ARGOCD_USER=... ARGOCD_PASSWORD=...
DOMAIN=... CERTS=existing TLS_CRT=... TLS_KEY=... CA_BUNDLE=... OIDC_ISSUER=... OIDC_SECRET_FILE=... OIDC_CA=...
STORAGE_CLASS=... ./make --argocd harbor.example.com/coder-airgapped --yes`.

The steps below are the same install done by hand.

---

## By hand

Takes about 30 minutes. Steps 1–2 happen before you go in, steps 3–8 on a machine **inside** the air-gapped network.
No internet is needed – every image is in the bundle.


## Before you start

The air-gapped cluster needs these already running:

- [ ] **Kubernetes** 1.27 or newer (EKS is fine) with **amd64 (x86_64) nodes** – the images are amd64 only
- [ ] **Gateway API CRDs** v1.6.0 – `kubectl apply --server-side -f prereqs/gateway-api-v1.6.0-standard-install.yaml`
- [ ] **Traefik v3** with a **Gateway** that serves HTTPS for `coder.<your-domain>` and `*.<your-domain>`
      (plus Traefik's `rewrite-body` plugin – see `prereqs/traefik-41.4.0.reference-values.yaml`)
- [ ] **Keycloak** (or another OIDC login) with a client called `coder`
      and redirect URL `https://coder.<your-domain>/api/v2/users/oidc/callback` – your users sign in with it
- [ ] **DNS** for `coder.<your-domain>` and `*.<your-domain>`
- [ ] **Argo CD**
- [ ] **A container registry** (ECR, Harbor, ...)

Have these files ready:

| File | What it is |
|---|---|
| `ca.crt` | The CA that signed your HTTPS certificate |
| `wildcard.crt` / `wildcard.key` | The certificate for `*.<your-domain>` |
| `keycloak-ca.crt` | The CA of your Keycloak server |
| `oidc-client-secret.txt` | The Keycloak client secret, on one line |

Tools needed: `docker`, `helm`, `kubectl` (and `aws` if you use ECR).

---

## Step 1 – Check the bundle

```bash
tar -xzf coder-airgapped-1.38.0.tar.gz
cd coder-airgapped-1.38.0
./mirror-images.sh list
```

Every image should say `bundled` – they are all inside `images/` (see `images/README.md`), so no internet is needed.

Using the smaller **`-slim`** bundle (only the custom Coder image)? Then either run `./mirror-images.sh save` here on a
machine with internet, or – if your registry already has the public images – go on and use `custom` in Step 3.

## Step 2 – Carry it across

Copy the whole `coder-airgapped-1.38.0` folder into the air-gapped network.

---

## Step 3 – Push the images to your registry

```bash
cd coder-airgapped-1.38.0
REGISTRY=123456789012.dkr.ecr.us-east-1.amazonaws.com      # <-- your registry

docker login $REGISTRY                                     # ECR: aws ecr get-login-password | docker login -u AWS --password-stdin $REGISTRY
ECR=1 ./mirror-images.sh push $REGISTRY                    # drop ECR=1 if not using ECR
```

Public images already in your registry? Push only the custom one: `./mirror-images.sh push $REGISTRY custom`.
Harbor: `./make --push-images <harbor>/<project>` does this step and the next, logins included.

## Step 4 – Push the chart to your registry

```bash
helm registry login $REGISTRY                              # ECR: aws ecr get-login-password | helm registry login -u AWS --password-stdin $REGISTRY
ECR=1 ./mirror-images.sh chart $REGISTRY/charts            # drop ECR=1 if not using ECR
```

## Step 5 – Create the secrets (and the database operator)

```bash
CNPG_IMAGE=$REGISTRY/cloudnative-pg/cloudnative-pg:1.33.0 \
./install.sh --prepare-only --cnpg \
  --ca ca.crt \
  --tls-crt wildcard.crt --tls-key wildcard.key \
  --oidc-ca keycloak-ca.crt \
  --oidc-secret-file oidc-client-secret.txt
```

Leave out `--cnpg` if CloudNativePG is already installed on the cluster.

**Keycloak not ready yet?** Leave out `--oidc-ca` and `--oidc-secret-file`. Coder starts without them, and the admin
signs in with the password from Step 8. A Keycloak issuer that does not answer yet does not stop Coder either: its
sign-in button works as soon as Keycloak does. When Keycloak is set up, create the two secrets (run this step again
with both options) and restart Coder (`kubectl -n coder rollout restart deploy/coder`). Keep
`global.disablePasswordAuth: "false"` until a Keycloak sign-in has worked: with `"true"` nobody can sign in while
Keycloak is down.

## Step 6 – Fill in the Argo CD file

Open `argocd/coder-airgapped-application.yaml` and change every line marked **`CHANGE-ME`**:

| Setting | Example |
|---|---|
| Registry address (3 places) | `123456789012.dkr.ecr.us-east-1.amazonaws.com` |
| Registry password | ECR: output of `aws ecr get-login-password` |
| `domain` | `example.com` |
| `oidc.issuerUrl` | `https://keycloak.example.com/realms/master` |
| `storageClass` (5 places) | `gp3` |

Everything else can stay as it is. The admin account is `admin` / `admin@admin.com` with password **`admin`**
(`firstUser`); you change the password in Step 8.

## Step 7 – Deploy

```bash
kubectl apply -f argocd/coder-airgapped-application.yaml
```

Argo CD now installs everything. Watch it:

```bash
kubectl -n argocd get application coder      # wait for: Synced   Healthy
kubectl -n coder get pods                    # everything Running (takes 5–10 minutes)
```

## Step 8 – Sign in

Open **`https://coder.<your-domain>`** and sign in with email **`admin@admin.com`** and password **`admin`**.
You are the Coder admin (owner).

**Change the password now:** your avatar › **Account** › **Security**. (Later syncs never reset it.)

Everyone else clicks **Sign in with your certificate** and logs in with Keycloak; they get a normal account. The
admin can make others admins in **Admin settings › Users**. The banner starts empty – publish one at
`https://coder.<your-domain>/__banner/admin`.

Then give the image builder its base image (Templates › Images builds on the workspace image, from its own
registry in the cluster; `./make --deploy` does this itself):

```bash
./mirror-images.sh seed-builder coder          # uses kubectl port-forward; a few minutes
```

Done.

## Optional – kubectl and k9s for admins

Coder's admins (role Owner) can run `kubectl` and `k9s` on this cluster from their workspace terminals, each **signed
in as themselves**. There is no shared account, and the workspace holds no long-lived credential. The cluster sees the
person, e.g. `keycloak:alice` in group `keycloak:coder-cluster-admins` (an API server audit log, where one is turned
on, records that name). The group gets `cluster-admin`.

In the workspace, the first `kubectl` command prints a Keycloak link and code. The admin opens the link in their own
browser and signs in, as they do for Coder. The first time, they confirm the grant. Then kubectl continues. The sign-in lasts as long
as that Keycloak session (Minikube's development Keycloak: 30 minutes idle, 8 hours at most). It is forgotten when the
workspace restarts. k9s cannot show the link, so run a `kubectl` command (e.g. `kubectl auth whoami`) first.

Only Keycloak accounts can sign in. The built-in `admin@admin.com` has none: sign in to Coder with your own Keycloak
account and make it an Owner. The platform service keeps the Keycloak group equal to Coder's Owners who sign in with
Keycloak, checked every minute; anyone else added to the group in Keycloak is removed.

**Minikube:** `./make --deploy` sets it all up with its development Keycloak (the question *Let Coder's admins use
kubectl…*, default yes). The API server restarts once.

**EKS or another cluster:** three pieces, then `global.clusterAccess.enabled: true` in your values:

1. **Keycloak** (the realm of `global.oidc.issuerUrl`), as an admin with `kcadm.sh`:

   ```bash
   kcadm.sh create clients -r <realm> -f - <<'EOF'
   {"clientId": "kubernetes", "name": "Kubernetes (kubectl sign-in)", "publicClient": true, "consentRequired": true,
    "standardFlowEnabled": false, "directAccessGrantsEnabled": false,
    "attributes": {"oauth2.device.authorization.grant.enabled": "true", "access.token.lifespan": "600"},
    "protocolMappers": [{"name": "groups", "protocol": "openid-connect",
      "protocolMapper": "oidc-group-membership-mapper",
      "config": {"claim.name": "groups", "full.path": "false", "id.token.claim": "true", "access.token.claim": "true"}}]}
   EOF
   kcadm.sh create groups -r <realm> -s name=coder-cluster-admins
   ```

   If Coder's client signs in with certificates (its own browser flow), give this client the same flow:
   **Clients › kubernetes › Advanced › Authentication flow overrides › Browser flow**.
2. **The API server trusts Keycloak** for audience `kubernetes`: username claim `preferred_username`, groups claim
   `groups`, both with the prefix `keycloak:`. On EKS:

   ```bash
   aws eks associate-identity-provider-config --cluster-name <cluster> --oidc \
     identityProviderConfigName=keycloak,issuerUrl=<issuer URL>,clientId=kubernetes,usernameClaim=preferred_username,usernamePrefix=keycloak:,groupsClaim=groups,groupsPrefix=keycloak:
   ```

   EKS fetches the issuer's keys itself, so the issuer URL must be reachable from the EKS control plane, with a
   certificate it trusts. A Keycloak that only the private network can reach cannot be used this way. On a cluster
   you run yourself, use the API server's `--authentication-config` (see `minikube-support`, `signin_conf`).
3. **The workspace image has kubelogin** (`kubectl oidc-login`): `coder-workspace:1.7.0` and later.

The chart then adds the ClusterRoleBinding `coder-cluster-admins` and passes the issuer to the template. Admins'
workspaces get the kubeconfig at their next start. Settings: `global.clusterAccess` in `values.yaml` (client, group,
prefix, cluster role, which Coder roles count as admins, the group sync).

## Optional – the image builder (Templates › Images)

**Templates › Images** builds workspace images in the cluster for templates to run. Admins and template admins create
them; everyone can see them. Each image is a base image plus:

* apt packages, each checked in the apt sources as you type;
* uploaded `.deb` files;
* named installations, each with its own files and a bash script;
* removed packages and hardening;
* a desktop (XFCE, GNOME or none);
* shell configuration and dotfiles.

Each build is scanned: trivy for vulnerabilities and secrets, plus checks on users, sudo, setuid files and FIPS
readiness. A template uses the image as `localhost:5000/coder-images/<name>:<version>`. Images can be downloaded
(`docker load`) and backed up to S3.

It is on by default (`imageBuilder.enabled`). It adds:

* **`coder-image-registry`:** a registry for the built images, on a 50 Gi volume (`imageBuilder.registry.size`).
  The space of deleted images comes back the next time it starts (it garbage-collects first):
  `kubectl -n coder rollout restart deploy/coder-image-registry`.
* **A small DaemonSet, `coder-image-registry-proxy`**, on the host network of every node. It listens on
  `127.0.0.1:5000`, so each node's container runtime pulls the images from `localhost:5000`; runtimes treat localhost
  as a trusted registry without TLS. No node configuration is needed.
* **Build Jobs with BuildKit rootless** (`moby/buildkit:v0.33.0-rootless`). They run as uid 1000 with no privileges,
  but with **seccomp and AppArmor unconfined**: rootless BuildKit needs user namespaces. A cluster whose policy forbids
  unconfined pods (Pod Security `restricted`, or Kyverno/Gatekeeper rules) needs an exception for the `coder`
  namespace's `coder-image-*` pods. Each Job takes up to 4 CPUs and 8 Gi (`imageBuilder.build`).
* **Two volumes:** a 20 Gi one for uploads and build contexts (`imageBuilder.files`), and the platform service's
  limit raised to 512 Mi.

**In an air gap, set `imageBuilder.aptSources` to your apt mirror** (deb822, as in `values.yaml`), or set it later in
Images › Settings. The package check and the builds both use it. The vulnerability database is in the bundle (in
`coder-image-tools`, dated when the bundle was made); Java archives are not scanned, because their database is not
bundled. Set S3 backups (bucket, region, keys, or an S3-compatible endpoint such as MinIO) in Images › Settings.

Builds start from the workspace image in the builder's own registry in the cluster, so BuildKit needs no access
to your registry. `./make --deploy` puts it there (on any cluster); after an Argo CD or plain Helm install, run
`./mirror-images.sh seed-builder coder` once (Step 8). Until then, builds use the workspace image in your registry
(`global.registry`, with its pull secret if any).

## Keeping the security data current (air gap)

The image builder grades every image with trivy's vulnerability database and Java database, and with the FIPS rules.
In an air gap these age: trivy publishes a new vulnerability database every few hours, but the bundle carries the one
from the day it was made. Templates › Images shows their dates (the "Security data" badge, and Images › Settings). It
marks them ageing after 14 days and out of date after 30, and every report says which data graded it.

To update them:

1. On a machine with internet access and Docker, in this repository:
   `development/tools/build-security-data.sh`. It writes `dist/coder-security-data-<date>.tar.gz` (about 1.1 GB;
   `--no-java` leaves out the Java database: about 100 MB, but .jar / .war / .ear are then not scanned) and its
   `.sha256`. It uses the bundle's own trivy (`coder-image-tools`), so the database schema matches.
2. Carry the file across the air gap and check it: `sha256sum -c coder-security-data-<date>.tar.gz.sha256`.
3. In Coder, as an admin: Templates › Images › Settings › Security data › **Load security data…**. The service
   checks every file against the file's manifest and the database's schema before switching to it. The previous set
   is kept, and **Use the bundled data** goes back to the bundle's own.
4. **Rescan all built images**: each built image is scanned again with the new data, one after another. Nothing is
   rebuilt and versions stay; each image gets a new report.

The FIPS rules are `helm-chart/coder-airgapped/files/ui-updates/fips-rules.json`: versioned and dated, with the
standards they follow. Edit it (or pass `--fips-rules FILE`) when the rules change, and make a new file. FIPS-aligned
crypto in new builds uses the rules in force too. A scan needs up to 8 Gi of scratch space with the Java database.

## metrics-server (live CPU and memory)

The HUD, Health and quotas show live usage from the Kubernetes metrics API. The chart installs metrics-server
(`registry.k8s.io/metrics-server/metrics-server:v0.8.1`, in the bundle) only where the cluster has none
(`metricsServer.enabled: auto`). On EKS with the metrics-server add-on, nothing is installed. Set `false` to never
install it, `true` to always. Kubelets with self-signed serving certificates (Minikube, kind) need
`metricsServer.kubeletInsecureTLS: true`; `./make --deploy` sets it on Minikube. Keep it false on EKS.

## Workspace backups and disk growth

A workspace's ⋮ menu has **Back up home** and **Expand disk**, for its owner and admins. Both use the image builder's
volume (`imageBuilder.enabled`) and the platform service's permission to manage PVCs in the workspace namespace.

* **Backups** go to the S3 bucket set in Templates › Images › Settings, or become a download kept 24 hours on the
  builder volume (`imageBuilder.files.size` bounds how large).
* **Expand disk** grows the volume in place where the storage class has `allowVolumeExpansion: true` (EBS gp2 / gp3
  on EKS). Elsewhere it stops the workspace and copies its home onto a bigger volume.

> **Good to know – workspaces in a strict air gap:** everything a workspace runs is in the workspace image: the
> desktop, Firefox, code-server, File Browser, marimo (with all its packages), tmux, k9s/kubectl and the JetBrains Mono
> Nerd Font. Nothing is downloaded when a workspace starts (`coder-workspace:1.8.0` and later).

---

## If something goes wrong

| Problem | Check |
|---|---|
| Pods stuck in `ImagePullBackOff` | Step 3 – are the images in your registry? Is `registry` in the Argo CD file right? |
| Argo CD says it can't find the chart | Step 4, and the registry address/password at the top of the Argo CD file (ECR passwords expire after 12 hours) |
| `coder` pod stuck in `ContainerCreating` | Step 5 – the secrets are missing: `kubectl -n coder get secrets` |
| Database not starting | `kubectl -n coder get cluster` – is CloudNativePG installed? Is the storage class right? |
| Login page doesn't load | DNS and the Gateway's HTTPS certificate for `coder.<your-domain>` |
| `admin` / `admin` is refused | The admin's password was changed, or the database was not fresh: `kubectl -n coder logs job/coder-bootstrap-first-user` |
| No workspace template in Coder | `kubectl -n coder logs job/coder-bootstrap-first-user` and `job/coder-workspace-push-template` |

---

## Without Argo CD (plain Helm)

Do steps 1–3 and 5 as above (step 5 **without** `--prepare-only`), but first:

```bash
cp values-eks-airgap.example.yaml my-values.yaml    # change every CHANGE-ME
```

and add `-f my-values.yaml` to the step 5 command. That installs everything directly; skip steps 4, 6 and 7.

More detail: `README.md`.

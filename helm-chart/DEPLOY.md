# How to deploy Coder (air-gapped)

Takes about 30 minutes. Steps 1–2 happen before you go in, steps 3–8 on a machine **inside** the air-gapped network.
No internet is needed – every image is in the bundle.

---

## Before you start

The air-gapped cluster needs these already running:

- [ ] **Kubernetes** 1.27 or newer (EKS is fine) with **amd64 (x86_64) nodes** – the images are amd64 only
- [ ] **Gateway API CRDs** v1.5.1 – `kubectl apply --server-side -f prereqs/gateway-api-v1.5.1-standard-install.yaml`
- [ ] **Traefik v3** with a **Gateway** that serves HTTPS for `coder.<your-domain>` and `*.<your-domain>`
      (plus Traefik's `rewrite-body` plugin – see `prereqs/traefik-41.4.0.reference-values.yaml`)
- [ ] **Keycloak** (or another OIDC login) with a client called `coder`
      and redirect URL `https://coder.<your-domain>/api/v2/users/oidc/callback`,
      and **a Keycloak account for you** (you'll put its email in Step 6 – that account becomes Coder's admin)
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
tar -xzf coder-airgapped-1.2.0.tar.gz
cd coder-airgapped-1.2.0
./mirror-images.sh list
```

Every image should say `bundled` – they are all inside `images/` (see `images/README.md`), so no internet is needed.

Using the smaller **`-slim`** bundle (only the custom Coder image)? Then either run `./mirror-images.sh save` here on a
machine with internet, or – if your registry already has the public images – go on and use `custom` in Step 3.

## Step 2 – Carry it across

Copy the whole `coder-airgapped-1.2.0` folder into the air-gapped network.

---

## Step 3 – Push the images to your registry

```bash
cd coder-airgapped-1.2.0
REGISTRY=123456789012.dkr.ecr.us-east-1.amazonaws.com      # <-- your registry

docker login $REGISTRY                                     # ECR: aws ecr get-login-password | docker login -u AWS --password-stdin $REGISTRY
ECR=1 ./mirror-images.sh push $REGISTRY                    # drop ECR=1 if not using ECR
```

Public images already in your registry? Push only the custom one: `./mirror-images.sh push $REGISTRY custom`.

## Step 4 – Push the chart to your registry

```bash
helm registry login $REGISTRY                              # ECR: aws ecr get-login-password | helm registry login -u AWS --password-stdin $REGISTRY
ECR=1 ./mirror-images.sh chart $REGISTRY/charts            # drop ECR=1 if not using ECR
```

## Step 5 – Create the secrets (and the database operator)

```bash
CNPG_IMAGE=$REGISTRY/cloudnative-pg/cloudnative-pg:1.30.0 \
./install.sh --prepare-only --cnpg \
  --ca ca.crt \
  --tls-crt wildcard.crt --tls-key wildcard.key \
  --oidc-ca keycloak-ca.crt \
  --oidc-secret-file oidc-client-secret.txt
```

Leave out `--cnpg` if CloudNativePG is already installed on the cluster.

## Step 6 – Fill in the Argo CD file

Open `argocd/coder-airgapped-application.yaml` and change every line marked **`CHANGE-ME`**:

| Setting | Example |
|---|---|
| Registry address (3 places) | `123456789012.dkr.ecr.us-east-1.amazonaws.com` |
| Registry password | ECR: output of `aws ecr get-login-password` |
| `domain` | `example.com` |
| `oidc.issuerUrl` | `https://keycloak.example.com/realms/master` |
| `firstUser.email` | **the email of your Keycloak account** – it becomes the Coder admin |
| `storageClass` (2 places) | `gp3` |

Everything else can stay as it is.

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

Open **`https://coder.<your-domain>`**, click **Sign in with your certificate**, and log in to Keycloak with the
account whose email you put in `firstUser.email`. You are the Coder admin (owner). Everyone else who signs in with
Keycloak gets a normal account.

There is no password login (Keycloak only, by design), so Coder can only be signed in to while Keycloak is up.

Done.

> **Good to know – workspaces in a strict air gap:** workspaces start and connect, but the template installs its apps
> (code-server, File Browser, marimo, the desktop) from the internet when a workspace starts. Without internet those
> apps show as unhealthy. To get them, use a workspace image that already contains them (`coder-workspace.workspaceImage`)
> or point apt/pip at internal mirrors – see `README.md` › *Known gaps*.

---

## If something goes wrong

| Problem | Check |
|---|---|
| Pods stuck in `ImagePullBackOff` | Step 3 – are the images in your registry? Is `registry` in the Argo CD file right? |
| Argo CD says it can't find the chart | Step 4, and the registry address/password at the top of the Argo CD file (ECR passwords expire after 12 hours) |
| `coder` pod stuck in `ContainerCreating` | Step 5 – the secrets are missing: `kubectl -n coder get secrets` |
| Database not starting | `kubectl -n coder get cluster` – is CloudNativePG installed? Is the storage class right? |
| Login page doesn't load | DNS and the Gateway's HTTPS certificate for `coder.<your-domain>` |
| Signed in, but you are not admin | `firstUser.email` must be exactly your Keycloak account's email. Fix it before the first deploy |
| No workspace template in Coder | `kubectl -n coder logs job/coder-bootstrap-first-user` and `job/coder-workspace-push-template` |

---

## Without Argo CD (plain Helm)

Do steps 1–3 and 5 as above (step 5 **without** `--prepare-only`), but first:

```bash
cp values-eks-airgap.example.yaml my-values.yaml    # change every CHANGE-ME
```

and add `-f my-values.yaml` to the step 5 command. That installs everything directly; skip steps 4, 6 and 7.

More detail: `README.md`.

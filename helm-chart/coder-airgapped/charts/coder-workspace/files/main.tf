terraform {
  required_providers {
    coder = {
      source = "coder/coder"
    }
    kubernetes = {
      source = "hashicorp/kubernetes"
    }
  }
}

provider "coder" {}

variable "use_kubeconfig" {
  type        = bool
  description = "Use host kubeconfig? (true/false)"
  default     = false
}

# The StorageClass of workspaces' home volumes ("" = the cluster's default). EKS clusters may have no default class:
# set it (coder-workspace.storageClass in the chart). Only new volumes use it: a volume's class never changes.
variable "home_storage_class" {
  type        = string
  description = "StorageClass of the home volumes (empty: the cluster's default)."
  default     = ""
}

variable "namespace" {
  type        = string
  description = "The Kubernetes namespace to create workspaces in. Workspaces run only in the coder namespace."
  default     = "coder"
  validation {
    condition     = var.namespace == "coder"
    error_message = "Workspaces are created only in the coder namespace."
  }
}

# Admins' kubectl and k9s on this cluster (global.clusterAccess in the chart): each admin signs in as themselves with
# Keycloak's device login (kubelogin), so the cluster sees the person (keycloak:<user>).
# The workspace holds no long-lived credential; the sign-in lasts as long as the Keycloak session. "" = off.
variable "cluster_oidc_issuer" {
  type        = string
  description = "Keycloak issuer URL the cluster trusts for admins' kubectl sign-in (\"\" = off)."
  default     = ""
}

variable "cluster_oidc_client_id" {
  type        = string
  description = "Keycloak client of the kubectl sign-in (public, device login)."
  default     = "kubernetes"
}

variable "cluster_admin_roles" {
  type        = string
  description = "Coder roles (comma-separated) whose workspaces get the cluster kubeconfig."
  default     = "owner"
}

# Which apps the workspace has: "developer" (ubuntu-vnc-desktop: code-server, marimo) or "dataflow" (ubuntu-dataflow:
# Apache NiFi, Apache Airflow). Everything else is the same. The chart pushes this template twice (templates/job.yaml).
variable "app_set" {
  type        = string
  description = "developer (code-server, marimo) or dataflow (Apache NiFi, Apache Airflow)."
  default     = "developer"
  validation {
    condition     = contains(["developer", "dataflow"], var.app_set)
    error_message = "app_set must be developer or dataflow."
  }
}

# The Gateway that serves Coder (the dataflow template's NiFi route attaches to it), and Coder's Service behind it.
variable "gateway_name" {
  type        = string
  description = "The Gateway serving Coder's host (its NiFi route attaches to it)."
  default     = "traefik-gateway"
}

variable "gateway_namespace" {
  type    = string
  default = "traefik"
}

variable "coder_service" {
  type        = string
  description = "Coder's Service in this namespace, as Coder's own HTTPRoute names it."
  default     = "coder"
}

variable "coder_service_port" {
  type    = number
  default = 80
}

# The workspace image; empty: the chart's (substituted for "codercom/enterprise-base:ubuntu" below). The dataflow
# template's has NiFi and Airflow too (development/workspace-image-dataflow).
variable "workspace_image" {
  type        = string
  description = "The workspace pods' image; empty: the template's default."
  default     = ""
}

locals {
  dataflow        = var.app_set == "dataflow"
  workspace_image = var.workspace_image != "" ? var.workspace_image : "codercom/enterprise-base:ubuntu"
}

data "coder_parameter" "cpu" {
  name         = "cpu"
  display_name = "CPU"
  description  = "The number of CPU cores"
  default      = "2"
  icon         = "/icon/memory.svg"
  mutable      = true
  option {
    name  = "2 Cores"
    value = "2"
  }
  option {
    name  = "4 Cores"
    value = "4"
  }
  option {
    name  = "6 Cores"
    value = "6"
  }
  option {
    name  = "8 Cores"
    value = "8"
  }
}

data "coder_parameter" "memory" {
  name         = "memory"
  display_name = "Memory"
  description  = "The amount of memory in GB"
  # NiFi (a 1 GB Java heap) and Airflow beside the desktop need more than the developer apps.
  default      = local.dataflow ? "6" : "2"
  icon         = "/__icons/memory.svg" # files/icons/memory.svg (a memory module), served by templates/icons.yaml
  mutable      = true
  option {
    name  = "2 GB"
    value = "2"
  }
  option {
    name  = "4 GB"
    value = "4"
  }
  option {
    name  = "6 GB"
    value = "6"
  }
  option {
    name  = "8 GB"
    value = "8"
  }
}

data "coder_parameter" "home_disk_size" {
  name         = "home_disk_size"
  display_name = "Home disk size"
  description  = "The size of the home disk in GB"
  default      = "10"
  type         = "number"
  icon         = "/__icons/hard-drive.svg" # files/icons/hard-drive.svg, served by templates/icons.yaml
  mutable      = false
  validation {
    min = 1
    max = 99999
  }
}

provider "kubernetes" {
  config_path = var.use_kubeconfig == true ? "~/.kube/config" : null
}

data "coder_workspace" "me" {}
data "coder_workspace_owner" "me" {}

locals {
  # The owner is an admin (one of cluster_admin_roles) and the cluster sign-in is on.
  cluster_admin = var.cluster_oidc_issuer != "" && length(setintersection(
    toset([for r in data.coder_workspace_owner.me.rbac_roles : r.name]),
    toset([for r in split(",", var.cluster_admin_roles) : trimspace(r) if trimspace(r) != ""]),
  )) > 0
  cluster_kubeconfig = "/home/coder/.kube/coder-cluster.yaml"
  # The metrics' functions (the agent's metadata below).
  metadata_sh = file("${path.module}/scripts/metadata.sh")
  # Lock Down (an admin's jail for this workspace: Network, Sudo or both): the script the pod's init container runs
  # at start, and the host the workspace's agent reaches Coder on (Network keeps it).
  lockdown_sh = file("${path.module}/scripts/lockdown.sh")
  coder_host  = regex("^[a-z]+://([^/:]+)", data.coder_workspace.me.access_url)[0]
}

resource "coder_agent" "main" {
  os             = "linux"
  arch           = "amd64"

  display_apps {
    web_terminal = true
    vscode       = false
    ssh_helper   = false
  }

  # Admins' terminals, k9s and apps find the cluster sign-in first, then their own ~/.kube/config.
  env = local.cluster_admin ? { KUBECONFIG = "${local.cluster_kubeconfig}:/home/coder/.kube/config" } : {}

  startup_script = templatefile("${path.module}/scripts/startup.sh", {
    owner_name             = data.coder_workspace_owner.me.name
    workspace_name         = data.coder_workspace.me.name
    namespace              = var.namespace
    cluster_admin          = local.cluster_admin
    cluster_kubeconfig     = local.cluster_kubeconfig
    cluster_oidc_issuer    = var.cluster_oidc_issuer
    cluster_oidc_client_id = var.cluster_oidc_client_id
    app_set                = var.app_set
    access_host            = replace(replace(data.coder_workspace.me.access_url, "https://", ""), "http://", "")
  })

  # The metrics, in the same order in both of the dashboard's groups (the keys order them): CPU, RAM, Disk and Ø Load
  # (the load average) of the workspace, then of the node it runs on (the Cluster group: keys ending in "_host"). Each
  # but CPU is a function of scripts/metadata.sh, which explains it: the file runs with the function's name appended.
  metadata {
    display_name = "CPU"
    key          = "0_cpu_usage"
    script       = "coder stat cpu"
    interval     = 10
    timeout      = 1
  }

  metadata {
    display_name = "RAM"
    key          = "1_ram_usage"
    script       = "${local.metadata_sh}\nram"
    interval     = 10
    timeout      = 1
  }

  metadata {
    display_name = "Disk"
    key          = "2_home_disk"
    script       = "${local.metadata_sh}\ndisk"
    interval     = 60
    timeout      = 15
  }

  metadata {
    display_name = "Ø Load"
    key          = "3_load"
    script       = "${local.metadata_sh}\nload"
    interval     = 10
    timeout      = 3
  }

  metadata {
    display_name = "CPU"
    key          = "4_cpu_usage_host"
    script       = "coder stat cpu --host"
    interval     = 10
    timeout      = 1
  }

  metadata {
    display_name = "RAM"
    key          = "5_mem_usage_host"
    script       = "${local.metadata_sh}\nram_host"
    interval     = 10
    timeout      = 1
  }

  metadata {
    display_name = "Disk"
    key          = "6_disk_host"
    script       = "${local.metadata_sh}\ndisk_host"
    interval     = 60
    timeout      = 5
  }

  metadata {
    display_name = "Ø Load"
    key          = "7_load_host"
    script       = "${local.metadata_sh}\nload_host"
    interval     = 60
    timeout      = 1
  }

  # The workspace's network traffic (bytes received and sent so far), every second: not a metric display, the
  # dashboard's network lights by the workspace's name (key ending in "net_io").
  metadata {
    display_name = "Network"
    key          = "8_net_io"
    script       = "${local.metadata_sh}\nnet"
    interval     = 1
    timeout      = 1
  }
}

# Lock Down, at start: Sudo (Network is the pod's init container, below). Login waits for it.
resource "coder_script" "lockdown" {
  agent_id           = coder_agent.main.id
  display_name       = "Lock Down"
  icon               = "/__icons/lockdown.svg" # files/icons/lockdown.svg, served by templates/icons.yaml
  run_on_start       = true
  start_blocks_login = true
  script = templatefile("${path.module}/scripts/lockdown-start.sh", {
    workspace_id = data.coder_workspace.me.id
  })
}

resource "coder_app" "marimo" {
  count        = local.dataflow ? 0 : 1
  agent_id     = coder_agent.main.id
  slug         = "marimo"
  display_name = "marimo"
  icon         = "/__icons/marimo.svg"   # files/icons/marimo.svg, served by templates/icons.yaml
  # Path-based, like the other apps. marimo serves under the full app path (--base-url in the
  # startup script), so the URL carries it too ("main" = agent name).
  url          = "http://localhost:2718/@${data.coder_workspace_owner.me.name}/${data.coder_workspace.me.name}.main/apps/marimo/"
  subdomain    = false
  share        = "owner"

  healthcheck {
    url       = "http://localhost:2718/@${data.coder_workspace_owner.me.name}/${data.coder_workspace.me.name}.main/apps/marimo/health"
    interval  = 5
    threshold = 60
  }
}

# The windows of marimo notebooks (tkinter), shown in their cells' output (coder_marimo_tk): not a button of its own.
resource "coder_app" "marimo_windows" {
  count        = local.dataflow ? 0 : 1
  agent_id     = coder_agent.main.id
  slug         = "marimo-windows"
  display_name = "marimo windows"
  icon         = "/__icons/marimo.svg"
  url          = "http://localhost:6902"
  subdomain    = false
  share        = "owner"
  hidden       = true
}

resource "coder_app" "novnc" {
  agent_id     = coder_agent.main.id
  slug         = "novnc"
  display_name = "Desktop"
  icon         = "/icon/desktop.svg" # Coder's own icon (served by the dashboard; the noVNC logo was on the internet)
  # Path-based (no wildcard DNS/route needed). noVNC opens its websocket relative to the host
  # root, so pass the prefixed path explicitly ("main" = agent name, as in filebrowser above).
  url          = "http://localhost:6901?autoconnect=true&resize=remote&compression=0&path=@${data.coder_workspace_owner.me.name}/${data.coder_workspace.me.name}.main/apps/novnc/websockify"
  subdomain    = false
  share        = "owner"

  healthcheck {
    url       = "http://localhost:6901/"
    interval  = 5
    threshold = 3
  }
}

resource "coder_app" "filebrowser" {
  agent_id     = coder_agent.main.id
  slug         = "filebrowser"
  display_name = "File Browser"
  icon         = "/icon/folder.svg"
  url          = "http://localhost:13339"
  subdomain    = false
  share        = "owner"

  healthcheck {
    url       = "http://localhost:13339/health"
    interval  = 5
    threshold = 3
  }
}

resource "coder_app" "code-server" {
  count        = local.dataflow ? 0 : 1
  agent_id     = coder_agent.main.id
  slug         = "code-server"
  display_name = "code-server"
  icon         = "/icon/code.svg"
  url          = "http://localhost:13337?folder=/home/coder"
  subdomain    = false
  share        = "owner"

  healthcheck {
    url       = "http://localhost:13337/healthz"
    interval  = 3
    threshold = 10
  }
}

# The dataflow template's apps, in place of code-server and marimo: path-based like the others, so they open in the
# dashboard's panels (same origin). Started by the startup script. NiFi learns its path from the headers the cluster's
# Gateway adds (the HTTPRoute nifi_route below).
resource "coder_app" "nifi" {
  count        = local.dataflow ? 1 : 0
  agent_id     = coder_agent.main.id
  slug         = "nifi"
  display_name = "Apache NiFi"
  icon         = "/__icons/nifi.svg" # files/icons/nifi.svg, served by templates/icons.yaml
  # The root: Coder joins the app URL's path to every request, and NiFi serves at /nifi and /nifi-api (its root
  # redirects to its UI, under the app's path).
  url          = "http://127.0.0.1:8080"
  subdomain    = false
  share        = "owner"

  # The dashboard's panel shows the port and protocol it is checked on.
  healthcheck {
    url       = "http://127.0.0.1:8080/nifi-api/flow/about"
    interval  = 5
    threshold = 60
  }
}

# NiFi under Coder's app path: NiFi builds its pages' links and API calls from the proxy headers it is sent
# (X-ProxyContextPath, -Scheme, -Host, -Port), which Coder's app proxy passes on but does not add. The cluster's Gateway
# (Traefik) adds them, by an HTTPRoute of this workspace's own on Coder's host: only its NiFi path, to Coder as Coder's
# own route does (the longest path wins), with these headers set. Nothing else in front of NiFi; Coder still decides
# who may open the app. Needs Coder's service account to manage HTTPRoutes in the namespace (the chart's Role).
locals {
  nifi_path   = "/@${data.coder_workspace_owner.me.name}/${data.coder_workspace.me.name}.main/apps/nifi"
  coder_https = startswith(data.coder_workspace.me.access_url, "https://")
}

resource "kubernetes_manifest" "nifi_route" {
  count = local.dataflow ? 1 : 0
  manifest = {
    apiVersion = "gateway.networking.k8s.io/v1"
    kind       = "HTTPRoute"
    metadata = {
      name      = "coder-${data.coder_workspace.me.id}-nifi"
      namespace = var.namespace
      labels = {
        "app.kubernetes.io/name"    = "coder-workspace"
        "app.kubernetes.io/part-of" = "coder"
        "com.coder.resource"        = "true"
        "com.coder.workspace.id"    = data.coder_workspace.me.id
        "com.coder.workspace.name"  = data.coder_workspace.me.name
        "com.coder.user.username"   = data.coder_workspace_owner.me.name
      }
    }
    spec = {
      hostnames = [local.coder_host]
      parentRefs = [{
        group     = "gateway.networking.k8s.io"
        kind      = "Gateway"
        name      = var.gateway_name
        namespace = var.gateway_namespace
      }]
      rules = [{
        matches = [{ path = { type = "PathPrefix", value = local.nifi_path } }]
        filters = [{
          type = "RequestHeaderModifier"
          requestHeaderModifier = {
            set = [
              { name = "X-ProxyContextPath", value = local.nifi_path },
              { name = "X-ProxyScheme", value = local.coder_https ? "https" : "http" },
              { name = "X-ProxyHost", value = local.coder_host },
              { name = "X-ProxyPort", value = local.coder_https ? "443" : "80" },
            ]
          }
        }]
        backendRefs = [{ name = var.coder_service, port = var.coder_service_port }]
      }]
    }
  }
}

resource "coder_app" "airflow" {
  count        = local.dataflow ? 1 : 0
  agent_id     = coder_agent.main.id
  slug         = "airflow"
  display_name = "Apache Airflow"
  icon         = "/icon/airflow.svg" # Coder's own icon
  # Airflow serves under the full app path (its base_url, in the startup script), so the URL carries it too.
  url          = "http://localhost:8090/@${data.coder_workspace_owner.me.name}/${data.coder_workspace.me.name}.main/apps/airflow/"
  subdomain    = false
  share        = "owner"

  healthcheck {
    url       = "http://localhost:8090/@${data.coder_workspace_owner.me.name}/${data.coder_workspace.me.name}.main/apps/airflow/api/v2/monitor/health"
    interval  = 5
    threshold = 60
  }
}

resource "kubernetes_persistent_volume_claim_v1" "home" {
  metadata {
    name      = "coder-${data.coder_workspace.me.id}-home"
    namespace = var.namespace
    labels = {
      "app.kubernetes.io/name"     = "coder-pvc"
      "app.kubernetes.io/instance" = "coder-pvc-${data.coder_workspace.me.id}"
      "app.kubernetes.io/part-of"  = "coder"
      "com.coder.resource"         = "true"
      "com.coder.workspace.id"     = data.coder_workspace.me.id
      "com.coder.workspace.name"   = data.coder_workspace.me.name
      "com.coder.user.id"          = data.coder_workspace_owner.me.id
      "com.coder.user.username"    = data.coder_workspace_owner.me.name
    }
    annotations = {
      "com.coder.user.email" = data.coder_workspace_owner.me.email
    }
  }
  wait_until_bound = false

  spec {
    access_modes       = ["ReadWriteOnce"]
    storage_class_name = var.home_storage_class != "" ? var.home_storage_class : null
    resources {
      requests = {
        storage = "${data.coder_parameter.home_disk_size.value}G"
      }
    }
  }

  # The parameter is the size the workspace was created with; the disk can grow later (the workspace's ⋮ menu >
  # Expand disk, done by the platform service), so later builds leave the volume's size alone. The storage class
  # is fixed when the volume is made: a change of home_storage_class must never replace (empty) existing homes.
  lifecycle {
    ignore_changes = [spec[0].resources, spec[0].storage_class_name]
  }
}

resource "kubernetes_deployment_v1" "main" {
  count = data.coder_workspace.me.start_count
  depends_on = [
    kubernetes_persistent_volume_claim_v1.home
  ]
  wait_for_rollout = false
  metadata {
    name      = "coder-${data.coder_workspace.me.id}"
    namespace = var.namespace
    labels = {
      "app.kubernetes.io/name"     = "coder-workspace"
      "app.kubernetes.io/instance" = "coder-workspace-${data.coder_workspace.me.id}"
      "app.kubernetes.io/part-of"  = "coder"
      "com.coder.resource"         = "true"
      "com.coder.workspace.id"     = data.coder_workspace.me.id
      "com.coder.workspace.name"   = data.coder_workspace.me.name
      "com.coder.user.id"          = data.coder_workspace_owner.me.id
      "com.coder.user.username"    = data.coder_workspace_owner.me.name
    }
    annotations = {
      "com.coder.user.email" = data.coder_workspace_owner.me.email
    }
  }

  spec {
    replicas = 1
    selector {
      match_labels = {
        "app.kubernetes.io/name"     = "coder-workspace"
        "app.kubernetes.io/instance" = "coder-workspace-${data.coder_workspace.me.id}"
        "app.kubernetes.io/part-of"  = "coder"
        "com.coder.resource"         = "true"
        "com.coder.workspace.id"     = data.coder_workspace.me.id
        "com.coder.workspace.name"   = data.coder_workspace.me.name
        "com.coder.user.id"          = data.coder_workspace_owner.me.id
        "com.coder.user.username"    = data.coder_workspace_owner.me.name
      }
    }
    strategy {
      type = "Recreate"
    }

    template {
      metadata {
        labels = {
          "app.kubernetes.io/name"     = "coder-workspace"
          "app.kubernetes.io/instance" = "coder-workspace-${data.coder_workspace.me.id}"
          "app.kubernetes.io/part-of"  = "coder"
          "com.coder.resource"         = "true"
          "com.coder.workspace.id"     = data.coder_workspace.me.id
          "com.coder.workspace.name"   = data.coder_workspace.me.name
          "com.coder.user.id"          = data.coder_workspace_owner.me.id
          "com.coder.user.username"    = data.coder_workspace_owner.me.name
        }
      }
      spec {
        security_context {
          run_as_user     = 1000
          fs_group        = 1000
          run_as_non_root = true
        }

        # Trust the local PKI: append the cluster root CA (secret "tls-ca") to the
        # image's stock bundle so curl, the agent, git and apt can reach
        # https://coder.xxx.com while public sites still verify.
        init_container {
          name              = "trust-local-ca"
          image             = local.workspace_image
          image_pull_policy = "IfNotPresent"
          command = [
            "sh", "-c",
            "cat /etc/ssl/certs/ca-certificates.crt /local-ca/ca.crt > /ca-bundle/ca-certificates.crt",
          ]
          volume_mount {
            name       = "local-ca"
            mount_path = "/local-ca"
            read_only  = true
          }
          volume_mount {
            name       = "ca-bundle"
            mount_path = "/ca-bundle"
          }
        }

        # Lock Down, at start: Network, before anything in the workspace runs. The admins' setting comes from the
        # ConfigMap coder-lockdown (the platform service keeps it; none yet: nothing is locked). Root with NET_ADMIN
        # in the pod's network namespace, which the workspace container shares but cannot change (it has no
        # NET_ADMIN). A running workspace is locked without a restart, by the platform service.
        init_container {
          name              = "lockdown"
          image             = local.workspace_image
          image_pull_policy = "IfNotPresent"
          command = [
            "sh", "-c", local.lockdown_sh, "lockdown",
            "file:/etc/coder-lockdown/${data.coder_workspace.me.id}", local.coder_host, "",
          ]
          security_context {
            run_as_user     = 0
            run_as_non_root = false
            capabilities {
              add = ["NET_ADMIN", "NET_RAW"]
            }
          }
          volume_mount {
            name       = "lockdown"
            mount_path = "/etc/coder-lockdown"
            read_only  = true
          }
        }

        container {
          name              = "dev"
          image             = local.workspace_image
          # The workspace image is versioned (a new build gets a new tag), and offline there is nothing to pull from.
          image_pull_policy = "IfNotPresent"
          # No core dumps from the agent or anything it starts (terminals, the desktop, apps).
          command = ["sh", "-c", join("\n", ["ulimit -c 0", coder_agent.main.init_script])]
          security_context {
            run_as_user = "1000"
          }
          volume_mount {
            name       = "ca-bundle"
            mount_path = "/etc/ssl/certs/ca-certificates.crt"
            sub_path   = "ca-certificates.crt"
            read_only  = true
          }
          env {
            name  = "CODER_AGENT_TOKEN"
            value = coder_agent.main.token
          }
          # The home volume's size as Kubernetes has it (it can grow after creation), for the Home Disk metric.
          env {
            name  = "CODER_HOME_DISK_SIZE"
            value = kubernetes_persistent_volume_claim_v1.home.spec[0].resources[0].requests["storage"]
          }
          resources {
            requests = {
              "cpu"    = "250m"
              "memory" = "512Mi"
            }
            limits = {
              "cpu"    = "${data.coder_parameter.cpu.value}"
              "memory" = "${data.coder_parameter.memory.value}Gi"
            }
          }
          volume_mount {
            mount_path = "/home/coder"
            name       = "home"
            read_only  = false
          }
          # Firefox's tabs share memory through /dev/shm; a pod's default 64 MiB makes them crash.
          volume_mount {
            mount_path = "/dev/shm"
            name       = "dshm"
          }
          # Lock Down's setting (read-only): the start script reads it for Sudo.
          volume_mount {
            mount_path = "/etc/coder-lockdown"
            name       = "lockdown"
            read_only  = true
          }
        }

        volume {
          name = "dshm"
          empty_dir {
            medium     = "Memory"
            size_limit = "1Gi"
          }
        }

        volume {
          name = "home"
          persistent_volume_claim {
            claim_name = kubernetes_persistent_volume_claim_v1.home.metadata.0.name
            read_only  = false
          }
        }

        volume {
          name = "local-ca"
          secret {
            secret_name = "tls-ca"
            items {
              key  = "ca.crt"
              path = "ca.crt"
            }
          }
        }

        volume {
          name = "ca-bundle"
          empty_dir {}
        }

        # Lock Down's settings, by workspace id (the platform service makes the ConfigMap at the first lock).
        volume {
          name = "lockdown"
          config_map {
            name     = "coder-lockdown"
            optional = true
          }
        }

        affinity {
          pod_anti_affinity {
            preferred_during_scheduling_ignored_during_execution {
              weight = 1
              pod_affinity_term {
                topology_key = "kubernetes.io/hostname"
                label_selector {
                  match_expressions {
                    key      = "app.kubernetes.io/name"
                    operator = "In"
                    values   = ["coder-workspace"]
                  }
                }
              }
            }
          }
        }
      }
    }
  }
}

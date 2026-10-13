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
  default      = "2"
  icon         = "/icon/memory.svg"
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
  icon         = "/emojis/1f4be.png"
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
  })

  metadata {
    display_name = "CPU"
    key          = "0_cpu_usage"
    script       = "coder stat cpu"
    interval     = 10
    timeout      = 1
  }

  # Memory in GB (10^9 bytes) like the disks, not coder stat's GiB: its JSON (indented, so joined into one line for
  # awk) gives the bytes (the workspace's: its cgroup's usage and limit; with --host, the node's).
  metadata {
    display_name = "RAM"
    key          = "1_ram_usage"
    script       = file("${path.module}/scripts/metadata-ram.sh")
    interval     = 10
    timeout      = 1
  }

  # The home volume, in GB: what it holds of its own size (CODER_HOME_DISK_SIZE, the volume's size as Kubernetes
  # has it). Where the file system is the volume (EBS...), df says both; where the volume is a directory of a bigger
  # disk (local-path, hostPath), df would show that disk, so du counts the home instead.
  metadata {
    display_name = "Disk"
    key          = "3_home_disk"
    script       = file("${path.module}/scripts/metadata-disk.sh")
    interval     = 60
    timeout      = 15
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
    script       = file("${path.module}/scripts/metadata-ram-host.sh")
    interval     = 10
    timeout      = 1
  }

  # The disk of the node the workspace runs on (where its images and containers live; on Minikube, the host's disk),
  # in GB like the workspace's.
  metadata {
    display_name = "Disk"
    key          = "6_disk_host"
    script       = file("${path.module}/scripts/metadata-disk-host.sh")
    interval = 60
    timeout  = 5
  }

  # The host's 1-minute load over its cores: /proc/loadavg is the host's, so it is divided by the host's online
  # cores (getconf), not by nproc (in the container: the workspace's CPU limit, which made 6 / 6 cores read as fully
  # loaded on a 24-core host).
  metadata {
    display_name = "Load Avg"
    key          = "7_load_host"
    script       = file("${path.module}/scripts/metadata-load-host.sh")
    interval = 60
    timeout  = 1
  }
}

resource "coder_app" "marimo" {
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
          image             = "codercom/enterprise-base:ubuntu"
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

        container {
          name              = "dev"
          image             = "codercom/enterprise-base:ubuntu"
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

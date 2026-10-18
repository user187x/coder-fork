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

# Which apps the workspace has: "developer" (ubuntu-vnc-desktop: code-server), "dataflow" (ubuntu-dataflow:
# Apache NiFi, Apache Airflow), or one of the AI templates on the AI workspace image: "lmstudio" (ubuntu-lmstudio:
# LM Studio on the desktop, code-server) and "opencode" (ubuntu-opencode: the OpenCode agent, code-server; LM Studio
# serving its models). Everything else is the same. The chart pushes this template once per set (templates/job.yaml).
variable "app_set" {
  type        = string
  description = "developer (code-server), dataflow (Apache NiFi, Apache Airflow), lmstudio (LM Studio) or opencode (OpenCode on LM Studio)."
  default     = "developer"
  validation {
    condition     = contains(["developer", "dataflow", "lmstudio", "opencode"], var.app_set)
    error_message = "app_set must be developer, dataflow, lmstudio or opencode."
  }
}

# The apps a template made in Templates > Create new template has (its Desktop, File Browser and Terminal choices):
# the desktop (noVNC, and its X session), File Browser, and the dashboard's web terminal.
variable "desktop" {
  type        = bool
  description = "The Desktop app: the workspace's desktop in the browser (noVNC)."
  default     = true
}

variable "file_browser" {
  type        = bool
  description = "The File Browser app."
  default     = true
}

variable "terminal" {
  type        = bool
  description = "The web terminal."
  default     = true
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

# The owner's Cloud Drive (the chart's userStorage): their own S3 storage, the same in each of their workspaces. The platform
# service makes each account's volume (PersistentVolumeClaim coder-safe-<owner id>); the pod waits for it if it is not
# there yet.
variable "user_storage" {
  type        = bool
  description = "Mount the owner's Cloud Drive (their own storage, S3) in the workspace."
  default     = false
}

variable "user_storage_path" {
  type        = string
  description = "Where the Cloud Drive is in the workspace (a link to its volume while the workspace is connected)."
  default     = "/home/coder/cloud-drive"
}

# The workspace image; empty: the chart's (substituted for "codercom/enterprise-base:ubuntu" below). The dataflow
# template's has NiFi and Airflow too (development/workspace-image-dataflow).
variable "workspace_image" {
  type        = string
  description = "The workspace pods' image; empty: the template's default."
  default     = ""
}

# The Images view's registry as workspace pods pull from it (the chart's imageBuilder); empty: no Workspace image choice.
variable "images_registry" {
  type        = string
  description = "Where the Images view's images are pulled from, e.g. localhost:5000/coder-images."
  default     = ""
}

locals {
  dataflow      = var.app_set == "dataflow"
  # The AI templates: LM Studio (its app on the desktop and its server) in both, OpenCode's app in ubuntu-opencode.
  ai            = contains(["lmstudio", "opencode"], var.app_set)
  default_image = var.workspace_image != "" ? var.workspace_image : "codercom/enterprise-base:ubuntu"
  # The Workspace image parameter's, where the template offers one.
  workspace_image = length(data.coder_parameter.workspace_image) > 0 ? data.coder_parameter.workspace_image[0].value : local.default_image
}

# The workspace's image: the template's own, or one from Templates > Images (built or imported there; in the cluster's
# registry), so a workspace can run on an image made for it. Its pull reference, as the Images view shows it. Changed on
# a running workspace, it takes effect at its next start.
data "coder_parameter" "workspace_image" {
  count        = var.images_registry != "" ? 1 : 0
  name         = "workspace_image"
  display_name = "Workspace image"
  description  = "The template's image, or one from Templates › Images (${var.images_registry}/<name>:<version>). An image built on the workspace image has everything this template runs."
  type         = "string"
  default      = local.default_image
  mutable      = true
  icon         = "/icon/docker.svg"
  validation {
    regex = "^(${replace(local.default_image, ".", "\\.")}|${replace(var.images_registry, ".", "\\.")}/[a-z0-9][a-z0-9-]{1,38}[a-z0-9]:[0-9]+)$"
    error = "The template's image (${local.default_image}), or one from Templates › Images: ${var.images_registry}/<name>:<version>."
  }
}

data "coder_parameter" "cpu" {
  name         = "cpu"
  display_name = "CPU"
  description  = "The number of CPU cores"
  # A language model on the CPU wants several.
  default      = local.ai ? "4" : "2"
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
  option {
    name  = "12 Cores"
    value = "12"
  }
  option {
    name  = "16 Cores"
    value = "16"
  }
}

data "coder_parameter" "memory" {
  name         = "memory"
  display_name = "Memory"
  description  = "The amount of memory in GB"
  # NiFi (a 1 GB Java heap) and Airflow beside the desktop need more than the developer apps; a language model needs
  # its size and its context's (an 8B model at 4 bits: some 5 GB, and 1-4 GB more for a long context).
  default      = local.ai ? "8" : local.dataflow ? "6" : "2"
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
  option {
    name  = "12 GB"
    value = "12"
  }
  option {
    name  = "16 GB"
    value = "16"
  }
  option {
    name  = "24 GB"
    value = "24"
  }
  option {
    name  = "32 GB"
    value = "32"
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

# Where the terminal (the agent's sessions) and File Browser (its root) open; made at start if it is missing under the
# home. Changed on a running workspace, it takes effect at its next start.
data "coder_parameter" "start_directory" {
  name         = "start_directory"
  display_name = "Start folder"
  description  = "Where the terminal and File Browser open: an absolute path, made at start if it is missing under /home/coder. File Browser shows this folder and what is below it."
  type         = "string"
  default      = "/home/coder"
  icon         = "/icon/folder.svg"
  mutable      = true
  validation {
    regex = "^/([A-Za-z0-9._@+-]+/?)*$"
    error = "An absolute path of letters, digits and . _ - @ +, such as /home/coder/projects."
  }
}

# The AI templates: how many tokens LM Studio gives a model it loads (its default context length; LM Studio's own,
# 8192, is too little for OpenCode, whose instructions alone take some 10 000), and OpenCode's limit per model (or the
# model's own maximum, if less). More context takes more memory.
data "coder_parameter" "context_length" {
  count        = local.ai ? 1 : 0
  name         = "context_length"
  display_name = "Context length"
  description  = "Tokens a model gets when LM Studio loads it (its default context length; OpenCode needs at least 16K). More takes more memory."
  default      = "32768"
  icon         = "/__icons/lmstudio.svg"
  mutable      = true
  option {
    name  = "8K tokens"
    value = "8192"
  }
  option {
    name  = "16K tokens"
    value = "16384"
  }
  option {
    name  = "32K tokens"
    value = "32768"
  }
  option {
    name  = "64K tokens"
    value = "65536"
  }
}

# The AI templates: NVIDIA GPUs for the workspace (nvidia.com/gpu, from the cluster's NVIDIA device plugin; LM Studio
# runs its models on them with its CUDA engine). The workspace waits for a node that has them free; none: the CPU.
data "coder_parameter" "gpus" {
  count        = local.ai ? 1 : 0
  name         = "gpus"
  display_name = "GPUs"
  description  = "NVIDIA GPUs for LM Studio (nodes with the NVIDIA device plugin). The workspace waits until a node has them free; 0: the CPU only."
  default      = "0"
  icon         = "/icon/memory.svg"
  mutable      = true
  option {
    name  = "None (CPU)"
    value = "0"
  }
  option {
    name  = "1 GPU"
    value = "1"
  }
  option {
    name  = "2 GPUs"
    value = "2"
  }
  option {
    name  = "4 GPUs"
    value = "4"
  }
}

locals {
  context_length = local.ai ? data.coder_parameter.context_length[0].value : ""
  gpus           = local.ai ? tonumber(data.coder_parameter.gpus[0].value) : 0
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
  # The host the workspace's agent reaches Coder on (Lock Down's Network keeps it; the dataflow template's NiFi route).
  coder_host = regex("^[a-z]+://([^/:]+)", data.coder_workspace.me.access_url)[0]
}

# ---------------------------------------------------------------------------------------------------------------------
# The platform's sections (# platform:begin NAME ... # platform:end NAME): what every workspace of the platform has -
# the owner's certificate, Lock Down, the Cloud Drive, the metrics. Coder's template decorator (the fork's
# coderd/templatedecorator) takes them from this template, the platform's base, and injects each into any template that
# lacks it, at the same place (top level, the agent, the pod, the agent's container), when it is built: a template made
# in Create new template has none of them in its own code, and Source Code > Materialized shows them injected.
# ---------------------------------------------------------------------------------------------------------------------

# platform:begin metrics
locals {
  # The metrics' functions (the agent's metadata below).
  platform_metadata_sh = file("${path.module}/scripts/metadata.sh")
}
# platform:end metrics

# platform:begin lockdown
locals {
  # Lock Down (an admin's jail for this workspace: Network, Sudo or both): the script the pod's init container runs
  # at start (Network) and the agent runs at start (Sudo; login waits for it).
  platform_lockdown_sh = file("${path.module}/scripts/lockdown.sh")
}

# Lock Down, at start: Sudo (Network is the pod's init container, below). Login waits for it.
resource "coder_script" "lockdown" {
  agent_id           = coder_agent.main.id
  display_name       = "Lock Down"
  icon               = "/__icons/lockdown.svg" # files/icons/lockdown.svg, served by templates/icons.yaml
  run_on_start       = true
  start_blocks_login = true
  script             = "#!/bin/sh\nset -- start 'file:/etc/coder-lockdown/${data.coder_workspace.me.id}'\n${local.platform_lockdown_sh}"
}
# platform:end lockdown

# platform:begin certificates
# Installs the owner's PKCS#12 certificate into the workspace as ~/.cert/<owner>.p12, where <owner> is the Coder
# username (for user jdoe: /home/coder/.cert/jdoe.p12). Nothing else is left in ~/.cert.
# Where the certificate comes from: each user uploads theirs in Coder > Settings > Certificates. The page stores the
# file as one of the user's own Coder secrets (base64, because secret values are text), with the file path
# ~/.cert/.<owner>.p12.b64. When a workspace starts, Coder's agent writes the owner's secrets (and only the owner's) to
# those paths, directory 0700 and files 0600, before any startup script runs. This script decodes it to
# ~/.cert/<owner>.p12 (0600) and removes the encoded copy. An upload saved under another name (by an earlier version
# of the page, or before the user was renamed) is installed under the owner's name all the same. When the owner
# deletes the certificate in Coder, it is removed from ~/.cert on the next start (only a file this script installed).
resource "coder_script" "user_certificates" {
  agent_id           = coder_agent.main.id
  display_name       = "Certificates"
  icon               = "/__icons/certificate.svg" # files/icons/certificate.svg, served by templates/icons.yaml
  run_on_start       = true
  start_blocks_login = true # nobody gets a shell before the certificate is in place
  script = templatefile("${path.module}/scripts/certificates.sh", {
    owner_name = data.coder_workspace_owner.me.name
  })
}
# platform:end certificates

# platform:begin cloud-drive
locals {
  # The Cloud Drive's volume, out of the way: user_storage_path links to it while the owner keeps the workspace
  # connected (coder_script.cloud_drive).
  cloud_drive_mount = "/mnt/cloud-drive"
  # What the platform service says of the Cloud Drive when this workspace starts (data.kubernetes_config_map_v1.cloud_drive):
  # a workspace shared with others has none (the Cloud Drive is its owner's alone); the owner's source workspace (a
  # folder of it is their Cloud Drive) serves that folder and links to it; the owner's others mount the source's
  # volume while it runs, else nothing until their next start ("offline"); without a source, the S3 volume.
  cloud_drive_state     = try(jsondecode(data.kubernetes_config_map_v1.cloud_drive[0].data["state"]), {})
  cloud_drive_shared    = contains(try(local.cloud_drive_state.shared, []), data.coder_workspace.me.id)
  cloud_drive_source    = try(local.cloud_drive_state.sources[data.coder_workspace_owner.me.id], null)
  cloud_drive_is_source = try(local.cloud_drive_source.workspace, "") == data.coder_workspace.me.id
  cloud_drive_mode = (!var.user_storage ? "" : local.cloud_drive_shared ? "shared" : local.cloud_drive_is_source ? "source"
  : local.cloud_drive_source == null || try(local.cloud_drive_source.running, false) ? "mount" : "offline")
  cloud_drive_claim = local.cloud_drive_mode != "mount" ? "" : (local.cloud_drive_source == null
  ? "coder-safe-${data.coder_workspace_owner.me.id}" : "coder-drive-src-${data.coder_workspace_owner.me.id}")
}

# The Cloud Drive's state, kept by the platform service (the chart's coder-workspace Role lets Coder's service account
# read this one ConfigMap): {"shared": [workspace ids], "sources": {owner id: {workspace, path, running}}}.
data "kubernetes_config_map_v1" "cloud_drive" {
  count = var.user_storage ? 1 : 0
  metadata {
    name      = "coder-cloud-drive"
    namespace = var.namespace
  }
}

# The Cloud Drive, at start and then live: user_storage_path links to its volume while the pod's annotation
# platform.coder/cloud-drive says "on" (the owner connects and disconnects the workspace on the Workspaces page).
resource "coder_script" "cloud_drive" {
  count              = var.user_storage ? 1 : 0
  agent_id           = coder_agent.main.id
  display_name       = "Cloud Drive"
  icon               = "/__icons/cloud-drive.svg" # files/icons/cloud-drive.svg, served by templates/icons.yaml
  run_on_start       = true
  start_blocks_login = true
  script = templatefile("${path.module}/scripts/cloud-drive.sh", {
    link = var.user_storage_path
    mode = local.cloud_drive_mode
    # the volume, where this workspace has one ("on"); the source's folder comes in the annotation (source:<folder>)
    mount = local.cloud_drive_claim != "" ? local.cloud_drive_mount : ""
    # the `cloud-drive` command: the Cloud Drive mounted into folders of the workspace (scripts/cloud-drive-cli.py)
    cli = base64encode(file("${path.module}/scripts/cloud-drive-cli.py"))
  })
}
# platform:end cloud-drive

# platform:begin code-server-ha
# code-server as a global singleton (the chart's codeServerHA): not in the workspace, but one instance per workspace
# across every cluster that shares Coder's database, kept by the code-server-replicator; a relay in this pod
# (127.0.0.1:13337, where code-server's app looks) takes its connections there. Off unless a template turns it on.
variable "code_server_ha" {
  type        = bool
  description = "code-server as a global singleton kept by the code-server-replicator (high availability), not in the workspace."
  default     = false
}

variable "code_server_ha_image" {
  type        = string
  description = "The relay's image (the chart's platform service image); empty: no high availability."
  default     = ""
}

variable "code_server_ha_db_ca_secret" {
  type        = string
  description = "The Secret with the CA of Coder's database (the relay checks it)."
  default     = "coder-db-ca"
}

locals {
  code_server_ha = var.code_server_ha && var.code_server_ha_image != ""
}
# platform:end code-server-ha

resource "coder_agent" "main" {
  os             = "linux"
  arch           = "amd64"
  # The terminal's and SSH sessions' starting directory (the Start folder parameter; the startup script makes it).
  dir = data.coder_parameter.start_directory.value

  display_apps {
    web_terminal = var.terminal
    vscode       = false
    ssh_helper   = false
  }

  # Admins' terminals, k9s and apps find the cluster sign-in first, then their own ~/.kube/config.
  # The AI templates: OpenCode reads its settings for this workspace (LM Studio as the provider) from this file, which
  # the startup script writes (coder-opencode-config, in the AI workspace image).
  env = merge(
    local.cluster_admin ? { KUBECONFIG = "${local.cluster_kubeconfig}:/home/coder/.kube/config" } : {},
    local.ai ? { OPENCODE_CONFIG = "/home/coder/.config/opencode/coder-workspace.json" } : {},
  )

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
    start_directory        = data.coder_parameter.start_directory.value
    context_length         = local.context_length
    desktop                = var.desktop
    file_browser           = var.file_browser
  })

  # platform:begin metrics
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
    script       = "${local.platform_metadata_sh}\nram"
    interval     = 10
    timeout      = 1
  }

  metadata {
    display_name = "Disk"
    key          = "2_home_disk"
    script       = "${local.platform_metadata_sh}\ndisk"
    interval     = 60
    timeout      = 15
  }

  metadata {
    display_name = "Ø Load"
    key          = "3_load"
    script       = "${local.platform_metadata_sh}\nload"
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
    script       = "${local.platform_metadata_sh}\nram_host"
    interval     = 10
    timeout      = 1
  }

  metadata {
    display_name = "Disk"
    key          = "6_disk_host"
    script       = "${local.platform_metadata_sh}\ndisk_host"
    interval     = 60
    timeout      = 5
  }

  metadata {
    display_name = "Ø Load"
    key          = "7_load_host"
    script       = "${local.platform_metadata_sh}\nload_host"
    interval     = 60
    timeout      = 1
  }

  # The workspace's network traffic (bytes received and sent so far), every second: not a metric display, the
  # dashboard's network lights by the workspace's name (key ending in "net_io").
  metadata {
    display_name = "Network"
    key          = "8_net_io"
    script       = "${local.platform_metadata_sh}\nnet"
    interval     = 1
    timeout      = 1
  }
  # platform:end metrics
}

# In ubuntu-lmstudio the desktop is LM Studio's window (the app starts there), so it is the LM Studio app.
resource "coder_app" "novnc" {
  count        = var.desktop ? 1 : 0
  agent_id     = coder_agent.main.id
  slug         = "novnc"
  display_name = var.app_set == "lmstudio" ? "LM Studio" : "Desktop"
  # Coder's own icon (served by the dashboard; the noVNC logo was on the internet); LM Studio's (files/icons/lmstudio.svg)
  icon         = var.app_set == "lmstudio" ? "/__icons/lmstudio.svg" : "/icon/desktop.svg"
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
  count        = var.file_browser ? 1 : 0
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

# ubuntu-opencode: OpenCode, the AI coding agent, in a terminal in the Start folder, on the workspace's LM Studio (its
# settings: OPENCODE_CONFIG, above). Quitting it closes the terminal; it opens again from the button.
resource "coder_app" "opencode" {
  count        = var.app_set == "opencode" ? 1 : 0
  agent_id     = coder_agent.main.id
  slug         = "opencode"
  display_name = "OpenCode"
  icon         = "/__icons/opencode.svg" # files/icons/opencode.svg, served by templates/icons.yaml
  command      = "cd ${jsonencode(data.coder_parameter.start_directory.value)} 2>/dev/null; exec opencode"
  share        = "owner"
}

# The dataflow template's apps, in place of code-server: path-based like the others, so they open in the
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
        # platform:begin cloud-drive
        # Connected to the owner's Cloud Drive from the start; the platform service sets "off" for a workspace the
        # owner disconnected (and back), and the pod reads it at /etc/platform/cloud-drive.
        # The source workspace (a folder of it is the owner's Cloud Drive) starts linking that folder.
        annotations = var.user_storage ? { "platform.coder/cloud-drive" = (local.cloud_drive_is_source
        ? "source:${try(local.cloud_drive_source.path, "")}" : "on") } : {}
        # platform:end cloud-drive
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

        # platform:begin lockdown
        # Lock Down, at start: Network, before anything in the workspace runs. The admins' setting comes from the
        # ConfigMap coder-lockdown (the platform service keeps it; none yet: nothing is locked). Root with NET_ADMIN
        # in the pod's network namespace, which the workspace container shares but cannot change (it has no
        # NET_ADMIN). A running workspace is locked without a restart, by the platform service.
        init_container {
          name              = "lockdown"
          image             = local.workspace_image
          image_pull_policy = "IfNotPresent"
          command = [
            "sh", "-c", local.platform_lockdown_sh, "lockdown",
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

        # Lock Down's settings, by workspace id (the platform service makes the ConfigMap at the first lock).
        volume {
          name = "lockdown"
          config_map {
            name     = "coder-lockdown"
            optional = true
          }
        }
        # platform:end lockdown

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
            # The AI templates' GPUs (none: no such limit).
            limits = merge({
              "cpu"    = "${data.coder_parameter.cpu.value}"
              "memory" = "${data.coder_parameter.memory.value}Gi"
            }, local.gpus > 0 ? { "nvidia.com/gpu" = tostring(local.gpus) } : {})
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
          # platform:begin lockdown
          # Lock Down's setting (read-only): the start script reads it for Sudo.
          volume_mount {
            mount_path = "/etc/coder-lockdown"
            name       = "lockdown"
            read_only  = true
          }
          # platform:end lockdown
          # platform:begin cloud-drive
          # The owner's Cloud Drive: the same files in every one of their workspaces (and on the Workspaces page), at
          # user_storage_path while the workspace is connected (a link to this mount; coder_script.cloud_drive).
          dynamic "volume_mount" {
            for_each = local.cloud_drive_claim != "" ? [1] : []
            content {
              mount_path = local.cloud_drive_mount
              name       = "safe"
            }
          }
          # Whether it is connected: the pod's annotation platform.coder/cloud-drive, kept current by the kubelet.
          dynamic "volume_mount" {
            for_each = var.user_storage ? [1] : []
            content {
              mount_path = "/etc/platform"
              name       = "platform-info"
              read_only  = true
            }
          }
          # The ISO library (Templates > Images), read-only: the same disk images in every workspace.
          dynamic "volume_mount" {
            for_each = var.user_storage ? [1] : []
            content {
              mount_path = "/home/coder/isos"
              name       = "isos"
              read_only  = true
            }
          }
          dynamic "env" {
            for_each = var.user_storage ? [1] : []
            content {
              name  = "CODER_SAFE"
              value = var.user_storage_path
            }
          }
          # platform:end cloud-drive
          # platform:begin code-server-ha
          # code-server runs elsewhere (code-server HA): the startup script starts none here.
          dynamic "env" {
            for_each = local.code_server_ha ? [1] : []
            content {
              name  = "CODER_CODE_SERVER_HA"
              value = "1"
            }
          }
          # platform:end code-server-ha
          # The AI templates: the model library (Templates > Images), read-only: the same GGUF models in every AI
          # workspace (LM Studio lists them).
          dynamic "volume_mount" {
            for_each = var.user_storage && local.ai ? [1] : []
            content {
              mount_path = "/home/coder/models"
              name       = "models"
              read_only  = true
            }
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

        # platform:begin code-server-ha
        # The relay (code-server HA): where code-server would listen, and it takes each connection to the workspace's
        # active code-server instance, in this cluster or another.
        dynamic "container" {
          for_each = local.code_server_ha ? [1] : []
          content {
            name              = "code-server-relay"
            image             = var.code_server_ha_image
            image_pull_policy = "IfNotPresent"
            command           = ["python3", "/app/csha.py", "front"]
            security_context {
              run_as_user                = 1000
              allow_privilege_escalation = false
              read_only_root_filesystem  = true
              capabilities {
                drop = ["ALL"]
              }
            }
            env {
              name  = "CSHA_WORKSPACE"
              value = data.coder_workspace.me.id
            }
            # its proof: the agent's token (Coder's registry of agents)
            env {
              name  = "CSHA_AGENT_TOKEN"
              value = coder_agent.main.token
            }
            env {
              name  = "CSHA_NAMESPACE"
              value = var.namespace
            }
            env {
              name  = "CSHA_IMAGE"
              value = local.workspace_image
            }
            env {
              name = "CSHA_NODE"
              value_from {
                field_ref {
                  field_path = "spec.nodeName"
                }
              }
            }
            env {
              name = "CSHA_CLUSTER"
              value_from {
                config_map_key_ref {
                  name     = "coder-code-server-ha"
                  key      = "cluster"
                  optional = true
                }
              }
            }
            env {
              name = "CSHA_DB"
              value_from {
                secret_key_ref {
                  name = "coder-code-server-ha-relay"
                  key  = "uri"
                }
              }
            }
            env {
              name  = "CSHA_DB_CA"
              value = "/etc/coder-db-ca/ca.crt"
            }
            resources {
              requests = {
                cpu    = "10m"
                memory = "32Mi"
              }
              limits = {
                memory = "128Mi"
              }
            }
            volume_mount {
              name       = "coder-db-ca"
              mount_path = "/etc/coder-db-ca"
              read_only  = true
            }
          }
        }
        dynamic "volume" {
          for_each = local.code_server_ha ? [1] : []
          content {
            name = "coder-db-ca"
            secret {
              secret_name = var.code_server_ha_db_ca_secret
              items {
                key  = "ca.crt"
                path = "ca.crt"
              }
            }
          }
        }
        # platform:end code-server-ha

        # platform:begin cloud-drive
        # The owner's Cloud Drive: their S3 prefix, or their source workspace's folder, through the cluster's CSI driver
        # (ReadWriteMany: all their workspaces); none in a shared workspace or the source itself (local.cloud_drive_mode).
        dynamic "volume" {
          for_each = local.cloud_drive_claim != "" ? [1] : []
          content {
            name = "safe"
            persistent_volume_claim {
              claim_name = local.cloud_drive_claim
            }
          }
        }
        dynamic "volume" {
          for_each = var.user_storage ? [1] : []
          content {
            name = "platform-info"
            downward_api {
              items {
                path = "cloud-drive"
                field_ref {
                  field_path = "metadata.annotations['platform.coder/cloud-drive']"
                }
              }
            }
          }
        }
        # The ISO library: one read-only volume for every workspace (the platform service makes it).
        dynamic "volume" {
          for_each = var.user_storage ? [1] : []
          content {
            name = "isos"
            persistent_volume_claim {
              claim_name = "coder-isos"
              read_only  = true
            }
          }
        }
        # platform:end cloud-drive

        # The model library: one read-only volume for every AI workspace (the platform service makes it).
        dynamic "volume" {
          for_each = var.user_storage && local.ai ? [1] : []
          content {
            name = "models"
            persistent_volume_claim {
              claim_name = "coder-models"
              read_only  = true
            }
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

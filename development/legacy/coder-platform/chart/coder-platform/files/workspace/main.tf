terraform {
  required_providers {
    # Exact versions: the air-gapped Coder image carries exactly these in its provider mirror
    # (images/coder/Dockerfile mirrors whatever this file pins). Change both together.
    coder = {
      source  = "coder/coder"
      version = "2.18.0"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "3.2.1"
    }
  }
}

provider "coder" {}

variable "use_kubeconfig" {
  type        = bool
  description = "Use host kubeconfig? (true/false)"
  default     = false
}

# @@...@@ values are filled in by the coder-platform chart (templates/workspace-template.yaml) when it pushes this
# template, so they always match the installation.
variable "namespace" {
  type        = string
  description = "The Kubernetes namespace to create workspaces in. Workspaces run only in Coder's own namespace."
  default     = "@@WORKSPACE_NAMESPACE@@"
  validation {
    condition     = var.namespace == "@@WORKSPACE_NAMESPACE@@"
    error_message = "Workspaces are created only in the @@WORKSPACE_NAMESPACE@@ namespace."
  }
}

variable "workspace_image" {
  type        = string
  description = "Container image of the workspace (tools preinstalled: code-server, File Browser, marimo, desktop)."
  default     = "@@WORKSPACE_IMAGE@@"
}

variable "image_pull_secret" {
  type        = string
  description = "docker-registry Secret for pulling the workspace image; empty when the registry needs no login."
  default     = "@@IMAGE_PULL_SECRET@@"
}

variable "ca_secret" {
  type        = string
  description = "Secret (key ca.crt) with the CA that signed Coder's certificate; added to the workspace's trust store."
  default     = "@@CA_SECRET@@"
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

resource "coder_agent" "main" {
  os             = "linux"
  arch           = "amd64"

  display_apps {
    web_terminal = true
    vscode       = false
    ssh_helper   = false
  }

  startup_script = <<-EOT
    set -e

    # ~/.cert/<owner>.p12 is installed by the "Certificates" script (certificates.tf) from Settings > Certificates.

    # Every tool below is preinstalled in the coder-platform workspace image (images/workspace), which is what
    # air-gapped clusters run. A tool is downloaded only when the image lacks it (e.g. the stock
    # codercom/enterprise-base image on a connected cluster).

    # 1. code-server
    CODE_SERVER=/opt/code-server/bin/code-server
    if [ ! -x "$CODE_SERVER" ]; then
      CODE_SERVER=/tmp/code-server/bin/code-server
      [ -x "$CODE_SERVER" ] || curl -fsSL https://code-server.dev/install.sh | sh -s -- --method=standalone --prefix=/tmp/code-server
    fi
    # Probe the port rather than pgrep -f: the agent runs this script as `sh -c '<script>'`,
    # so `pgrep -f` matches the script's own command line and never starts anything.
    if ! curl -fs http://localhost:13337/healthz > /dev/null; then
      "$CODE_SERVER" --auth none --port 13337 >/tmp/code-server.log 2>&1 &
    fi

    # 2. File Browser
    if ! command -v filebrowser >/dev/null; then
      curl -fsSL https://raw.githubusercontent.com/filebrowser/get/master/get.sh | sudo bash
    fi
    # Path-based apps are proxied with their full /@user/workspace.agent/apps/<slug> prefix
    # intact, so filebrowser must serve under it or its assets 404 (endless spinner).
    # The agent name ("main") is hardcoded: referencing coder_agent.main here would be a cycle.
    if ! pgrep -x filebrowser > /dev/null; then
      filebrowser --noauth --root /home/coder --port 13339 \
        --baseurl "/@${data.coder_workspace_owner.me.name}/${data.coder_workspace.me.name}.main/apps/filebrowser" \
        >/tmp/filebrowser.log 2>&1 &
    fi

    # 3. marimo (Python notebooks): the image's /opt/marimo-venv, else a venv on the home volume, installed once
    # per workspace and again only when MARIMO_VERSION changes.
    MARIMO_VERSION=0.24.2
    MARIMO_VENV=/opt/marimo-venv
    MARIMO_BASE="/@${data.coder_workspace_owner.me.name}/${data.coder_workspace.me.name}.main/apps/marimo"
    if [ "$($MARIMO_VENV/bin/marimo --version 2>/dev/null)" != "$MARIMO_VERSION" ]; then
      MARIMO_VENV=/home/coder/.marimo-venv
      if [ "$($MARIMO_VENV/bin/marimo --version 2>/dev/null)" != "$MARIMO_VERSION" ]; then
        python3 -m venv $MARIMO_VENV
        $MARIMO_VENV/bin/pip install --quiet --upgrade "marimo==$MARIMO_VERSION"
      fi
    fi
    # marimo has no setting for its home page "Resources" section (external links): hide it
    # with a CSS rule in marimo's own index.html (the column whose 3-column grid holds
    # target=_blank cards; nothing else matches).
    MARIMO_INDEX=$($MARIMO_VENV/bin/python -c 'import marimo, os; print(os.path.join(os.path.dirname(marimo.__file__), "_static", "index.html"))')
    if ! grep -q marimo-hide-resources "$MARIMO_INDEX" && [ -w "$MARIMO_INDEX" ]; then
      sed -i 's#</head>#<style id="marimo-hide-resources">div.flex.flex-col.gap-2:has(> div.grid.gap-3.lg\\:grid-cols-3 > a[target="_blank"]){display:none !important}</style></head>#' "$MARIMO_INDEX"
    fi
    mkdir -p /home/coder/notebooks
    # --no-token: no marimo login page. marimo listens on 127.0.0.1 only, so the Coder agent's
    # proxy is the only way in, and the app is share = "owner": Coder decides who gets in.
    if ! curl -fs "http://localhost:2718$MARIMO_BASE/health" > /dev/null; then
      $MARIMO_VENV/bin/marimo edit --headless --host 127.0.0.1 --port 2718 --no-token \
        --skip-update-check --base-url "$MARIMO_BASE" /home/coder/notebooks \
        >/tmp/marimo.log 2>&1 &
    fi

    # 4. Install Desktop Environment & noVNC
    if [ ! -f /usr/bin/vncserver ]; then
      sudo apt-get update
      sudo DEBIAN_FRONTEND=noninteractive apt-get install -y xfce4 xfce4-goodies dbus-x11 tigervnc-standalone-server novnc websockify
    fi

    # Serve the noVNC client as the directory index so the bare app URL loads it (done in the image already).
    [ -e /usr/share/novnc/index.html ] || sudo ln -sf /usr/share/novnc/vnc.html /usr/share/novnc/index.html

    # Configure XFCE to start on VNC launch
    # TigerVNC >= 1.14 reads ~/.config/tigervnc (the legacy ~/.vnc fails to migrate),
    # and the session ends when xstartup returns, so xfce must be exec'd in the foreground.
    mkdir -p ~/.config/tigervnc
    cat << 'VNC_SCRIPT' > ~/.config/tigervnc/xstartup
#!/bin/sh
xrdb $HOME/.Xresources 2>/dev/null
exec startxfce4
VNC_SCRIPT
    chmod +x ~/.config/tigervnc/xstartup

    # Start TigerVNC server on display :1. Bound to localhost only: websockify below
    # runs in the same pod, and TigerVNC refuses unauthenticated non-local listeners.
    if ! pgrep -x Xtigervnc > /dev/null; then
      vncserver :1 -geometry 1280x800 -depth 24 -localhost yes -SecurityTypes None
    fi

    # Start websockify proxy for noVNC. Coder strips the /@user/workspace.agent/apps/novnc
    # prefix before proxying, so the stock web root works as-is.
    if ! pgrep -x websockify > /dev/null; then
      websockify --web=/usr/share/novnc/ 6901 localhost:5901 > /tmp/novnc.log 2>&1 &
    fi
  EOT

  metadata {
    display_name = "CPU Usage"
    key          = "0_cpu_usage"
    script       = "coder stat cpu"
    interval     = 10
    timeout      = 1
  }

  metadata {
    display_name = "RAM Usage"
    key          = "1_ram_usage"
    script       = "coder stat mem"
    interval     = 10
    timeout      = 1
  }

  metadata {
    display_name = "Home Disk"
    key          = "3_home_disk"
    script       = "coder stat disk --path $${HOME}"
    interval     = 60
    timeout      = 1
  }

  metadata {
    display_name = "CPU Usage (Host)"
    key          = "4_cpu_usage_host"
    script       = "coder stat cpu --host"
    interval     = 10
    timeout      = 1
  }

  metadata {
    display_name = "Memory Usage (Host)"
    key          = "5_mem_usage_host"
    script       = "coder stat mem --host"
    interval     = 10
    timeout      = 1
  }

  metadata {
    display_name = "Load Average (Host)"
    key          = "6_load_host"
    script       = <<EOT
      echo "`cat /proc/loadavg | awk '{ print $1 }'` `nproc`" | awk '{ printf "%0.2f", $1/$2 }'
    EOT
    interval = 60
    timeout  = 1
  }
}

resource "coder_app" "marimo" {
  agent_id     = coder_agent.main.id
  slug         = "marimo"
  display_name = "marimo"
  icon         = "/__icons/marimo.svg"   # files/workspace/icons/marimo.svg, served by the chart's templates/icons.yaml
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

resource "coder_app" "novnc" {
  agent_id     = coder_agent.main.id
  slug         = "novnc"
  display_name = "Desktop (noVNC)"
  icon         = "/icon/novnc.svg"
  # Path-based (no wildcard DNS/route needed). noVNC opens its websocket relative to the host
  # root, so pass the prefixed path explicitly ("main" = agent name, as in filebrowser above).
  url          = "http://localhost:6901?autoconnect=true&resize=remote&path=@${data.coder_workspace_owner.me.name}/${data.coder_workspace.me.name}.main/apps/novnc/websockify"
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
    access_modes = ["ReadWriteOnce"]
    resources {
      requests = {
        storage = "${data.coder_parameter.home_disk_size.value}Gi"
      }
    }
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

        # The workspace image is built for amd64 only (clusters may also have arm64 nodes, e.g. AWS Graviton).
        node_selector = {
          "kubernetes.io/arch" = "amd64"
        }

        dynamic "image_pull_secrets" {
          for_each = var.image_pull_secret == "" ? [] : [var.image_pull_secret]
          content {
            name = image_pull_secrets.value
          }
        }

        # Trust the local PKI: append the CA in Secret var.ca_secret (optional: without it the stock bundle is
        # used as is) to the image's stock bundle so curl, the agent, git and apt reach Coder's HTTPS address.
        init_container {
          name              = "trust-local-ca"
          image             = var.workspace_image
          image_pull_policy = "IfNotPresent"
          command = [
            "sh", "-c",
            "cat /etc/ssl/certs/ca-certificates.crt $(ls /local-ca/ca.crt 2>/dev/null) > /ca-bundle/ca-certificates.crt",
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
          image             = var.workspace_image
          image_pull_policy = "IfNotPresent"
          command           = ["sh", "-c", coder_agent.main.init_script]
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
            secret_name = var.ca_secret
            optional    = true
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

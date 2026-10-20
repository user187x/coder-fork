# The DATAFLOW template's own part (the chart pushes it beside main.tf, and with scripts/apache_nifi.sh and
# scripts/apache_airflow.sh, for that template only): Apache NiFi and Apache Airflow in place of code-server (main.tf's
# code_server = false), each started by a script of its own, NiFi's route on the cluster's Gateway, and Ghostty in
# place of the web terminal.

# Ghostty in place of the web terminal (the chart's dataflowTemplate.terminal; its push then sets main.tf's terminal
# to false) - the Ghostty app where the Terminal was, opening the same session in Coder's terminal page, drawn by
# Ghostty's terminal emulator (ghostty-web, in the dashboard).
variable "terminal_app" {
  type        = string
  description = "The web terminal: web (Coder's own) or ghostty (Ghostty's, as the Ghostty app in its place)."
  default     = "web"
  validation {
    condition     = contains(["web", "ghostty"], var.terminal_app)
    error_message = "terminal_app: web or ghostty."
  }
}

# The Gateway that serves Coder (the NiFi route below attaches to it), and Coder's Service behind it.
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

# Its apps, in place of code-server: path-based like the others, so they open in the
# dashboard's panels (same origin). Each started by a script of its own (coder_script.nifi and coder_script.airflow
# below, beside the startup script). NiFi learns its path from the headers the cluster's Gateway adds (the HTTPRoute
# nifi_route below).
resource "coder_app" "nifi" {
  agent_id     = coder_agent.main.id
  slug         = "nifi"
  display_name = "Apache NiFi"
  icon         = "/__icons/nifi.svg" # files/icons/nifi.svg, served by templates/icons.yaml
  # The root: Coder joins the app URL's path to every request, and NiFi serves at /nifi and /nifi-api (its root
  # redirects to its UI, under the app's path).
  url       = "http://127.0.0.1:8080"
  subdomain = false
  share     = "owner"

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
  nifi_path    = "/@${data.coder_workspace_owner.me.name}/${data.coder_workspace.me.name}.main/apps/nifi"
  airflow_path = "/@${data.coder_workspace_owner.me.name}/${data.coder_workspace.me.name}.main/apps/airflow"
  coder_https  = startswith(data.coder_workspace.me.access_url, "https://")
  # Coder's host as browsers reach it, with its port if it has one (NiFi and Airflow make their links with it)
  access_host = replace(replace(data.coder_workspace.me.access_url, "https://", ""), "http://", "")
}

# NiFi and Airflow at every start, each in a script of its own (the workspace's Logs show them by name): their homes
# in ~/.nifi and ~/airflow, their app paths and Coder's host.
resource "coder_script" "nifi" {
  agent_id     = coder_agent.main.id
  display_name = "Apache NiFi"
  icon         = "/__icons/nifi.svg" # files/icons/nifi.svg, served by templates/icons.yaml
  run_on_start = true
  script = templatefile("${path.module}/scripts/apache_nifi.sh", {
    path        = local.nifi_path
    access_host = local.access_host
  })
}

resource "coder_script" "airflow" {
  agent_id     = coder_agent.main.id
  display_name = "Apache Airflow"
  icon         = "/icon/airflow.svg" # Coder's own icon
  run_on_start = true
  script = templatefile("${path.module}/scripts/apache_airflow.sh", {
    path        = local.airflow_path
    access_host = local.access_host
  })
}

resource "kubernetes_manifest" "nifi_route" {
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
  agent_id     = coder_agent.main.id
  slug         = "airflow"
  display_name = "Apache Airflow"
  icon         = "/icon/airflow.svg" # Coder's own icon
  # Airflow serves under the full app path (its base_url, in scripts/apache_airflow.sh), so the URL carries it too.
  url       = "http://localhost:8090/@${data.coder_workspace_owner.me.name}/${data.coder_workspace.me.name}.main/apps/airflow/"
  subdomain = false
  share     = "owner"

  healthcheck {
    url       = "http://localhost:8090/@${data.coder_workspace_owner.me.name}/${data.coder_workspace.me.name}.main/apps/airflow/api/v2/monitor/health"
    interval  = 5
    threshold = 60
  }
}

# The Ghostty app (terminal_app ghostty): a terminal app, so it opens where and as the Terminal did - Coder's terminal
# page, in its own window, a login shell in the Start folder - and the dashboard draws an agent's terminals with
# Ghostty's emulator wherever the agent has this app (slug "ghostty").
resource "coder_app" "ghostty" {
  count        = var.terminal_app == "ghostty" ? 1 : 0
  agent_id     = coder_agent.main.id
  slug         = "ghostty"
  display_name = "Ghostty"
  icon         = "/__icons/ghostty.png" # files/icons/ghostty.png (Ghostty's app icon, MIT), served by templates/icons.yaml
  command      = "cd ${jsonencode(data.coder_parameter.start_directory.value)} 2>/dev/null; exec \"$SHELL\" -l"
  share        = "owner"
}

# Before 1.40.1 these were in main.tf with a count (one per DATAFLOW workspace): the same objects.
moved {
  from = coder_app.nifi[0]
  to   = coder_app.nifi
}

moved {
  from = coder_script.nifi[0]
  to   = coder_script.nifi
}

moved {
  from = coder_script.airflow[0]
  to   = coder_script.airflow
}

moved {
  from = kubernetes_manifest.nifi_route[0]
  to   = kubernetes_manifest.nifi_route
}

moved {
  from = coder_app.airflow[0]
  to   = coder_app.airflow
}

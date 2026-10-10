# Legacy template of the upgrade rehearsal: a plain Kubernetes workspace pinned to providers the bundle doesn't carry.
terraform {
  required_providers {
    coder      = { source = "coder/coder", version = "2.18.0" }
    kubernetes = { source = "hashicorp/kubernetes", version = "~> 2.38" }
    random     = { source = "hashicorp/random", version = "3.7.2" }
  }
}
variable "namespace" { default = "coder" }
variable "image" { default = "" }
data "coder_workspace" "me" {}
data "coder_workspace_owner" "me" {}
resource "random_id" "suffix" { byte_length = 2 }
resource "coder_agent" "main" {
  os   = "linux"
  arch = "amd64"
}
resource "kubernetes_pod" "main" {
  count = data.coder_workspace.me.start_count
  metadata {
    name      = "legacy-${data.coder_workspace.me.id}-${random_id.suffix.hex}"
    namespace = var.namespace
    labels = {
      "com.coder.workspace.id"   = data.coder_workspace.me.id
      "com.coder.workspace.name" = data.coder_workspace.me.name
    }
  }
  spec {
    container {
      name    = "dev"
      image   = var.image
      command = ["sh", "-c", coder_agent.main.init_script]
      env {
        name  = "CODER_AGENT_TOKEN"
        value = coder_agent.main.token
      }
      # the shared CA (like any real template here): curl (agent download) and the agent trust Coder's address
      env {
        name  = "CURL_CA_BUNDLE"
        value = "/etc/company-ca/ca-bundle.crt"
      }
      env {
        name  = "SSL_CERT_FILE"
        value = "/etc/company-ca/ca-bundle.crt"
      }
      volume_mount {
        name       = "company-ca"
        mount_path = "/etc/company-ca"
        read_only  = true
      }
    }
    volume {
      name = "company-ca"
      secret {
        secret_name = "company-ca-bundle"
      }
    }
  }
}

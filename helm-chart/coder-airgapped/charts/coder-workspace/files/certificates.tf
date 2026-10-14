# Installs the owner's PKCS#12 certificate into the workspace as ~/.cert/<owner>.p12, where <owner> is the Coder
# username (for user jdoe: /home/coder/.cert/jdoe.p12). Nothing else is left in ~/.cert.
#
# Drop this file next to a template's main.tf (Terraform reads every .tf file in the template directory). It needs a
# `coder_agent` named "main"; change agent_id below if yours is called something else.
#
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

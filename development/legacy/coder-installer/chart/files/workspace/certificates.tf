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
  icon               = "/icon/lock.svg"
  run_on_start       = true
  start_blocks_login = true # nobody gets a shell before the certificate is in place
  script             = <<-EOT
    #!/bin/sh
    set -eu
    umask 077
    owner='${data.coder_workspace_owner.me.name}'
    case "$owner" in
      ""|*/*|.*|*[!A-Za-z0-9._@+-]*) echo "Unexpected workspace owner name: $owner" >&2; exit 1 ;;
    esac
    dir="$HOME/.cert"
    target="$dir/$owner.p12"
    state="$${XDG_STATE_HOME:-$HOME/.local/state}/coder-certificates"
    mkdir -p "$dir" "$state"
    chmod 700 "$dir" "$state"
    installed="$state/installed" # names this script installed on earlier starts (kept out of ~/.cert)
    if [ -f "$dir/.installed-by-coder" ]; then # the earlier version kept that list in ~/.cert itself
      cat "$dir/.installed-by-coder" >> "$installed"
      rm -f "$dir/.installed-by-coder"
    fi

    # The owner's upload, or failing that the first one saved under another name. Only one is installed.
    source=""
    if [ -f "$dir/.$owner.p12.b64" ]; then source="$dir/.$owner.p12.b64"; fi
    for encoded in "$dir"/.*.p12.b64; do
      [ -f "$encoded" ] || continue
      if [ -z "$source" ]; then
        source="$encoded"
      elif [ "$encoded" != "$source" ]; then
        echo "Ignoring $(basename "$encoded"): only one certificate is installed, as ~/.cert/$owner.p12" >&2
        rm -f "$encoded"
      fi
    done

    ok=""
    if [ -n "$source" ]; then
      tmp=$(mktemp "$dir/.install.XXXXXX")
      if base64 -d < "$source" > "$tmp" 2>/dev/null && [ -s "$tmp" ]; then
        chmod 600 "$tmp"
        mv -f "$tmp" "$target"
        ok=1
        echo "Installed ~/.cert/$owner.p12"
      else
        rm -f "$tmp"
        echo "Skipping $(basename "$source"): not a valid upload" >&2
      fi
      rm -f "$source" # the agent writes it again on the next start; the installed .p12 is the only copy kept
    fi

    # Files installed on earlier starts under other names, and this one if the owner has deleted it in Coder.
    if [ -f "$installed" ]; then
      while IFS= read -r old; do
        case "$old" in ""|*/*|.*) continue ;; esac
        if [ "$old" = "$owner" ] && [ -n "$ok" ]; then continue; fi
        if [ -f "$dir/$old.p12" ]; then rm -f "$dir/$old.p12" && echo "Removed ~/.cert/$old.p12"; fi
      done < "$installed"
    fi
    # An empty ~/.cert/<owner>.p12 left behind by the template's former paste-a-certificate parameter.
    if [ -f "$target" ] && [ ! -s "$target" ]; then rm -f "$target"; fi

    if [ -n "$ok" ]; then echo "$owner" > "$installed"; else rm -f "$installed"; fi
  EOT
}

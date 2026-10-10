#!/bin/bash

set -euo pipefail

export SSL_CERT_FILE=~/.config/coderv2/ca-bundle.pem

coder templates push ubuntu-vnc-desktop \
 -d /tmp/claude-1000/-home-xxx-devbox-data-apps-argocd-coder/28b2193c-5d1a-48eb-99bc-246251a95aa3/scratchpad/tpl-v3 \
 --variable namespace=coder \
 --message "Only ~/.cert/<username>.p12; namespace locked to coder" \
 --yes

bash /tmp/claude-1000/-home-xxx-devbox-data-apps-argocd-coder/28b2193c-5d1a-48eb-99bc-246251a95aa3/scratchpad/cleanup-orphans.sh --apply

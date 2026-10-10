# Sourced (not executed) by plugin-lib.sh, banner-guide and preflight-banner.
#
# What Traefik itself says about its plugins. Traefik prints it once, at start-up, and a rollout only proves the
# pod is Ready: after a plugin problem (e.g. the same plugin name defined twice - "the plugin's name must be
# unique") Traefik still becomes Ready but DISABLES ALL PLUGINS, every router that uses a plugin Middleware is
# dropped, and the banner silently stops while Coder itself keeps answering.
#
#   plugin_health <namespace> <deployment>   prints  loaded | disabled | unknown
#                                            (for "disabled" a second line gives Traefik's own reason)
#
# It reads the START of the log (--limit-bytes returns the first bytes, so a busy log cannot push the line out).
plugin_health() {
 local log=""
 for _ in 1 2 3 4 5 6; do
  log=$(kubectl -n "$1" logs "deployment/$2" --limit-bytes=200000 2>/dev/null || true)
  grep -qE 'Plugins loaded|Plugins are disabled' <<<"${log}" && break
  sleep 2
 done
 if grep -q 'Plugins are disabled' <<<"${log}"; then
  echo disabled
  awk '/Plugins are disabled/ && !done { gsub(/\x1b\[[0-9;]*m/, ""); print substr($0, 1, 240); done = 1 }' <<<"${log}"
 elif grep -q 'Plugins loaded' <<<"${log}"; then echo loaded
 else echo unknown; fi
}

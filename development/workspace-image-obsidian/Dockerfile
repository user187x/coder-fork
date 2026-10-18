# The workspace image of the Obsidian template (ubuntu-obsidian): the ubuntu-vnc-desktop image (../workspace-image) and
# Obsidian (/opt/Obsidian, from the obsidian.deb kept untracked at the repository's root), its window on the
# workspace's desktop: the template's Obsidian app. Started without Chromium's sandbox (a pod has no user namespaces;
# its SUID helper is removed, as nothing may gain privileges in a pod) and with its automatic updates off (no internet:
# the image is its version). Obsidian's own terms apply: the image is for the organization's own clusters, never
# pushed to a public registry or attached to a public release (build-bundle.sh --no-obsidian).
# Built by ../tools/build-obsidian-image.sh, which puts the package beside this file for the build (checked against
# its SHA-256).
ARG BASE=ghcr.io/user187x/coder-workspace:1.8.7
FROM ${BASE}
ARG OBSIDIAN_VERSION=1.14.4
USER root

# Obsidian: the package with its dependencies from the distribution; the package is mounted for the build only.
RUN --mount=type=bind,source=obsidian.deb,target=/tmp/obsidian.deb \
    apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends /tmp/obsidian.deb \
 && rm -rf /var/lib/apt/lists/* \
 && dpkg-query -W -f '${Version}\n' obsidian | grep -qx "${OBSIDIAN_VERSION}" \
 && rm -f /opt/Obsidian/chrome-sandbox \
 && rm -f /etc/apparmor.d/obsidian \
 && sed -i 's|^Exec=.*|Exec=/usr/local/bin/obsidian-app %U|' /usr/share/applications/md.obsidian.Obsidian.desktop

# The template's helpers: obsidian-app (Obsidian as a pod runs it), coder-obsidian-start (at every start: the vault,
# Obsidian's settings, the app on the desktop).
COPY --chmod=755 bin/ /usr/local/bin/

# What is in this image, beside the base's (the Images view's add-ons list).
RUN python3 - "${OBSIDIAN_VERSION}" <<'PY'
import json, sys
p = "/usr/share/coder-workspace/addons.json"
d = json.load(open(p))
d["software"] += [{"name": "Obsidian", "what": "Notes in a vault of Markdown files: the app on the desktop "
                   "(/opt/Obsidian; internal use)", "group": "Apps", "version": sys.argv[1]}]
json.dump(d, open(p, "w"), indent=1)
PY

USER coder

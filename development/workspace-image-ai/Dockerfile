# The workspace image of the AI templates (ubuntu-lmstudio and ubuntu-opencode): the ubuntu-vnc-desktop image
# (../workspace-image) and
#   - LM Studio, the desktop app (/opt/lm-studio, the AppImage unpacked, read-only): its window on the workspace's
#     desktop, its local server (OpenAI-compatible, 127.0.0.1:1234) and the lms command. Its llama.cpp engines (CPU,
#     NVIDIA CUDA, Vulkan) are the app's own: each workspace's ~/.lmstudio links to them instead of unpacking a copy.
#     LM Studio's terms allow personal and internal business use only: this image is for the organization's own
#     clusters, never published (../tools/build-bundle.sh --no-lmstudio leaves it out of a bundle);
#   - OpenCode (MIT), the AI coding agent (/usr/local/bin/opencode), with everything that would reach the internet off:
#     no update check, no model catalogue download, no language-server downloads, no sharing, no default plugins.
# Built by ../tools/build-ai-image.sh, which downloads both beforehand (pinned, checked against their SHA-256): the
# build needs no network.
ARG BASE=ghcr.io/user187x/coder-workspace:1.8.5
FROM ${BASE}
ARG LMSTUDIO_VERSION=0.4.26-4
ARG OPENCODE_VERSION=1.18.34
USER root

# LM Studio: the AppImage unpacked (no FUSE in a pod), started with --no-sandbox (no user namespaces in a pod either);
# lms on the PATH; its menu entry on the desktop. The archive is mounted for the build only: no layer keeps it.
RUN --mount=type=bind,source=LM-Studio-${LMSTUDIO_VERSION}-x64.AppImage,target=/tmp/lm-studio.AppImage \
    cd /tmp && cp /tmp/lm-studio.AppImage /tmp/lms.AppImage && chmod +x /tmp/lms.AppImage \
 && /tmp/lms.AppImage --appimage-extract >/dev/null \
 && mv /tmp/squashfs-root /opt/lm-studio && rm -f /tmp/lms.AppImage \
 && rm -f /opt/lm-studio/chrome-sandbox \
 && chmod -R a+rX,go-w /opt/lm-studio \
 && test -x /opt/lm-studio/lm-studio && test -x /opt/lm-studio/resources/app/.webpack/lms \
 && ln -s /opt/lm-studio/resources/app/.webpack/lms /usr/local/bin/lms \
 && printf '#!/bin/sh\nexec /opt/lm-studio/lm-studio --no-sandbox "$@"\n' >/usr/local/bin/lm-studio \
 && chmod 755 /usr/local/bin/lm-studio \
 && sed -e 's|^Exec=.*|Exec=/usr/local/bin/lm-studio %U|' -e 's|^Name=.*|Name=LM Studio|' \
        -e 's|^Icon=.*|Icon=/opt/lm-studio/lm-studio.png|' /opt/lm-studio/ai.elementlabs.lmstudio.desktop \
        >/usr/share/applications/lm-studio.desktop

# OpenCode: the single binary, and its settings for a cluster without internet (the template's provider is the
# workspace's own LM Studio: its startup script writes the configuration).
RUN --mount=type=bind,source=opencode-${OPENCODE_VERSION}-linux-x64-baseline.tar.gz,target=/tmp/opencode.tar.gz \
    tar -xzf /tmp/opencode.tar.gz -C /usr/local/bin opencode \
 && chmod 755 /usr/local/bin/opencode \
 && /usr/local/bin/opencode --version
ENV OPENCODE_DISABLE_AUTOUPDATE=1 \
    OPENCODE_DISABLE_MODELS_FETCH=1 \
    OPENCODE_DISABLE_LSP_DOWNLOAD=1 \
    OPENCODE_DISABLE_SHARE=1 \
    OPENCODE_DISABLE_DEFAULT_PLUGINS=1

# The templates' helpers (they know LM Studio's layout, so they come with it): coder-lmstudio-start (at every
# start: the links, the models, the app, its server, OpenCode's settings), coder-lmstudio-home, coder-ai-models,
# coder-opencode-config.
COPY --chmod=755 bin/ /usr/local/bin/

# What is in this image, beside the base's (the Images view's add-ons list).
RUN python3 - "${LMSTUDIO_VERSION}" "${OPENCODE_VERSION}" <<'PY'
import json, sys
p = "/usr/share/coder-workspace/addons.json"
d = json.load(open(p))
d["software"] += [{"name": "LM Studio", "what": "Local language models: the app on the desktop, its server on "
                   "127.0.0.1:1234 and lms (/opt/lm-studio; internal use only)", "group": "Apps", "version": sys.argv[1]},
                  {"name": "OpenCode", "what": "The AI coding agent, on the workspace's LM Studio (opencode)",
                   "group": "Apps", "version": sys.argv[2]}]
json.dump(d, open(p, "w"), indent=1)
PY

USER coder

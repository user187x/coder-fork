# The workspace image of the AI templates (ubuntu-unsloth and ubuntu-opencode): the ubuntu-vnc-desktop image
# (../workspace-image) and
#   - Unsloth Studio (AGPL-3.0): local language models - chat with GGUF models through llama.cpp, fine-tuning on NVIDIA
#     GPUs, datasets, exports - and its OpenAI-compatible API (127.0.0.1:8888/v1). Its desktop app (the unsloth.AppImage
#     at the repository's root, unpacked in /opt/unsloth-app, read-only) is its window on the workspace's desktop and
#     runs its server. The server itself is installed here, by the installer the app carries (its install.sh), with
#     everything it would download at a first start: PyTorch for NVIDIA GPUs (CUDA 12.8, Turing and newer; it runs on
#     the CPU just as well), llama.cpp's CUDA build (its CPU backends load where there is no GPU), Node, whisper.cpp
#     and audio.cpp. It lives in /opt/unsloth (HOME=/opt/unsloth at build time); each workspace's ~/.unsloth links to
#     it and keeps only its own data there (chats, settings, outputs, exports). No network at run time: no update
#     checks, Hugging Face offline;
#   - OpenCode (MIT), the AI coding agent (/usr/local/bin/opencode), with everything that would reach the internet off:
#     no update check, no model catalogue download, no language-server downloads, no sharing, no default plugins.
#     `unsloth start opencode` points it at the workspace's Unsloth.
# Built by ../tools/build-ai-image.sh, which puts the AppImage and OpenCode's archive (pinned, checked against their
# SHA-256) beside this file; the build downloads Unsloth Studio's Python packages, llama.cpp and Node (pinned by the
# installer of that AppImage).
ARG BASE=ghcr.io/user187x/coder-workspace:1.8.7
FROM ${BASE}
ARG OPENCODE_VERSION=1.18.34
# PyTorch's wheel index and llama.cpp's backend: CUDA, whatever the build machine has (--build-arg ...=cpu for a
# smaller, CPU-only image).
ARG UNSLOTH_TORCH_INDEX_URL=https://download.pytorch.org/whl/cu128
ARG UNSLOTH_LLAMA_CPP_BACKEND=cuda
USER root

# The desktop app's libraries the desktop image lacks (WebKitGTK's Wayland server library; the app carries the rest).
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends libwayland-server0 \
 && rm -rf /var/lib/apt/lists/*

# The desktop app: the AppImage unpacked (no FUSE in a pod); started by /usr/local/bin/unsloth-studio-app with
# WebKit's sandbox and GPU compositing off (no user namespaces, no GPU for the display in a pod); its menu entry. The
# archive is mounted for the build only: no layer keeps it.
RUN --mount=type=bind,source=unsloth.AppImage,target=/tmp/unsloth.AppImage \
    cd /tmp && cp /tmp/unsloth.AppImage /tmp/u.AppImage && chmod +x /tmp/u.AppImage \
 && /tmp/u.AppImage --appimage-extract >/dev/null \
 && mv /tmp/squashfs-root /opt/unsloth-app && rm -f /tmp/u.AppImage \
 && chmod -R a+rX,go-w /opt/unsloth-app \
 && test -x /opt/unsloth-app/usr/bin/unsloth-studio && test -f /opt/unsloth-app/usr/lib/Unsloth/install.sh \
 && sed -e 's|^Exec=.*|Exec=/usr/local/bin/unsloth-studio-app %u|' -e 's|^Icon=.*|Icon=/opt/unsloth-app/unsloth-studio.png|' \
        /opt/unsloth-app/Unsloth.desktop >/usr/share/applications/unsloth-studio.desktop

# Unsloth Studio's server, installed as the workspace user into /opt/unsloth by the app's own installer, then its
# download cache dropped (the virtual environment keeps its files). `unsloth` on the PATH.
RUN install -d -o coder -g coder /opt/unsloth \
 && su coder -s /bin/sh -c "cd /opt/unsloth && HOME=/opt/unsloth UNSLOTH_SKIP_AUTOSTART=1 \
      UNSLOTH_TORCH_INDEX_URL='${UNSLOTH_TORCH_INDEX_URL}' UNSLOTH_LLAMA_CPP_BACKEND='${UNSLOTH_LLAMA_CPP_BACKEND}' \
      sh /opt/unsloth-app/usr/lib/Unsloth/install.sh --tauri" \
 && rm -rf /opt/unsloth/.unsloth/studio/cache /opt/unsloth/.cache \
 && test -x /opt/unsloth/.unsloth/studio/unsloth_studio/bin/unsloth \
 && test -x /opt/unsloth/.unsloth/llama.cpp/llama-server \
 && ln -s /opt/unsloth/.unsloth/studio/unsloth_studio/bin/unsloth /usr/local/bin/unsloth
ENV UNSLOTH_DISABLE_UPDATE_CHECK=1 \
    UNSLOTH_TAURI_UPDATE=0 \
    UNSLOTH_DISABLE_LLM_COMPRESSOR_AUTOINSTALL=1 \
    UNSLOTH_DISABLE_DIFFUSERS_AUTOREPAIR=1 \
    HF_HUB_OFFLINE=1 \
    HF_HUB_DISABLE_TELEMETRY=1 \
    DO_NOT_TRACK=1

# OpenCode: the single binary, and its settings for a cluster without internet (`unsloth start opencode` writes its
# provider: the workspace's Unsloth).
RUN --mount=type=bind,source=opencode-${OPENCODE_VERSION}-linux-x64-baseline.tar.gz,target=/tmp/opencode.tar.gz \
    tar -xzf /tmp/opencode.tar.gz -C /usr/local/bin opencode \
 && chmod 755 /usr/local/bin/opencode \
 && /usr/local/bin/opencode --version
ENV OPENCODE_DISABLE_AUTOUPDATE=1 \
    OPENCODE_DISABLE_MODELS_FETCH=1 \
    OPENCODE_DISABLE_LSP_DOWNLOAD=1 \
    OPENCODE_DISABLE_SHARE=1 \
    OPENCODE_DISABLE_DEFAULT_PLUGINS=1

# The templates' helpers (they know Unsloth's layout, so they come with it): coder-unsloth-start (at every start: the
# home's links, the app on the desktop, its server), coder-unsloth-home, unsloth-studio-app, coder-opencode (the
# OpenCode app: a model loaded, OpenCode on it).
COPY --chmod=755 bin/ /usr/local/bin/

# What is in this image, beside the base's (the Images view's add-ons list).
RUN python3 - "$(unsloth --version 2>/dev/null | tail -n 1 || true)" "${OPENCODE_VERSION}" \
      "$(cat /opt/unsloth/.unsloth/llama.cpp/BUILD_INFO.txt 2>/dev/null | sed -n 's/^llama.cpp version: //p')" <<'PY'
import json, sys
p = "/usr/share/coder-workspace/addons.json"
d = json.load(open(p))
d["software"] += [{"name": "Unsloth Studio", "what": "Local language models: chat, fine-tuning, datasets; the app on the "
                   "desktop and its OpenAI-compatible server on 127.0.0.1:8888 (/opt/unsloth, AGPL-3.0)",
                   "group": "Apps", "version": sys.argv[1] or "installed"},
                  {"name": "llama.cpp (Unsloth)", "what": "Unsloth's GGUF inference (CUDA, with CPU fallback)",
                   "group": "Apps", "version": sys.argv[3] or "installed"},
                  {"name": "OpenCode", "what": "The AI coding agent, on the workspace's Unsloth (opencode)",
                   "group": "Apps", "version": sys.argv[2]}]
json.dump(d, open(p, "w"), indent=1)
PY

USER coder

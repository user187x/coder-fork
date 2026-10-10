# Workspace image for air-gapped clusters: everything the bundled template's startup script used to download at
# every workspace start is installed here instead (code-server, File Browser, marimo, XFCE + TigerVNC + noVNC).
# The template still falls back to downloading a tool only if it is missing, so the stock
# codercom/enterprise-base image keeps working on connected clusters.
#
#   docker build -t coder-platform/workspace:<tag> images/workspace
ARG BASE_IMAGE=codercom/enterprise-base:ubuntu@sha256:feefd9a4da419d69b98dce3bce6b9a36030f074b61b5e1bdb7239af1bff14e85
FROM ${BASE_IMAGE}

ARG CODE_SERVER_VERSION=4.139.1
ARG FILEBROWSER_VERSION=2.63.23
ARG MARIMO_VERSION=0.24.2
ARG TARGETARCH=amd64

USER root
ENV DEBIAN_FRONTEND=noninteractive

# Desktop (XFCE on TigerVNC, served to the browser by noVNC + websockify).
RUN apt-get update \
 && apt-get install -y --no-install-recommends xfce4 xfce4-goodies dbus-x11 tigervnc-standalone-server novnc websockify \
 && ln -sf /usr/share/novnc/vnc.html /usr/share/novnc/index.html \
 && apt-get clean && rm -rf /var/lib/apt/lists/*

# code-server (standalone release: its own Node.js, no npm at runtime).
RUN mkdir -p /opt/code-server \
 && curl -fsSL "https://github.com/coder/code-server/releases/download/v${CODE_SERVER_VERSION}/code-server-${CODE_SERVER_VERSION}-linux-${TARGETARCH}.tar.gz" \
    | tar -xz --strip-components=1 -C /opt/code-server \
 && /opt/code-server/bin/code-server --version

# File Browser.
RUN curl -fsSL "https://github.com/filebrowser/filebrowser/releases/download/v${FILEBROWSER_VERSION}/linux-${TARGETARCH}-filebrowser.tar.gz" \
    | tar -xz -C /usr/local/bin filebrowser \
 && filebrowser version

# marimo in its own virtualenv outside /home (the home directory is the workspace's volume). Its home page
# "Resources" section (links to the internet) is hidden here once, instead of by the startup script.
RUN python3 -m venv /opt/marimo-venv \
 && /opt/marimo-venv/bin/pip install --no-cache-dir --quiet "marimo==${MARIMO_VERSION}" \
 && idx=$(/opt/marimo-venv/bin/python -c 'import marimo, os; print(os.path.join(os.path.dirname(marimo.__file__), "_static", "index.html"))') \
 && sed -i 's#</head>#<style id="marimo-hide-resources">div.flex.flex-col.gap-2:has(> div.grid.gap-3.lg\\:grid-cols-3 > a[target="_blank"]){display:none !important}</style></head>#' "$idx" \
 && grep -q marimo-hide-resources "$idx" \
 && test "$(/opt/marimo-venv/bin/marimo --version)" = "${MARIMO_VERSION}"

LABEL org.opencontainers.image.title="coder-platform workspace" \
      io.coder-platform.code-server="${CODE_SERVER_VERSION}" \
      io.coder-platform.filebrowser="${FILEBROWSER_VERSION}" \
      io.coder-platform.marimo="${MARIMO_VERSION}"
USER coder

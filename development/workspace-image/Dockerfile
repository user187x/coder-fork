# The workspace image of the ubuntu-vnc-desktop template: Coder's enterprise-base with, preinstalled,
#   - the desktop the template starts (XFCE, TigerVNC, noVNC, websockify) - no apt-get at workspace start, so it
#     also works without internet. light-locker and LightDM are left out: the screen locker needs a display manager,
#     crashes in a container at every session start and leaves core.<pid> files in the user's home directory.
#     The XFCE screensaver is not started either (it would lock the VNC desktop behind a password the user lacks);
#   - JetBrains Mono Nerd Font (fonts/), the default monospace and desktop font (fontconfig, XFCE, its terminal);
#   - tmux 3.8 with the bundled configuration (packages/tmux-installation.deb, package tmux-mirror); the template
#     runs tmux-mirror-setup for users who have no tmux configuration yet;
#   - command-line tools: btop, bat (Ubuntu's binary is batcat; bat links to it), vim, eza, fzf, grc, jq, yq (Ubuntu's:
#     the jq wrapper for YAML, kislyuk/yq);
#   - k9s (the developer's own build and configuration) and kubectl: packages/k8s-devtools_*.deb, made by
#     ../tools/build-k8s-devtools.sh (pinned by packages/SHA256SUMS), with bash completion
#     (enabled for every interactive shell in /etc/bash.bashrc);
#   - marimo (Python notebooks) with everything it needs, so it works with no network at all: /opt/marimo, a Python
#     environment with marimo and its extras (SQL, charts, formatting, language server, uv) and a data stack, pinned in
#     marimo/requirements.lock; tkinter (python3-tk). The template gives each user a ~/.marimo-venv on top of it;
#   - the default shell setup (shell/): ~/.bashrc (/etc/skel and /usr/share/coder-workspace/bashrc) and the vimrc it
#     points Vim at; the template copies them into homes that have none (or still Ubuntu's stock .bashrc).
# Build with ../tools/build-workspace-image.sh, which saves it into the delivery bundle.
ARG BASE=codercom/enterprise-base:ubuntu
FROM ${BASE}

USER root
ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
	&& apt-get install -y xfce4 xfce4-goodies xfce4-terminal dbus-x11 tigervnc-standalone-server novnc websockify \
		fontconfig light-locker- lightdm- lightdm-gtk-greeter- \
	&& ln -sf /usr/share/novnc/vnc.html /usr/share/novnc/index.html \
	&& rm -f /etc/xdg/autostart/light-locker.desktop /etc/xdg/autostart/xfce4-screensaver.desktop \
	&& rm -rf /var/lib/apt/lists/*

# Command-line tools.
RUN apt-get update \
	&& apt-get install -y --no-install-recommends btop bat vim eza fzf grc jq yq \
	&& ln -sf /usr/bin/batcat /usr/local/bin/bat \
	&& rm -rf /var/lib/apt/lists/*

# Default shell setup. Ubuntu's stock .bashrc is kept, so the template can tell an untouched one from the user's own.
COPY shell/bashrc shell/vimrc /usr/share/coder-workspace/
RUN cp /etc/skel/.bashrc /usr/share/coder-workspace/ubuntu-bashrc \
	&& cp /usr/share/coder-workspace/bashrc /etc/skel/.bashrc \
	&& chmod 0644 /usr/share/coder-workspace/* /etc/skel/.bashrc

# JetBrains Mono Nerd Font: installed, and preferred for "monospace".
COPY fonts/ /usr/share/fonts/truetype/jetbrains-mono-nerd/
COPY fontconfig/60-jetbrains-mono-nerd.conf /etc/fonts/conf.d/60-jetbrains-mono-nerd.conf
RUN fc-cache -f >/dev/null && fc-match monospace | grep -q 'JetBrainsMono'

# XFCE defaults (users without their own settings): the desktop and terminal fonts, and the desktop background
# (XFCE's dark cp-dark wallpaper).
COPY xfce/ /etc/xdg/xfce4/

# tmux 3.8 + configuration (self-contained package; Recommends are not needed).
COPY packages/tmux-installation.deb /tmp/tmux-installation.deb
RUN apt-get update \
	&& apt-get install -y --no-install-recommends /tmp/tmux-installation.deb \
	&& rm -rf /tmp/tmux-installation.deb /var/lib/apt/lists/* \
	&& /usr/local/bin/tmux -V

# k9s, kubectl (k8s-devtools) and bash completion for every interactive shell.
COPY packages/k8s-devtools_*.deb /tmp/
RUN apt-get update \
	&& apt-get install -y --no-install-recommends bash-completion /tmp/k8s-devtools_*.deb \
	&& rm -rf /tmp/k8s-devtools_*.deb /var/lib/apt/lists/* \
	&& printf '%s\n' '' '# Bash completion (workspace image).' \
		'if ! shopt -oq posix && [ -f /usr/share/bash-completion/bash_completion ]; then' \
		'  . /usr/share/bash-completion/bash_completion' 'fi' >>/etc/bash.bashrc \
	&& k9s version -s && kubectl version --client

# marimo, offline: everything installed at build time (marimo/requirements.lock, pinned by build-workspace-image.sh).
# Its home page's "Resources" section (external links, useless offline) is hidden with a CSS rule in its index.html.
COPY marimo/requirements.lock /tmp/marimo-requirements.lock
RUN apt-get update \
	&& apt-get install -y --no-install-recommends python3-venv python3-tk \
	&& rm -rf /var/lib/apt/lists/* \
	&& python3 -m venv /opt/marimo \
	&& /opt/marimo/bin/pip install --no-cache-dir --disable-pip-version-check -r /tmp/marimo-requirements.lock \
	&& rm -f /tmp/marimo-requirements.lock \
	&& index="$(/opt/marimo/bin/python -c 'import marimo, os; print(os.path.join(os.path.dirname(marimo.__file__), "_static", "index.html"))')" \
	&& sed -i 's#</head>#<style id="marimo-hide-resources">div.flex.flex-col.gap-2:has(> div.grid.gap-3.lg\\:grid-cols-3 > a[target="_blank"]){display:none !important}</style></head>#' "$index" \
	&& grep -q marimo-hide-resources "$index" \
	&& /opt/marimo/bin/marimo --version

USER coder

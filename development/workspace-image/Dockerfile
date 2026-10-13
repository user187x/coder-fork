# The workspace image of the ubuntu-vnc-desktop template: Coder's enterprise-base with, preinstalled,
#   - the desktop the template starts (XFCE, TigerVNC, noVNC, websockify) - no apt-get at workspace start, so it
#     also works without internet. light-locker and LightDM are left out: the screen locker needs a display manager,
#     crashes in a container at every session start and leaves core.<pid> files in the user's home directory.
#     The XFCE screensaver is not started either (it would lock the VNC desktop behind a password the user lacks);
#   - Firefox (Mozilla's .deb, desktop/firefox-policies.json), XFCE's default web browser (desktop/);
#     XFCE's compositor is off by default (desktop/xfwm4.xml), which makes the desktop faster over VNC;
#   - JetBrains Mono Nerd Font (fonts/), the default monospace and desktop font (fontconfig, XFCE, its terminal);
#   - tmux 3.8 with the bundled configuration (packages/tmux-installation.deb, package tmux-mirror); the template
#     runs tmux-mirror-setup for users who have no tmux configuration yet;
#   - command-line tools: btop, bat (Ubuntu's binary is batcat; bat links to it), vim, eza, fzf, grc, jq, yq (Ubuntu's:
#     the jq wrapper for YAML, kislyuk/yq);
#   - k9s (the developer's own build and configuration), kubectl and kubelogin (kubectl oidc-login, the admins'
#     Keycloak sign-in to the cluster): packages/k8s-devtools_*.deb, made by
#     ../tools/build-k8s-devtools.sh (pinned by packages/SHA256SUMS), with bash completion
#     (enabled for every interactive shell in /etc/bash.bashrc);
#   - marimo (Python notebooks) with everything it needs, so it works with no network at all: /opt/marimo, a Python
#     environment with marimo and its extras (SQL, charts, formatting, language server, uv) and a data stack, pinned in
#     marimo/requirements.lock; tkinter (python3-tk). The template gives each user a ~/.marimo-venv on top of it.
#     marimo opens its own pages in the same window (marimo/same-window.js);
#   - the default shell setup (shell/): ~/.bashrc (/etc/skel and /usr/share/coder-workspace/bashrc) and the vimrc it
#     points Vim at; the template copies them into homes that have none (or still Ubuntu's stock .bashrc);
#   - code-server and File Browser, the template's two web apps (pinned below, checked by SHA-256): the template used
#     to download them at every workspace start, which fails without internet;
#   - /usr/share/coder-workspace/addons.json: what it adds over the base, with every version (addons/addons.py), for
#     the Images view, which lists it next to the image's security report.
# Build with ../tools/build-workspace-image.sh, which saves it into the delivery bundle.
ARG BASE=codercom/enterprise-base:ubuntu
FROM ${BASE}

USER root
ARG DEBIAN_FRONTEND=noninteractive
ARG BASE

# The base's packages, before anything is added (addons/addons.py, the last step, tells what this image adds).
RUN install -d /usr/share/coder-workspace \
	&& dpkg-query -W -f='${Package}\t${Version}\n' >/usr/share/coder-workspace/.base-packages \
	&& apt-mark showmanual >/usr/share/coder-workspace/.base-manual

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

# k9s, kubectl, kubelogin (k8s-devtools) and bash completion for every interactive shell.
COPY packages/k8s-devtools_*.deb /tmp/
RUN apt-get update \
	&& apt-get install -y --no-install-recommends bash-completion /tmp/k8s-devtools_*.deb \
	&& rm -rf /tmp/k8s-devtools_*.deb /var/lib/apt/lists/* \
	&& printf '%s\n' '' '# Bash completion (workspace image).' \
		'if ! shopt -oq posix && [ -f /usr/share/bash-completion/bash_completion ]; then' \
		'  . /usr/share/bash-completion/bash_completion' 'fi' >>/etc/bash.bashrc \
	&& k9s version -s && kubectl version --client && kubectl oidc-login --version

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

# Firefox, the desktop's web browser (XFCE's "Web Browser" and the dock's launcher open it): Mozilla's own .deb
# (Ubuntu's firefox package only installs the snap, which cannot run in a container), with policies for an offline
# network (no updates, telemetry, studies, Pocket, first-run pages or online new-tab content: new tabs and the start
# page are blank) that trust the system CA store (p11-kit) - the
# template's bundle, which includes the cluster's own CA. Also XFCE's compositor off by default (desktop/xfwm4.xml):
# over VNC every composited effect is extra drawing to encode and send.
COPY desktop/firefox-policies.json /etc/firefox/policies/policies.json
COPY desktop/mimeapps.list /etc/xdg/mimeapps.list
COPY desktop/helpers.rc /etc/xdg/xfce4/helpers.rc
COPY desktop/xfwm4.xml /etc/xdg/xfce4/xfconf/xfce-perchannel-xml/xfwm4.xml
RUN install -d -m 0755 /etc/apt/keyrings \
	&& curl -fsSL https://packages.mozilla.org/apt/repo-signing-key.gpg -o /etc/apt/keyrings/packages.mozilla.org.asc \
	&& GNUPGHOME="$(mktemp -d)" gpg -n -q --import --import-options import-show /etc/apt/keyrings/packages.mozilla.org.asc \
		| grep -q 35BAA0B33E9EB396F59CA838C0BA5CE6DC6315A3 \
	&& echo 'deb [signed-by=/etc/apt/keyrings/packages.mozilla.org.asc] https://packages.mozilla.org/apt mozilla main' \
		>/etc/apt/sources.list.d/mozilla.list \
	&& printf '%s\n' 'Package: *' 'Pin: origin packages.mozilla.org' 'Pin-Priority: 1000' >/etc/apt/preferences.d/mozilla \
	&& apt-get update \
	&& apt-get install -y --no-install-recommends firefox p11-kit-modules \
	&& rm -rf /var/lib/apt/lists/* \
	&& { update-alternatives --set x-www-browser /usr/bin/firefox 2>/dev/null || true; }
RUN firefox --version && test -f /usr/lib/x86_64-linux-gnu/pkcs11/p11-kit-trust.so

# marimo opens its own pages in the same window (marimo/same-window.js), so embedded in the dashboard's app panel
# it stays in the panel instead of opening new browser windows.
COPY marimo/same-window.js /tmp/marimo-same-window.js
RUN index="$(/opt/marimo/bin/python -c 'import marimo, os; print(os.path.join(os.path.dirname(marimo.__file__), "_static", "index.html"))')" \
	&& /opt/marimo/bin/python -c 'import sys; p, js = sys.argv[1], open(sys.argv[2]).read(); s = open(p).read(); s = s.replace("<head>", "<head><script id=\"marimo-same-window\">" + js + "</script>", 1); open(p, "w").write(s)' "$index" /tmp/marimo-same-window.js \
	&& grep -q marimo-same-window "$index" && rm -f /tmp/marimo-same-window.js

# code-server (VS Code in the browser) and File Browser: downloaded here, at build time only, and checked. The
# template starts these; it downloads them itself only on an image without them (and only with internet).
ARG CODE_SERVER_VERSION=4.141.0
ARG CODE_SERVER_SHA256=65bcd20b524d03eb1b5adb7f360244599ef0e5b74089a46a982d32c074b97a29
ARG FILEBROWSER_VERSION=2.63.23
ARG FILEBROWSER_SHA256=b14db2bb8033caa3f80205eb6578b2ed0744ebd9e716b790bc4a9703ce909e88
RUN curl -fsSL -o /tmp/code-server.tgz \
		"https://github.com/coder/code-server/releases/download/v${CODE_SERVER_VERSION}/code-server-${CODE_SERVER_VERSION}-linux-amd64.tar.gz" \
	&& echo "${CODE_SERVER_SHA256}  /tmp/code-server.tgz" | sha256sum -c - \
	&& install -d /usr/lib/code-server \
	&& tar -xzf /tmp/code-server.tgz -C /usr/lib/code-server --strip-components=1 \
	&& ln -sf /usr/lib/code-server/bin/code-server /usr/bin/code-server \
	&& rm -f /tmp/code-server.tgz \
	&& code-server --version
# CVE-2026-102422 (critical, arbitrary command execution): code-server 4.141.0, the newest, ships shell-quote 1.10.0
# among VS Code's modules. Replaced by 1.12.0 (the newest; 1.11.0 fixed it), checked by its npm SHA-512; same API
# (quote and parse give the same results). Drop this step once a code-server release carries a fixed shell-quote.
ARG SHELL_QUOTE_VERSION=1.12.0
ARG SHELL_QUOTE_SHA512=3dc072a8dc93ff7f05da40cd8b4d3a1c031125a50bb81ceafc53acc37a9d66f5ff180f56fe36a60da46c9201e3b9b1e27ed5cae6c205c4b364beb5d4c1ca1fe9
RUN curl -fsSL -o /tmp/shell-quote.tgz "https://registry.npmjs.org/shell-quote/-/shell-quote-${SHELL_QUOTE_VERSION}.tgz" \
	&& echo "${SHELL_QUOTE_SHA512}  /tmp/shell-quote.tgz" | sha512sum -c - \
	&& d=/usr/lib/code-server/lib/vscode/node_modules/shell-quote \
	&& test -d "$d" && rm -rf "$d" && install -d "$d" \
	&& tar -xzf /tmp/shell-quote.tgz -C "$d" --strip-components=1 --no-same-owner \
	&& rm -f /tmp/shell-quote.tgz \
	&& /usr/lib/code-server/lib/node -e 'const q = require(process.argv[1]); if (require(process.argv[1] + "/package.json").version !== process.argv[2] || q.quote(["a b"]) !== "'"'"'a b'"'"'") process.exit(1)' "$d" "${SHELL_QUOTE_VERSION}"
RUN curl -fsSL -o /tmp/filebrowser.tgz \
		"https://github.com/filebrowser/filebrowser/releases/download/v${FILEBROWSER_VERSION}/linux-amd64-filebrowser.tar.gz" \
	&& echo "${FILEBROWSER_SHA256}  /tmp/filebrowser.tgz" | sha256sum -c - \
	&& tar -xzf /tmp/filebrowser.tgz -C /usr/local/bin filebrowser \
	&& chmod 0755 /usr/local/bin/filebrowser \
	&& rm -f /tmp/filebrowser.tgz \
	&& filebrowser version

# What it adds over the base, every version as the installed programs say it: /usr/share/coder-workspace/addons.json.
COPY addons/addons.py /tmp/addons.py
RUN python3 /tmp/addons.py "${BASE}" >/usr/share/coder-workspace/addons.json \
	&& python3 -m json.tool /usr/share/coder-workspace/addons.json >/dev/null \
	&& rm -f /tmp/addons.py /usr/share/coder-workspace/.base-packages /usr/share/coder-workspace/.base-manual

USER coder

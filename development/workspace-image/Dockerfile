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
#   - Python's virtual environments (python3 -m venv), for the users' own projects;
#   - the default shell setup (shell/): ~/.bashrc (/etc/skel and /usr/share/coder-workspace/bashrc) and the vimrc it
#     points Vim at; the template copies them into homes that have none (or still Ubuntu's stock .bashrc);
#   - code-server and File Browser, the template's two web apps (pinned below, checked by SHA-256): the template used
#     to download them at every workspace start, which fails without internet. File Browser is FileBrowser Quantum
#     (gtsteffaniak/filebrowser), its release binary for linux-amd64 (filebrowser-quantum at the repository's root,
#     put into the build by ../tools/build-workspace-image.sh), unpacked from UPX so the Images view's scanner reads
#     its Go modules; installed as /usr/local/bin/filebrowser, the command the template starts;
#   - security: every package upgraded at build time; Docker's engine and plugins (dockerd, containerd, buildx,
#     compose) removed - a workspace pod has no privileges to run them, and their Go builds carried known
#     vulnerabilities - as is the base's stray /usr/bin/pebble (no package owns it, nothing uses it); the docker CLI
#     stays, for a remote daemon; packages a workspace pod has no use for, whose known vulnerabilities have no fix
#     yet, removed last (PulseAudio with ffmpeg's libraries, libsoup, the weather plugin, ...), and GNU coreutils
#     instead of Rust coreutils (see the step before addons.json);
#   - /usr/share/coder-workspace/addons.json: what it adds over the base, with every version (addons/addons.py), for
#     the Images view, which lists it next to the image's security report.
# Build with ../tools/build-workspace-image.sh, which saves it into the delivery bundle.
ARG BASE=codercom/enterprise-base:ubuntu
ARG FILEBROWSER_QUANTUM_VERSION=2.0.1-stable
ARG FILEBROWSER_QUANTUM_SHA256=42df10b6f2fcd4db17a53f64a9449e677a528164002fd13b050d348d7c477517

# FileBrowser Quantum: the release binary (the build context "quantum", from build-workspace-image.sh), checked, and
# unpacked from UPX (the same program; a scanner cannot read a packed one).
FROM docker.io/library/golang:1.27.2-trixie@sha256:47667934874913a449b96faa7ef4777fa285d9ec29126ee80ed61c45b7cccf0e AS filebrowser
ARG FILEBROWSER_QUANTUM_VERSION
ARG FILEBROWSER_QUANTUM_SHA256
COPY --from=quantum filebrowser-quantum /tmp/filebrowser-quantum
RUN echo "${FILEBROWSER_QUANTUM_SHA256}  /tmp/filebrowser-quantum" | sha256sum -c - \
	&& apt-get update && apt-get install -y --no-install-recommends upx-ucl \
	&& mkdir -p /out && upx -d -q -o /out/filebrowser /tmp/filebrowser-quantum \
	&& /out/filebrowser version | grep -q "v${FILEBROWSER_QUANTUM_VERSION}"

FROM ${BASE}

USER root
ARG DEBIAN_FRONTEND=noninteractive
ARG BASE

# The base's packages, before anything is added (addons/addons.py, the last step, tells what this image adds).
RUN install -d /usr/share/coder-workspace \
	&& dpkg-query -W -f='${Package}\t${Version}\n' >/usr/share/coder-workspace/.base-packages \
	&& apt-mark showmanual >/usr/share/coder-workspace/.base-manual

# Security: the base's packages to their newest (Ubuntu's and Docker's fixes since the base was built); Docker's engine
# and plugins out (no privileges to run them in a workspace pod; their Go builds carried known vulnerabilities; the
# docker CLI stays, for a remote daemon); the base's /usr/bin/pebble out (no package owns it, nothing uses it).
# iptables stays (Docker had pulled it in): Lock Down's Network mode runs it from this image (lockdown.sh).
RUN apt-get update \
	&& apt-get -y full-upgrade \
	&& apt-mark manual iptables >/dev/null \
	&& apt-get purge -y docker-ce docker-ce-rootless-extras containerd.io docker-buildx-plugin docker-compose-plugin \
	&& apt-get autoremove -y --purge \
	&& rm -f /usr/bin/pebble \
	&& rm -rf /var/lib/apt/lists/* \
	&& docker --version && ! command -v dockerd && ! test -e /usr/libexec/docker/cli-plugins/docker-buildx \
	&& iptables --version && ip6tables --version

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

# Python's virtual environments (python3 -m venv), for the users' own projects.
RUN apt-get update \
	&& apt-get install -y --no-install-recommends python3-venv \
	&& rm -rf /var/lib/apt/lists/* \
	&& python3 -m venv /tmp/venv-check && rm -rf /tmp/venv-check

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

# code-server (VS Code in the browser) and File Browser: downloaded here, at build time only, and checked. The
# template starts these; it downloads them itself only on an image without them (and only with internet).
ARG CODE_SERVER_VERSION=4.141.0
ARG CODE_SERVER_SHA256=65bcd20b524d03eb1b5adb7f360244599ef0e5b74089a46a982d32c074b97a29
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
# GHSA-c475-qrg2-pj4r and others (high: excessive CPU, an uncaught exception): code-server ships basic-ftp 5.3.1 (for
# get-uri, its proxy agent's ftp:// support). Replaced by 6.2.3, the newest, checked by its npm SHA-512. 6.0's one
# breaking change refuses separate FTP transfer hosts by default (bounce attacks), which get-uri does not use.
ARG BASIC_FTP_VERSION=6.2.3
ARG BASIC_FTP_SHA512=4fb198b50d9e28950de583911bb03cbb659f07d24c3746b8ff0977308eb8aaadaa4d17dadc55f3a0dab40c3a23643903c92b5c2fe4e47cab33e091c4671e6419
RUN curl -fsSL -o /tmp/basic-ftp.tgz "https://registry.npmjs.org/basic-ftp/-/basic-ftp-${BASIC_FTP_VERSION}.tgz" \
	&& echo "${BASIC_FTP_SHA512}  /tmp/basic-ftp.tgz" | sha512sum -c - \
	&& d=/usr/lib/code-server/node_modules/basic-ftp \
	&& test -d "$d" && rm -rf "$d" && install -d "$d" \
	&& tar -xzf /tmp/basic-ftp.tgz -C "$d" --strip-components=1 --no-same-owner \
	&& rm -f /tmp/basic-ftp.tgz \
	&& /usr/lib/code-server/lib/node -e 'const f = require(process.argv[1]); if (require(process.argv[1] + "/package.json").version !== process.argv[2] || typeof f.Client !== "function") process.exit(1); require("/usr/lib/code-server/node_modules/get-uri")' "$d" "${BASIC_FTP_VERSION}"
COPY --from=filebrowser /out/filebrowser /usr/local/bin/filebrowser
RUN chmod 0755 /usr/local/bin/filebrowser && filebrowser version | grep -q "FileBrowser Quantum"

# Security, last (no later step may pull these back): medium findings with no fix released, in packages a workspace
# pod has no use for.
#   - PulseAudio and its ALSA plugins: no sound reaches the browser over VNC. With them go ffmpeg's libraries (96
#     findings) and snapd's GLib library with libsoup (18), which the weather panel plugin also needs (it needs the
#     internet). Firefox still plays WebM, VP9, AV1 and Opus with its own decoders; H.264 and AAC (MP4) need ffmpeg.
#   - GNU coreutils in place of Ubuntu 26.04's Rust coreutils (uutils, 20): Ubuntu's own switch, coreutils-from-gnu.
#     build-essential goes (a metapackage that requires the Rust ones); the compilers and tools it names stay.
#   - ipp-usb and avahi-daemon (USB printers), dmsetup (device mapper), nodejs-doc, gstreamer's GL plugin.
# Kept: XFCE's goodies (xfce4-goodies is a metapackage: every plugin it names but the weather one stays) and
# glib-networking (TLS for GLib programs). The checks after it: none of them back, the rest still there.
RUN apt-get update \
	&& apt-mark manual glib-networking $(apt-cache depends --installed xfce4-goodies build-essential \
		| awk '/(Depends|Recommends):/ && $2 !~ /^</ {print $2}' | grep -vxE 'xfce4-weather-plugin|coreutils-from-uutils') >/dev/null \
	&& apt-get install -y --allow-remove-essential coreutils-from-gnu coreutils-from-uutils- \
	&& apt-get purge -y pulseaudio pulseaudio-utils libasound2-plugins libsnapd-glib-2-1 libsoup-3.0-0 libsoup-3.0-common \
		xfce4-weather-plugin xfce4-goodies ipp-usb avahi-daemon dmsetup nodejs-doc gstreamer1.0-gl rust-coreutils \
	&& apt-get autoremove -y --purge \
	&& rm -rf /var/lib/apt/lists/* \
	&& ls --version | grep -q 'GNU coreutils' && test ! -e /usr/lib/cargo/bin/coreutils \
	&& for p in pulseaudio libavcodec62 libavutil60 libswresample6 libsoup-3.0-0 rust-coreutils avahi-daemon; do \
		! dpkg -s "$p" >/dev/null 2>&1 || { echo "still installed: $p"; exit 1; }; done \
	&& for p in xfce4-power-manager xfce4-whiskermenu-plugin xfce4-pulseaudio-plugin glib-networking libpulse0 gcc g++ make dpkg-dev \
		libc6-dev linux-libc-dev; do dpkg -s "$p" >/dev/null 2>&1 || { echo "removed: $p"; exit 1; }; done \
	&& gcc --version >/dev/null && g++ --version >/dev/null && make --version >/dev/null \
	&& iptables --version && ip6tables --version && getent hosts localhost >/dev/null

# What it adds over the base, every version as the installed programs say it: /usr/share/coder-workspace/addons.json.
COPY addons/addons.py /tmp/addons.py
RUN python3 /tmp/addons.py "${BASE}" >/usr/share/coder-workspace/addons.json \
	&& python3 -m json.tool /usr/share/coder-workspace/addons.json >/dev/null \
	&& rm -f /tmp/addons.py /usr/share/coder-workspace/.base-packages /usr/share/coder-workspace/.base-manual

USER coder

#!/usr/bin/env bash
# The workspace's startup script (coder_agent.main in ../main.tf): the shell defaults, the cluster sign-in for admins,
# the desktop and the apps (code-server, File Browser; in the ubuntu-dataflow template, Apache NiFi and Apache Airflow
# in place of code-server; in the AI template, ubuntu-opencode, Unsloth Studio and its server too). A
# template made in Create new template may leave out the desktop or File Browser (its desktop and file_browser
# variables). Runs at every start; each step is safe to repeat.
# The .tf file renders this with templatefile(), so Terraform template syntax applies here: a shell variable in
# braces is written with two dollar signs ($${HOME}), and a percent sign before a brace is doubled (%%{).
set -e

# The template's values (templatefile).
OWNER_NAME='${owner_name}'
WORKSPACE_NAME='${workspace_name}'
NAMESPACE='${namespace}'
CLUSTER_ADMIN='${cluster_admin}' # true: the owner is an admin and the cluster sign-in is on
CLUSTER_KUBECONFIG='${cluster_kubeconfig}'
CLUSTER_OIDC_ISSUER='${cluster_oidc_issuer}'
CLUSTER_OIDC_CLIENT_ID='${cluster_oidc_client_id}'
APP_SET='${app_set}'                   # developer (code-server), dataflow (NiFi, Airflow) or opencode (AI)
DESKTOP='${desktop}'                   # true: the desktop (noVNC's Desktop app)
DESKTOP_THEME='${desktop_theme}'       # the Desktop theme parameter: serenade or classic (coder-desktop-theme)
FILE_BROWSER='${file_browser}'         # true: File Browser
AI_CONTEXT='${context_length}'         # the AI template's Context length parameter: the tokens OpenCode loads a model with
ACCESS_HOST='${access_host}'           # Coder's host, as browsers reach it (NiFi and Airflow make their links with it)
START_DIR='${start_directory}'         # the Start folder parameter: where the terminal and File Browser open
CLOUD_DRIVE_LINK='${cloud_drive_link}' # where the owner's Cloud Drive is linked (none without it)
# Path-based apps are proxied under this prefix ("main" = the agent's name).
APPS="/@$OWNER_NAME/$WORKSPACE_NAME.main/apps"
# One line per step, for the workspace page's Logs panel (the agent keeps this script's output, with the time).
log() { printf '%s\n' "$*"; }
log "Starting workspace $WORKSPACE_NAME of $OWNER_NAME"

# The Linux user is named after the owner - whoami, the prompt, file owners, the desktop - not "coder": uid 1000's
# account is renamed at every start (a new pod has the image's accounts again; the account's processes keep running,
# which usermod refuses, so its files are edited), its home still /home/coder. sudo allows the new name first (checked
# with visudo), so it keeps working (Lock Down's rule still comes last). Left as it is where sudo is not allowed (Lock
# Down's Sudo Command), or where the name is another account's or no Linux user name.
LINUX_USER=$(printf '%s' "$OWNER_NAME" | tr '[:upper:]' '[:lower:]')
CURRENT_USER=$(id -un)
if [ "$CURRENT_USER" = "$LINUX_USER" ]; then
 :
elif [ "$(id -u)" != 1000 ] || ! sudo -n true 2>/dev/null; then
 log "Linux user: $CURRENT_USER (sudo is not allowed, so not renamed $LINUX_USER)"
elif ! printf '%s' "$LINUX_USER" | grep -Eqx '[a-z_][a-z0-9_-]{0,31}'; then
 log "Linux user: $CURRENT_USER ($OWNER_NAME is no Linux user name)"
elif getent passwd "$LINUX_USER" >/dev/null || getent group "$LINUX_USER" >/dev/null; then
 log "Linux user: $CURRENT_USER ($LINUX_USER is another account's name)"
else
 rule=$(mktemp)
 printf '%s ALL=(ALL) NOPASSWD:ALL\n' "$LINUX_USER" >"$rule"
 if sudo -n visudo -cqf "$rule" && sudo -n install -m 0440 -o root -g root "$rule" /etc/sudoers.d/coder-user; then
  sudo -n sed -i "s/^$CURRENT_USER:/$LINUX_USER:/" /etc/passwd /etc/shadow
  sudo -n sed -i -E "s/^$CURRENT_USER:/$LINUX_USER:/; s/([:,])$CURRENT_USER(,|\$)/\1$LINUX_USER\2/g" /etc/group /etc/gshadow
  log "Linux user: $LINUX_USER (was $CURRENT_USER)"
 else
  log "Linux user: $CURRENT_USER (its sudo rule for $LINUX_USER was refused)"
 fi
 rm -f "$rule"
fi
# What starts from here, and every shell (/etc/profile.d, /etc/bash.bashrc), carries the account's name: the agent
# set USER when it started, as coder.
export USER="$(id -un)" LOGNAME="$(id -un)"
if [ "$USER" != coder ] && [ ! -e /etc/profile.d/coder-user.sh ] && sudo -n true 2>/dev/null; then
 printf '%s\n' '# The workspace renames its Linux user after the owner at every start (the template'"'"'s startup.sh).' \
  'export USER="$(id -un)" LOGNAME="$(id -un)"' | sudo -n tee /etc/profile.d/coder-user.sh >/dev/null
 grep -q coder-user.sh /etc/bash.bashrc ||
  printf '. /etc/profile.d/coder-user.sh\n' | sudo -n tee -a /etc/bash.bashrc >/dev/null
fi

# The Start folder: made when it is missing under the home (the agent's sessions start there); a path that steps up
# (..) or one that cannot be made is the home.
case "/$START_DIR/" in
*/../* | */./*) START_DIR=/home/coder ;;
esac
if [ ! -d "$START_DIR" ]; then
 case "$START_DIR" in
 /home/coder/*) mkdir -p "$START_DIR" && log "Start folder: made $START_DIR" || START_DIR=/home/coder ;;
 *) log "Start folder $START_DIR does not exist: File Browser opens the home" && START_DIR=/home/coder ;;
 esac
fi

# No core dumps (the pod's command already sets this for the agent and all it starts), and remove the ones an
# earlier workspace image left in the home directory: light-locker crashed at every desktop start, writing
# core.<pid>. Only files named core.<digits> that are ELF core dumps are removed.
ulimit -c 0
for f in /home/coder/core.[0-9]*; do
 [ -f "$f" ] || continue
 if [ "$(head -c 4 "$f" | od -An -c | tr -d ' ')" = "177ELF" ] && [ "$(od -An -tu1 -j16 -N1 "$f" | tr -d ' ')" = "4" ]; then
  rm -f "$f"
 fi
done

# The default shell setup (in the workspace image): its ~/.bashrc for users who have none, or still Ubuntu's stock
# one (an edited ~/.bashrc is left alone); ~/.profile, so login shells read ~/.bashrc; the vimrc that ~/.bashrc
# points Vim at (VIMINIT).
if [ -f /usr/share/coder-workspace/bashrc ]; then
 if [ ! -e ~/.bashrc ] || cmp -s ~/.bashrc /usr/share/coder-workspace/ubuntu-bashrc; then
  cp /usr/share/coder-workspace/bashrc ~/.bashrc
 fi
 [ -e ~/.profile ] || cp /etc/skel/.profile ~/.profile
 if [ ! -e ~/.config/vim/vimrc ]; then
  mkdir -p ~/.config/vim && cp /usr/share/coder-workspace/vimrc ~/.config/vim/vimrc
 fi
fi

# Dotfiles of an image built in Templates > Images: copied into the home directory where it has none of its own.
if [ -d /usr/share/coder-image/home ]; then
 cp -an /usr/share/coder-image/home/. ~/ 2>/dev/null || true
fi

# btop refreshes every 100 ms instead of its default 2000: for users without a btop configuration, or whose
# configuration still has btop's default (a value someone chose is left alone).
BTOP_CONF=~/.config/btop/btop.conf
if [ ! -e "$BTOP_CONF" ]; then
 mkdir -p ~/.config/btop && echo "update_ms = 100" >"$BTOP_CONF"
elif grep -qx 'update_ms = 2000' "$BTOP_CONF"; then
 sed -i 's/^update_ms = 2000$/update_ms = 100/' "$BTOP_CONF"
fi

# tmux (in the workspace image): the bundled configuration, for users who have none yet.
if command -v tmux-mirror-setup >/dev/null && [ ! -e ~/.config/tmux ] && [ ! -e ~/.tmux.conf ]; then
 tmux-mirror-setup >/tmp/tmux-mirror-setup.log 2>&1 || true
fi

# ~/.cert/<owner>.p12 is installed by the "Certificates" script (main.tf's platform section "certificates") from
# Settings > Certificates.

# Admins: kubectl and k9s on this cluster. ~/.kube/coder-cluster.yaml (this template's own file, rewritten at
# every start; KUBECONFIG lists it before ~/.kube/config, which is never touched) has the context
# <owner>@coder-cluster. The first kubectl command signs in: it prints a Keycloak link and code, which the admin
# opens in their own browser and signs in to as themselves. The tokens are kept in /tmp (gone when the workspace
# restarts) and last as long as that Keycloak session. Someone who is no longer an admin loses the file.
if [ "$CLUSTER_ADMIN" = "true" ]; then
 mkdir -p ~/.kube /tmp/coder-kube-signin && chmod 700 ~/.kube /tmp/coder-kube-signin
 cat >"$CLUSTER_KUBECONFIG.tmp" <<KUBECONFIG_FILE
# Written by the workspace template at every start (cluster sign-in for admins); edits here are replaced.
apiVersion: v1
kind: Config
clusters:
- name: coder-cluster
  cluster:
    server: https://kubernetes.default.svc
    certificate-authority: /var/run/secrets/kubernetes.io/serviceaccount/ca.crt
contexts:
- name: $OWNER_NAME@coder-cluster
  context:
    cluster: coder-cluster
    user: $OWNER_NAME@keycloak
    namespace: $NAMESPACE
current-context: $OWNER_NAME@coder-cluster
users:
- name: $OWNER_NAME@keycloak
  user:
    exec:
      apiVersion: client.authentication.k8s.io/v1
      command: kubectl
      args:
      - oidc-login
      - get-token
      - --oidc-issuer-url=$CLUSTER_OIDC_ISSUER
      - --oidc-client-id=$CLUSTER_OIDC_CLIENT_ID
      - --grant-type=device-code
      - --skip-open-browser
      - --token-cache-dir=/tmp/coder-kube-signin
      interactiveMode: IfAvailable
      provideClusterInfo: false
KUBECONFIG_FILE
 chmod 600 "$CLUSTER_KUBECONFIG.tmp" && mv "$CLUSTER_KUBECONFIG.tmp" "$CLUSTER_KUBECONFIG"
else
 rm -f "$CLUSTER_KUBECONFIG"
fi
[ "$CLUSTER_ADMIN" = "true" ] && log "kubectl and k9s: signed in to this cluster as you on first use (~/.kube/coder-cluster.yaml)"

# 1. Desktop Environment & noVNC, first: it needs no network. It is in the workspace image
# (ghcr.io/user187x/coder-workspace, without light-locker); installed here only on an image without it.
# code-server and File Browser below are downloaded when the image lacks them; offline they are skipped, the rest
# still starts. An image built in Templates > Images names its desktop in /etc/coder-image/desktop: xfce, gnome or
# none (no desktop: the Desktop app stays down); a template without the Desktop app starts none either.
IMAGE_DESKTOP=$(cat /etc/coder-image/desktop 2>/dev/null || echo xfce)
NO_DESKTOP="none in this image"
if [ "$DESKTOP" != true ]; then
 IMAGE_DESKTOP=none
 NO_DESKTOP="not in this template"
fi
if [ ! -f /usr/bin/vncserver ] && [ "$IMAGE_DESKTOP" != none ]; then
 sudo apt-get update
 sudo DEBIAN_FRONTEND=noninteractive apt-get install -y xfce4 xfce4-goodies dbus-x11 tigervnc-standalone-server novnc websockify light-locker- lightdm- lightdm-gtk-greeter-
 sudo rm -f /etc/xdg/autostart/light-locker.desktop /etc/xdg/autostart/xfce4-screensaver.desktop
fi

# Serve the noVNC client as the directory index so the bare app URL loads it.
# (Only when the image lacks it: the workspace image has it, and a Lock Down may have taken sudo away.)
[ "$(readlink /usr/share/novnc/index.html 2>/dev/null)" = /usr/share/novnc/vnc.html ] ||
 sudo ln -sf /usr/share/novnc/vnc.html /usr/share/novnc/index.html

# Configure XFCE (or the image's own desktop session, such as GNOME) to start on VNC launch
# TigerVNC >= 1.14 reads ~/.config/tigervnc (the legacy ~/.vnc fails to migrate),
# and the session ends when xstartup returns, so the desktop must be exec'd in the foreground.
mkdir -p ~/.config/tigervnc
cat <<'VNC_SCRIPT' >~/.config/tigervnc/xstartup
#!/bin/sh
xrdb $HOME/.Xresources 2>/dev/null
[ -x /usr/local/bin/coder-desktop-session ] && exec /usr/local/bin/coder-desktop-session
exec startxfce4
VNC_SCRIPT
chmod +x ~/.config/tigervnc/xstartup

# The desktop is faster over VNC without XFCE's compositor (every composited effect is extra drawing to encode
# and send). The workspace image turns it off by default; desktops set up before that get it off once (later
# changes in Settings > Window Manager Tweaks are kept).
XFWM4_XML=~/.config/xfce4/xfconf/xfce-perchannel-xml/xfwm4.xml
if [ ! -e ~/.config/coder-workspace/xfwm4-no-compositing ]; then
 if [ -f "$XFWM4_XML" ]; then
  sed -i 's|name="use_compositing" type="bool" value="true"|name="use_compositing" type="bool" value="false"|' "$XFWM4_XML"
 fi
 mkdir -p ~/.config/coder-workspace && touch ~/.config/coder-workspace/xfwm4-no-compositing
fi

# A terminal emulator never runs "in a terminal": XFCE's launcher editor (the dock's Edit Launcher) writes
# Terminal=true into the home's copy of a launcher when "Run in terminal" is ticked, and Terminator then opens inside
# xfce4-terminal, which shows Terminator's start-up warnings. The home's launchers of terminal emulators run on their own.
for f in ~/.local/share/applications/*.desktop; do
 { [ -f "$f" ] && grep -q '^Categories=.*TerminalEmulator' "$f" && grep -q '^Terminal=true$' "$f"; } || continue
 sed -i 's/^Terminal=true$/Terminal=false/' "$f" && log "Desktop: $(basename "$f") runs on its own again (not in a terminal)"
done

# TigerVNC's X server on display :1, started directly (not through vncserver, which warns that a password-less
# server is open to every user of the machine). It listens on no TCP port, only on a Unix socket that only this
# user can open (directory 0700, socket 0600): no other user and no other pod can reach it, so it needs no
# password. The X display itself has a cookie (~/.Xauthority).
VNC_SOCK=/tmp/coder-vnc/rfb.sock
mkdir -p /tmp/coder-vnc && chmod 700 /tmp/coder-vnc
if [ "$IMAGE_DESKTOP" != none ] && ! pgrep -f "^Xtigervnc :1 " >/dev/null; then
 # the Desktop theme, put in the home before XFCE reads it (a workspace image from before the themes has none)
 if [ "$IMAGE_DESKTOP" = xfce ] && [ -x /usr/local/bin/coder-desktop-theme ]; then
  log "$(/usr/local/bin/coder-desktop-theme apply "$DESKTOP_THEME" 2>&1 || true)"
 fi
 rm -f "$VNC_SOCK" /tmp/.X1-lock /tmp/.X11-unix/X1
 touch ~/.Xauthority && chmod 600 ~/.Xauthority
 xauth -f ~/.Xauthority add :1 MIT-MAGIC-COOKIE-1 "$(mcookie)"
 # -FrameRate 60: updates as often as noVNC can show them. -SendPrimary=0: only what is copied (the CLIPBOARD) goes to
 # the computer's clipboard (the image's noVNC shares it both ways), not every text selected (the PRIMARY).
 Xtigervnc :1 -auth ~/.Xauthority -geometry 1280x800 -depth 24 -rfbport -1 -rfbunixpath "$VNC_SOCK" \
  -SecurityTypes None -AlwaysShared -FrameRate 60 -SendPrimary=0 -desktop "$(hostname)" >/tmp/xvnc.log 2>&1 &
 for i in $(seq 1 50); do
  [ -S "$VNC_SOCK" ] && break
  sleep 0.2
 done
 DISPLAY=:1 XAUTHORITY=~/.Xauthority setsid ~/.config/tigervnc/xstartup >/tmp/xstartup.log 2>&1 </dev/null &
 log "Desktop: started ($IMAGE_DESKTOP on display :1)"
elif [ "$IMAGE_DESKTOP" = none ]; then
 log "Desktop: $NO_DESKTOP (the Desktop app stays down)"
else
 log "Desktop: running already"
fi

# websockify serves noVNC on 127.0.0.1 only and connects to the VNC socket: the only way in is Coder's app proxy,
# which signs users in. Coder strips the /@user/workspace.agent/apps/novnc prefix before proxying, so the stock web
# root works as-is.
# (By its port: pgrep -f would match this script, whose text contains the pattern.)
if [ "$IMAGE_DESKTOP" != none ] && ! curl -s -o /dev/null http://127.0.0.1:6901/; then
 websockify --web=/usr/share/novnc/ --unix-target="$VNC_SOCK" 127.0.0.1:6901 >/tmp/novnc.log 2>&1 &
 log "Desktop app (noVNC): started"
fi

# 2. code-server: the workspace image's (1.8.0 and later), so it works without internet. Downloaded only on an
# image without it, which needs internet (it is skipped, with a note in the log, when there is none). With code-server
# HA (CODER_CODE_SERVER_HA, the template's code_server_ha) it runs elsewhere: the relay in this pod answers instead.
if [ -n "$${CODER_CODE_SERVER_HA:-}" ]; then
 log "code-server: high availability - its instance runs apart from the workspace (the relay answers on 13337)"
elif [ "$APP_SET" != dataflow ]; then
 CODE_SERVER="$(command -v code-server || true)"
 if [ -z "$CODE_SERVER" ]; then
  if [ ! -x /tmp/code-server/bin/code-server ]; then
   curl -fsSL --max-time 60 https://code-server.dev/install.sh | sh -s -- --method=standalone --prefix=/tmp/code-server ||
    echo "code-server is not in this image and could not be downloaded: skipped"
  fi
  CODE_SERVER=/tmp/code-server/bin/code-server
 fi
 # Probe the port rather than pgrep -f: the agent runs this script as `sh -c '<script>'`,
 # so `pgrep -f` matches the script's own command line and never starts anything.
 if [ -x "$CODE_SERVER" ] && ! curl -fs http://localhost:13337/healthz >/dev/null; then
  "$CODE_SERVER" --auth none --port 13337 --disable-telemetry --disable-update-check >/tmp/code-server.log 2>&1 &
  log "code-server: started (log: /tmp/code-server.log)"
 elif [ -x "$CODE_SERVER" ]; then
  log "code-server: running already"
 fi
fi

# 3. File Browser (when the template has it): FileBrowser Quantum, the workspace image's (an image without it gets
# none: there is nothing to download it from without internet), started by coder-filebrowser (scripts/filebrowser.sh):
# under the app's path, on 127.0.0.1 only, no sign-in of its own; the Start folder, and each Cloud Drive here as a
# source of its own (Quantum opens no link out of a source's folder; the Cloud Drive's watcher restarts it as the drive
# is connected or disconnected). Its settings are written at every start (the Start folder may have changed); its
# database stays in the home. The helper is installed here and by the Cloud Drive's script, the newer one kept. An image
# built on an older workspace image still has the former File Browser: started with its own flags.
if [ "$FILE_BROWSER" != true ]; then
 log "File Browser: not in this template"
elif ! command -v filebrowser >/dev/null; then
 log "File Browser: not in this image: skipped"
elif filebrowser version 2>/dev/null | grep -q "FileBrowser Quantum"; then
 mkdir -p "$HOME/.config/filebrowser-quantum" "$HOME/.local/bin"
 printf 'APPS=%q\nSTART_DIR=%q\nCLOUD_DRIVE_LINK=%q\n' "$APPS" "$START_DIR" "$CLOUD_DRIVE_LINK" \
  >"$HOME/.config/filebrowser-quantum/env"
 fb_helper="$(mktemp)" && echo '${filebrowser}' | base64 -d >"$fb_helper" && bash "$fb_helper" install
 rm -f "$fb_helper"
 log "$("$HOME/.local/bin/coder-filebrowser" start)"
elif pgrep -x filebrowser >/dev/null; then
 log "File Browser: running already"
else
 filebrowser --noauth --root "$START_DIR" --port 13339 \
  --baseurl "$APPS/filebrowser" \
  >/tmp/filebrowser.log 2>&1 &
 log "File Browser: started in $START_DIR (log: /tmp/filebrowser.log)"
fi

# 4. Apache NiFi and Apache Airflow: the ubuntu-dataflow template's apps, in place of code-server, with no network.
# The workspace image has both: /opt/nifi (its NARs unpacked there, read-only) and /opt/airflow.
if [ "$APP_SET" = dataflow ]; then
 # NiFi: from /opt/nifi as the image installed it (its NARs unpacked there, read-only); everything it writes is a
 # link into ~/.nifi: conf (the image's, the first time; then the user's flows and settings), logs, state,
 # repositories, work areas. Unsecured HTTP on 127.0.0.1:8080, for this workspace only (the app is its owner's
 # alone). The app's path reaches it in the proxy headers the cluster's Gateway adds (the template's HTTPRoute), so
 # its pages and API calls go through Coder's proxy; it accepts them for this path and Coder's host only.
 if [ -x /opt/nifi/bin/nifi.sh ]; then
  NIFI_HOME=/home/coder/.nifi
  mkdir -p "$NIFI_HOME"
  (cd "$NIFI_HOME" && mkdir -p logs run state database_repository flowfile_repository content_repository \
   provenance_repository status_repository nar_repository assets connector_assets diagnostics \
   work/uploads work/python work/jetty work/docs)
  if [ ! -f "$NIFI_HOME/conf/nifi.properties" ]; then
   cp -r /opt/nifi/conf.dist "$NIFI_HOME/conf"
   # A key of its own for the flows' sensitive properties (kept with them).
   NIFI_KEY="$(head -c 32 /dev/urandom | base64 | tr -dc A-Za-z0-9 | head -c 32)"
   sed -i "s|^nifi.sensitive.props.key=.*|nifi.sensitive.props.key=$NIFI_KEY|" "$NIFI_HOME/conf/nifi.properties"
  fi
  # Its path and Coder's host, every start: renaming the workspace changes the path.
  sed -i -e "s|^nifi.web.proxy.context.path=.*|nifi.web.proxy.context.path=$APPS/nifi|" \
   -e "s|^nifi.web.proxy.host=.*|nifi.web.proxy.host=$ACCESS_HOST,$ACCESS_HOST:443|" "$NIFI_HOME/conf/nifi.properties"
  if ! curl -fs http://127.0.0.1:8080/nifi-api/flow/about >/dev/null; then
   JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64 /opt/nifi/bin/nifi.sh start </dev/null >/tmp/nifi-start.log 2>&1
   log "Apache NiFi: starting (log: ~/.nifi/logs/nifi-app.log)"
  else
   log "Apache NiFi: running already"
  fi
 else
  log "Apache NiFi is not in this image (/opt/nifi): skipped"
 fi

 # Airflow: standalone (its API server, scheduler, DAG processor and triggerer; SQLite in ~/airflow) under the full
 # app path (base_url), every user an admin (the app is its owner's alone), with Airflow's example DAGs. The same
 # settings in ~/.airflowrc for the airflow command in terminals.
 if [ -x /opt/airflow/bin/airflow ]; then
  AIRFLOW_BASE="$APPS/airflow"
  cat >/home/coder/.airflowrc <<AIRFLOWRC
export AIRFLOW_HOME=/home/coder/airflow
export AIRFLOW__API__HOST=127.0.0.1
export AIRFLOW__API__PORT=8090
export AIRFLOW__API__WORKERS=1
export AIRFLOW__API__BASE_URL=https://$ACCESS_HOST$AIRFLOW_BASE
export AIRFLOW__CORE__EXECUTION_API_SERVER_URL=http://127.0.0.1:8090$AIRFLOW_BASE/execution/
export AIRFLOW__CORE__SIMPLE_AUTH_MANAGER_ALL_ADMINS=True
export AIRFLOW__CORE__LOAD_EXAMPLES=True
export PATH="/opt/airflow/bin:\$PATH"
AIRFLOWRC
  grep -qs "airflowrc" /home/coder/.bashrc || echo '[ -f ~/.airflowrc ] && . ~/.airflowrc' >>/home/coder/.bashrc
  if ! curl -fs "http://127.0.0.1:8090$AIRFLOW_BASE/api/v2/monitor/health" >/dev/null; then
   # The whole subshell redirected (and exec'd): a background job holding the agent's output keeps the script from
   # ending (a start error).
   (. /home/coder/.airflowrc && mkdir -p "$AIRFLOW_HOME/dags" && exec airflow standalone) </dev/null >/tmp/airflow.log 2>&1 &
   log "Apache Airflow: starting (log: /tmp/airflow.log)"
  else
   log "Apache Airflow: running already"
  fi
 else
  log "Apache Airflow is not in this image (/opt/airflow): skipped"
 fi
fi

# 5. Unsloth Studio (the AI template, ubuntu-opencode), on the AI workspace image, with no network: its home linked to
# the image's install (its Python environments and llama.cpp are not copied into every home), its window on the
# desktop, which starts its server on 127.0.0.1:8888 (OpenAI-compatible: OpenCode's provider, with the model OpenCode
# loads at the Context length's tokens); it scans the model library (~/models) and the owner's Cloud Drive
# (~/cloud-drive/models) for GGUF models. In the background, redirected: Unsloth takes up to a minute to answer, and a
# background job holding the agent's output would keep this script from ending.
if [ "$APP_SET" = opencode ]; then
 if command -v coder-unsloth-start >/dev/null; then
  DISPLAY=:1 XAUTHORITY=/home/coder/.Xauthority setsid coder-unsloth-start </dev/null >/tmp/unsloth-start.log 2>&1 &
  log "Unsloth: starting on the desktop, with its server on 127.0.0.1:8888 (log: /tmp/unsloth-start.log)"
 else
  log "Unsloth Studio is not in this image (coder-unsloth-start): skipped"
 fi
fi

log "Startup done: the apps open once they answer their health checks"

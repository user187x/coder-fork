#!/usr/bin/env bash
# The workspace's startup script (coder_agent.main in ../main.tf): the shell defaults, the cluster sign-in for admins,
# the desktop and the apps (code-server, File Browser, marimo; or, in the ubuntu-dataflow template, Apache NiFi and
# Apache Airflow in place of code-server and marimo). Runs at every start; each step is safe to repeat.
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
APP_SET='${app_set}'         # developer (code-server, marimo) or dataflow (Apache NiFi, Apache Airflow)
ACCESS_HOST='${access_host}' # Coder's host, as browsers reach it (NiFi and Airflow make their links with it)
# Path-based apps are proxied under this prefix ("main" = the agent's name).
APPS="/@$OWNER_NAME/$WORKSPACE_NAME.main/apps"
# One line per step, for the workspace page's Logs panel (the agent keeps this script's output, with the time).
log() { printf '%s\n' "$*"; }
log "Starting workspace $WORKSPACE_NAME of $OWNER_NAME"

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

# ~/.cert/<owner>.p12 is installed by the "Certificates" script (certificates.tf) from Settings > Certificates.

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
# code-server, File Browser and marimo below are downloaded; offline they are skipped, the rest still starts.
# An image built in Templates > Images names its desktop in /etc/coder-image/desktop: xfce, gnome or none (no
# desktop: the Desktop app stays down).
IMAGE_DESKTOP=$(cat /etc/coder-image/desktop 2>/dev/null || echo xfce)
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

# TigerVNC's X server on display :1, started directly (not through vncserver, which warns that a password-less
# server is open to every user of the machine). It listens on no TCP port, only on a Unix socket that only this
# user can open (directory 0700, socket 0600): no other user and no other pod can reach it, so it needs no
# password. The X display itself has a cookie (~/.Xauthority).
VNC_SOCK=/tmp/coder-vnc/rfb.sock
mkdir -p /tmp/coder-vnc && chmod 700 /tmp/coder-vnc
if [ "$IMAGE_DESKTOP" != none ] && ! pgrep -f "^Xtigervnc :1 " >/dev/null; then
 rm -f "$VNC_SOCK" /tmp/.X1-lock /tmp/.X11-unix/X1
 touch ~/.Xauthority && chmod 600 ~/.Xauthority
 xauth -f ~/.Xauthority add :1 MIT-MAGIC-COOKIE-1 "$(mcookie)"
 # -FrameRate 60: updates as often as noVNC can show them.
 Xtigervnc :1 -auth ~/.Xauthority -geometry 1280x800 -depth 24 -rfbport -1 -rfbunixpath "$VNC_SOCK" \
  -SecurityTypes None -AlwaysShared -FrameRate 60 -desktop "$(hostname)" >/tmp/xvnc.log 2>&1 &
 for i in $(seq 1 50); do
  [ -S "$VNC_SOCK" ] && break
  sleep 0.2
 done
 DISPLAY=:1 XAUTHORITY=~/.Xauthority setsid ~/.config/tigervnc/xstartup >/tmp/xstartup.log 2>&1 </dev/null &
 log "Desktop: started ($IMAGE_DESKTOP on display :1)"
elif [ "$IMAGE_DESKTOP" = none ]; then
 log "Desktop: none in this image (the Desktop app stays down)"
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

# (Not in the dataflow template: it has no marimo.)
if [ "$APP_SET" != dataflow ]; then
 # Windows of marimo notebooks (tkinter): a second, private X display (:2: no desktop, no window manager, on its
 # own Unix socket), sized by the notebook to the window it shows. coder_marimo_tk (marimo, below) opens tkinter
 # windows there and shows each one live in its cell's output: the minimal noVNC page below, through the hidden
 # app "marimo-windows" (websockify on 127.0.0.1:6902).
 APPS_SOCK=/tmp/coder-vnc/apps.sock
 if ! pgrep -f "^Xtigervnc :2 " >/dev/null; then
  rm -f "$APPS_SOCK" /tmp/.X2-lock /tmp/.X11-unix/X2
  touch ~/.Xauthority && chmod 600 ~/.Xauthority
  xauth -f ~/.Xauthority add :2 MIT-MAGIC-COOKIE-1 "$(mcookie)"
  Xtigervnc :2 -auth ~/.Xauthority -geometry 640x480 -depth 24 -rfbport -1 -rfbunixpath "$APPS_SOCK" \
   -SecurityTypes None -AlwaysShared -FrameRate 60 -desktop marimo-windows >/tmp/xvnc-apps.log 2>&1 &
  for i in $(seq 1 50); do
   [ -S "$APPS_SOCK" ] && break
   sleep 0.2
  done
 fi
 APPS_WEB=/tmp/coder-vnc/apps-web
 mkdir -p "$APPS_WEB" && ln -sfn /usr/share/novnc/core "$APPS_WEB/core" && ln -sfn /usr/share/novnc/vendor "$APPS_WEB/vendor"
 cat <<'APPS_VIEW' >"$APPS_WEB/index.html"
<!doctype html>
<html><head><meta charset="utf-8"><title>marimo window</title>
<style>html,body{margin:0;height:100%;overflow:hidden;background:#fff}#screen{width:100%;height:100%}
#msg{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;font:12px sans-serif;color:#777}</style>
</head><body><div id="msg">Connecting...</div><div id="screen"></div>
<script type="module">
import RFB from "./core/rfb.js";
// The websocket next to this page (Coder's app path), so it works under any prefix.
const url = new URL("websockify", location.href);
url.protocol = location.protocol === "https:" ? "wss:" : "ws:";
const msg = document.getElementById("msg");
const connect = () => {
  const rfb = new RFB(document.getElementById("screen"), url.href);
  rfb.scaleViewport = false;
  rfb.resizeSession = false;
  rfb.addEventListener("connect", () => { msg.style.display = "none"; });
  rfb.addEventListener("disconnect", () => {
    msg.textContent = "Window closed";
    msg.style.display = "flex";
    setTimeout(connect, 2000);
  });
};
connect();
</script></body></html>
APPS_VIEW
 # (By its port: pgrep -f would match this script, whose text contains the pattern.)
 if ! curl -s -o /dev/null http://127.0.0.1:6902/; then
  websockify --web="$APPS_WEB" --unix-target="$APPS_SOCK" 127.0.0.1:6902 >/tmp/novnc-apps.log 2>&1 &
 fi
fi

# 2. code-server: the workspace image's (1.8.0 and later), so it works without internet. Downloaded only on an
# image without it, which needs internet (it is skipped, with a note in the log, when there is none).
if [ "$APP_SET" != dataflow ]; then
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

# 3. File Browser: the workspace image's as well; downloaded only on an image without it (needs internet).
if ! command -v filebrowser >/dev/null; then
 curl -fsSL --max-time 60 https://raw.githubusercontent.com/filebrowser/get/master/get.sh | sudo bash ||
  echo "File Browser is not in this image and could not be downloaded: skipped"
fi
# Path-based apps are proxied with their full /@user/workspace.agent/apps/<slug> prefix
# intact, so filebrowser must serve under it or its assets 404 (endless spinner).
# The agent name ("main") is hardcoded: referencing coder_agent.main here would be a cycle.
if ! pgrep -x filebrowser >/dev/null; then
 filebrowser --noauth --root /home/coder --port 13339 \
  --baseurl "$APPS/filebrowser" \
  >/tmp/filebrowser.log 2>&1 &
 log "File Browser: started (log: /tmp/filebrowser.log)"
else
 log "File Browser: running already"
fi

# 4. marimo (Python notebooks), with no network: the workspace image has it with everything it needs in /opt/marimo
# (marimo and its extras - SQL, charts, formatting, language server, uv - and a data stack; tkinter). Each user's
# notebooks run in ~/.marimo-venv, which sees all of /opt/marimo (a .pth file) and takes the packages they add
# (pip or uv; with no network, only what is already there). It is (re)made when missing, when an older one installed
# marimo itself from the internet, or when the image's Python changes.
if [ "$APP_SET" != dataflow ]; then
 MARIMO_VENV=/home/coder/.marimo-venv
 MARIMO_BASE="$APPS/marimo"
 if [ -x /opt/marimo/bin/marimo ]; then
  MARIMO_STAMP="$(python3 -V) /opt/marimo $(/opt/marimo/bin/marimo --version)"
  if [ "$(cat "$MARIMO_VENV/.coder-workspace" 2>/dev/null)" != "$MARIMO_STAMP" ]; then
   rm -rf "$MARIMO_VENV"
   python3 -m venv "$MARIMO_VENV"
   /opt/marimo/bin/python -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])' \
    >"$("$MARIMO_VENV/bin/python" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')/coder-workspace-marimo.pth"
   echo "$MARIMO_STAMP" >"$MARIMO_VENV/.coder-workspace"
  fi
  # tkinter windows show in the notebook and stop with marimo's Stop button: coder_marimo_tk, loaded through the .pth.
  MARIMO_SITE="$("$MARIMO_VENV/bin/python" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"
  cat <<'CODER_MARIMO_TK' >"$MARIMO_SITE/coder_marimo_tk.py"
"""Workspace template: tkinter windows in marimo notebooks show in the notebook, and stop with its Stop button.

In a marimo kernel (marimo imported before tkinter) and with the template's settings in the environment:
  - a Tk window opens on the workspace's private display for notebook windows (CODER_MARIMO_TK_DISPLAY, :2 - no
    desktop, no window manager), at its top-left corner, and that display is sized to the window;
  - while mainloop() runs, the cell's output is the window itself, live and interactive: a noVNC view of that
    display (CODER_MARIMO_TK_VIEW, the hidden app "marimo-windows"), resized when the window is;
  - mainloop() runs the window's events from Python, so marimo's Stop works at once (Tk's own loop waits in C,
    where the interrupt is only noticed at the next window event). Stop is the window's close button: the window
    closes and the cell ends normally ("Window closed"), not with an interrupt traceback; closing the window from
    code (destroy) ends it the same way. A failed cell does not leave a dead window behind.
Loaded lazily (when tkinter is imported) from coder-workspace-marimo.pth in ~/.marimo-venv.
"""

import importlib.abc
import importlib.machinery
import os
import subprocess
import sys

DISPLAY = os.environ.get("CODER_MARIMO_TK_DISPLAY", "")
VIEW = os.environ.get("CODER_MARIMO_TK_VIEW", "")


def _size(root):
    root.update_idletasks()
    return max(root.winfo_width(), root.winfo_reqwidth(), 1), max(root.winfo_height(), root.winfo_reqheight(), 1)


def _show(root, size):
    """The cell's output: the window, live (the display is cut to the window's size)."""
    width, height = size
    if DISPLAY:
        subprocess.run(["xrandr", "-d", DISPLAY, "--fb", "%dx%d" % (width, height)], capture_output=True, check=False)
    if not VIEW:
        return
    try:
        import marimo as mo

        mo.output.replace(
            mo.Html(
                '<iframe src="%s" title="tkinter window" style="display:block;width:%dpx;height:%dpx;'
                'border:1px solid rgba(127,127,127,.35);border-radius:6px;background:#fff" '
                'allow="clipboard-read; clipboard-write"></iframe>' % (VIEW, width + 2, height + 2)
            )
        )
    except Exception:
        pass


def _closed(how):
    """The cell's output once its window is gone (the live view would only say "Window closed")."""
    if not VIEW:
        return
    try:
        import marimo as mo

        mo.output.replace(
            mo.Html('<span style="font-size:12px;opacity:.65">Window closed%s.</span>' % how)
        )
    except Exception:
        pass


def _patch(tkinter):
    import time

    if DISPLAY:
        tk_init = tkinter.Tk.__init__

        def __init__(self, screenName=None, *args, **kwargs):
            tk_init(self, DISPLAY if screenName is None else screenName, *args, **kwargs)
            try:
                self.wm_geometry("+0+0")  # no window manager there: the window is all the view shows
            except tkinter.TclError:
                pass

        tkinter.Tk.__init__ = __init__

    def mainloop(widget=None, n=0):
        root = tkinter._get_default_root("run the main loop") if widget is None else widget._root()
        shown = None
        try:
            ticks = 0
            while root.winfo_exists():
                root.update()
                if ticks % 25 == 0:  # twice a second: follow the window's size
                    size = _size(root)
                    if size != shown:
                        _show(root, size)
                        shown = size
                ticks += 1
                time.sleep(0.02)
        except tkinter.TclError:
            pass  # the window was closed (destroyed)
        except KeyboardInterrupt:
            # marimo's Stop (MarimoInterrupt is a KeyboardInterrupt): close the window, as its close button would.
            try:
                root.destroy()
            except Exception:
                pass
            if shown is not None:
                _closed(" (Stop)")
            return
        except BaseException:
            try:
                root.destroy()  # failed: do not leave a dead window behind
            except Exception:
                pass
            raise
        if shown is not None:
            _closed("")

    tkinter.Misc.mainloop = lambda self, n=0: mainloop(self, n)
    tkinter.mainloop = lambda n=0: mainloop(None, n)


class _Finder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname != "tkinter" or "marimo" not in sys.modules:
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        if spec is None or spec.loader is None:
            return None
        loader = spec.loader
        exec_module = loader.exec_module

        def exec_and_patch(module):
            exec_module(module)
            _patch(module)

        loader.exec_module = exec_and_patch
        return spec


if not any(isinstance(f, _Finder) for f in sys.meta_path):
    sys.meta_path.insert(0, _Finder())
CODER_MARIMO_TK
  grep -qx "import coder_marimo_tk" "$MARIMO_SITE/coder-workspace-marimo.pth" 2>/dev/null ||
   echo "import coder_marimo_tk" >>"$MARIMO_SITE/coder-workspace-marimo.pth"
  # marimo installs missing packages with the manager in its settings: one that is not installed here (pixi, rye,
  # poetry) cannot work, so it falls back to uv (in /opt/marimo). An installed choice is kept.
  MARIMO_TOML=~/.config/marimo/marimo.toml
  for m in pixi rye poetry; do
   if grep -qx "manager = \"$m\"" "$MARIMO_TOML" 2>/dev/null && ! command -v "$m" >/dev/null; then
    sed -i "s/^manager = \"$m\"$/manager = \"uv\"/" "$MARIMO_TOML"
   fi
  done
  # marimo's dark theme by default: set once per user (a later change in marimo's settings is kept).
  if [ ! -e ~/.config/marimo/.coder-workspace-dark ]; then
   mkdir -p ~/.config/marimo
   /opt/marimo/bin/python - "$MARIMO_TOML" <<'MARIMO_DARK' && touch ~/.config/marimo/.coder-workspace-dark
import os, sys, tomlkit
path = sys.argv[1]
doc = tomlkit.parse(open(path).read()) if os.path.exists(path) else tomlkit.document()
doc.setdefault("display", tomlkit.table())["theme"] = "dark"
open(path, "w").write(tomlkit.dumps(doc))
MARIMO_DARK
  fi
  mkdir -p /home/coder/notebooks
  # --no-token: no marimo login page. marimo listens on 127.0.0.1 only, so the Coder agent's
  # proxy is the only way in, and the app is share = "owner": Coder decides who gets in.
  # /opt/marimo/bin on PATH: uv and ruff for marimo; pip and uv give up quickly with no network.
  # DISPLAY: GUI windows of other libraries show on the workspace's desktop (display :1, the Desktop app);
  # without a display they fail with "no $DISPLAY". tkinter windows show in the notebook instead
  # (CODER_MARIMO_TK_*: coder_marimo_tk, the private display :2 and the app "marimo-windows").
  if ! curl -fs "http://localhost:2718$MARIMO_BASE/health" >/dev/null; then
   DISPLAY=:1 XAUTHORITY=/home/coder/.Xauthority CODER_MARIMO_TK_DISPLAY=:2 \
    CODER_MARIMO_TK_VIEW="$APPS/marimo-windows/" \
    PATH="$MARIMO_VENV/bin:/opt/marimo/bin:$PATH" PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_RETRIES=0 PIP_TIMEOUT=10 \
    UV_HTTP_TIMEOUT=10 "$MARIMO_VENV/bin/python" -m marimo edit --headless --host 127.0.0.1 --port 2718 \
    --no-token --skip-update-check --base-url "$MARIMO_BASE" /home/coder/notebooks >/tmp/marimo.log 2>&1 &
   log "marimo: started (log: /tmp/marimo.log)"
  else
   log "marimo: running already"
  fi
 else
  log "marimo is not in this image (/opt/marimo): skipped"
 fi
fi

# 5. Apache NiFi and Apache Airflow: the ubuntu-dataflow template's apps, in place of code-server and marimo, with no
# network. The workspace image has both: /opt/nifi (its NARs unpacked there, read-only) and /opt/airflow.
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

log "Startup done: the apps open once they answer their health checks"

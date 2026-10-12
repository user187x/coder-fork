terraform {
  required_providers {
    coder = {
      source = "coder/coder"
    }
    kubernetes = {
      source = "hashicorp/kubernetes"
    }
  }
}

provider "coder" {}

variable "use_kubeconfig" {
  type        = bool
  description = "Use host kubeconfig? (true/false)"
  default     = false
}

variable "namespace" {
  type        = string
  description = "The Kubernetes namespace to create workspaces in. Workspaces run only in the coder namespace."
  default     = "coder"
  validation {
    condition     = var.namespace == "coder"
    error_message = "Workspaces are created only in the coder namespace."
  }
}

# Admins' kubectl and k9s on this cluster (global.clusterAccess in the chart): each admin signs in as themselves with
# Keycloak's device login (kubelogin), so the cluster sees the person (keycloak:<user>).
# The workspace holds no long-lived credential; the sign-in lasts as long as the Keycloak session. "" = off.
variable "cluster_oidc_issuer" {
  type        = string
  description = "Keycloak issuer URL the cluster trusts for admins' kubectl sign-in (\"\" = off)."
  default     = ""
}

variable "cluster_oidc_client_id" {
  type        = string
  description = "Keycloak client of the kubectl sign-in (public, device login)."
  default     = "kubernetes"
}

variable "cluster_admin_roles" {
  type        = string
  description = "Coder roles (comma-separated) whose workspaces get the cluster kubeconfig."
  default     = "owner"
}

data "coder_parameter" "cpu" {
  name         = "cpu"
  display_name = "CPU"
  description  = "The number of CPU cores"
  default      = "2"
  icon         = "/icon/memory.svg"
  mutable      = true
  option {
    name  = "2 Cores"
    value = "2"
  }
  option {
    name  = "4 Cores"
    value = "4"
  }
  option {
    name  = "6 Cores"
    value = "6"
  }
  option {
    name  = "8 Cores"
    value = "8"
  }
}

data "coder_parameter" "memory" {
  name         = "memory"
  display_name = "Memory"
  description  = "The amount of memory in GB"
  default      = "2"
  icon         = "/icon/memory.svg"
  mutable      = true
  option {
    name  = "2 GB"
    value = "2"
  }
  option {
    name  = "4 GB"
    value = "4"
  }
  option {
    name  = "6 GB"
    value = "6"
  }
  option {
    name  = "8 GB"
    value = "8"
  }
}

data "coder_parameter" "home_disk_size" {
  name         = "home_disk_size"
  display_name = "Home disk size"
  description  = "The size of the home disk in GB"
  default      = "10"
  type         = "number"
  icon         = "/emojis/1f4be.png"
  mutable      = false
  validation {
    min = 1
    max = 99999
  }
}

provider "kubernetes" {
  config_path = var.use_kubeconfig == true ? "~/.kube/config" : null
}

data "coder_workspace" "me" {}
data "coder_workspace_owner" "me" {}

locals {
  # The owner is an admin (one of cluster_admin_roles) and the cluster sign-in is on.
  cluster_admin = var.cluster_oidc_issuer != "" && length(setintersection(
    toset([for r in data.coder_workspace_owner.me.rbac_roles : r.name]),
    toset([for r in split(",", var.cluster_admin_roles) : trimspace(r) if trimspace(r) != ""]),
  )) > 0
  cluster_kubeconfig = "/home/coder/.kube/coder-cluster.yaml"
}

resource "coder_agent" "main" {
  os             = "linux"
  arch           = "amd64"

  display_apps {
    web_terminal = true
    vscode       = false
    ssh_helper   = false
  }

  # Admins' terminals, k9s and apps find the cluster sign-in first, then their own ~/.kube/config.
  env = local.cluster_admin ? { KUBECONFIG = "${local.cluster_kubeconfig}:/home/coder/.kube/config" } : {}

  startup_script = <<-EOT
    set -e

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
      mkdir -p ~/.config/btop && echo "update_ms = 100" > "$BTOP_CONF"
    elif grep -qx 'update_ms = 2000' "$BTOP_CONF"; then
      sed -i 's/^update_ms = 2000$/update_ms = 100/' "$BTOP_CONF"
    fi

    # tmux (in the workspace image): the bundled configuration, for users who have none yet.
    if command -v tmux-mirror-setup > /dev/null && [ ! -e ~/.config/tmux ] && [ ! -e ~/.tmux.conf ]; then
      tmux-mirror-setup > /tmp/tmux-mirror-setup.log 2>&1 || true
    fi

    # ~/.cert/<owner>.p12 is installed by the "Certificates" script (certificates.tf) from Settings > Certificates.

    # Admins: kubectl and k9s on this cluster. ~/.kube/coder-cluster.yaml (this template's own file, rewritten at
    # every start; KUBECONFIG lists it before ~/.kube/config, which is never touched) has the context
    # <owner>@coder-cluster. The first kubectl command signs in: it prints a Keycloak link and code, which the admin
    # opens in their own browser and signs in to as themselves. The tokens are kept in /tmp (gone when the workspace
    # restarts) and last as long as that Keycloak session. Someone who is no longer an admin loses the file.
    if [ "${local.cluster_admin}" = "true" ]; then
      mkdir -p ~/.kube /tmp/coder-kube-signin && chmod 700 ~/.kube /tmp/coder-kube-signin
      cat > ${local.cluster_kubeconfig}.tmp << 'KUBECONFIG_FILE'
# Written by the workspace template at every start (cluster sign-in for admins); edits here are replaced.
apiVersion: v1
kind: Config
clusters:
- name: coder-cluster
  cluster:
    server: https://kubernetes.default.svc
    certificate-authority: /var/run/secrets/kubernetes.io/serviceaccount/ca.crt
contexts:
- name: ${data.coder_workspace_owner.me.name}@coder-cluster
  context:
    cluster: coder-cluster
    user: ${data.coder_workspace_owner.me.name}@keycloak
    namespace: ${var.namespace}
current-context: ${data.coder_workspace_owner.me.name}@coder-cluster
users:
- name: ${data.coder_workspace_owner.me.name}@keycloak
  user:
    exec:
      apiVersion: client.authentication.k8s.io/v1
      command: kubectl
      args:
      - oidc-login
      - get-token
      - --oidc-issuer-url=${var.cluster_oidc_issuer}
      - --oidc-client-id=${var.cluster_oidc_client_id}
      - --grant-type=device-code
      - --skip-open-browser
      - --token-cache-dir=/tmp/coder-kube-signin
      interactiveMode: IfAvailable
      provideClusterInfo: false
KUBECONFIG_FILE
      chmod 600 ${local.cluster_kubeconfig}.tmp && mv ${local.cluster_kubeconfig}.tmp ${local.cluster_kubeconfig}
    else
      rm -f ${local.cluster_kubeconfig}
    fi

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
    sudo ln -sf /usr/share/novnc/vnc.html /usr/share/novnc/index.html

    # Configure XFCE (or the image's own desktop session, such as GNOME) to start on VNC launch
    # TigerVNC >= 1.14 reads ~/.config/tigervnc (the legacy ~/.vnc fails to migrate),
    # and the session ends when xstartup returns, so the desktop must be exec'd in the foreground.
    mkdir -p ~/.config/tigervnc
    cat << 'VNC_SCRIPT' > ~/.config/tigervnc/xstartup
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
    if [ "$IMAGE_DESKTOP" != none ] && ! pgrep -f "^Xtigervnc :1 " > /dev/null; then
      rm -f "$VNC_SOCK" /tmp/.X1-lock /tmp/.X11-unix/X1
      touch ~/.Xauthority && chmod 600 ~/.Xauthority
      xauth -f ~/.Xauthority add :1 MIT-MAGIC-COOKIE-1 "$(mcookie)"
      # -FrameRate 60: updates as often as noVNC can show them.
      Xtigervnc :1 -auth ~/.Xauthority -geometry 1280x800 -depth 24 -rfbport -1 -rfbunixpath "$VNC_SOCK" \
        -SecurityTypes None -AlwaysShared -FrameRate 60 -desktop "$(hostname)" > /tmp/xvnc.log 2>&1 &
      for i in $(seq 1 50); do [ -S "$VNC_SOCK" ] && break; sleep 0.2; done
      DISPLAY=:1 XAUTHORITY=~/.Xauthority setsid ~/.config/tigervnc/xstartup > /tmp/xstartup.log 2>&1 < /dev/null &
    fi

    # websockify serves noVNC on 127.0.0.1 only and connects to the VNC socket: the only way in is Coder's app proxy,
    # which signs users in. Coder strips the /@user/workspace.agent/apps/novnc prefix before proxying, so the stock web
    # root works as-is.
    # (By its port: pgrep -f would match this script, whose text contains the pattern.)
    if [ "$IMAGE_DESKTOP" != none ] && ! curl -s -o /dev/null http://127.0.0.1:6901/; then
      websockify --web=/usr/share/novnc/ --unix-target="$VNC_SOCK" 127.0.0.1:6901 > /tmp/novnc.log 2>&1 &
    fi

    # Windows of marimo notebooks (tkinter): a second, private X display (:2: no desktop, no window manager, on its
    # own Unix socket), sized by the notebook to the window it shows. coder_marimo_tk (marimo, below) opens tkinter
    # windows there and shows each one live in its cell's output: the minimal noVNC page below, through the hidden
    # app "marimo-windows" (websockify on 127.0.0.1:6902).
    APPS_SOCK=/tmp/coder-vnc/apps.sock
    if ! pgrep -f "^Xtigervnc :2 " > /dev/null; then
      rm -f "$APPS_SOCK" /tmp/.X2-lock /tmp/.X11-unix/X2
      touch ~/.Xauthority && chmod 600 ~/.Xauthority
      xauth -f ~/.Xauthority add :2 MIT-MAGIC-COOKIE-1 "$(mcookie)"
      Xtigervnc :2 -auth ~/.Xauthority -geometry 640x480 -depth 24 -rfbport -1 -rfbunixpath "$APPS_SOCK" \
        -SecurityTypes None -AlwaysShared -FrameRate 60 -desktop marimo-windows > /tmp/xvnc-apps.log 2>&1 &
      for i in $(seq 1 50); do [ -S "$APPS_SOCK" ] && break; sleep 0.2; done
    fi
    APPS_WEB=/tmp/coder-vnc/apps-web
    mkdir -p "$APPS_WEB" && ln -sfn /usr/share/novnc/core "$APPS_WEB/core" && ln -sfn /usr/share/novnc/vendor "$APPS_WEB/vendor"
    cat << 'APPS_VIEW' > "$APPS_WEB/index.html"
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
      websockify --web="$APPS_WEB" --unix-target="$APPS_SOCK" 127.0.0.1:6902 > /tmp/novnc-apps.log 2>&1 &
    fi

    # 2. Install & Start code-server
    if [ ! -d "/tmp/code-server" ]; then
      curl -fsSL https://code-server.dev/install.sh | sh -s -- --method=standalone --prefix=/tmp/code-server
    fi
    # Probe the port rather than pgrep -f: the agent runs this script as `sh -c '<script>'`,
    # so `pgrep -f` matches the script's own command line and never starts anything.
    if ! curl -fs http://localhost:13337/healthz > /dev/null; then
      /tmp/code-server/bin/code-server --auth none --port 13337 >/tmp/code-server.log 2>&1 &
    fi

    # 3. Install & Start Filebrowser
    if ! command -v filebrowser >/dev/null; then
      curl -fsSL https://raw.githubusercontent.com/filebrowser/get/master/get.sh | sudo bash
    fi
    # Path-based apps are proxied with their full /@user/workspace.agent/apps/<slug> prefix
    # intact, so filebrowser must serve under it or its assets 404 (endless spinner).
    # The agent name ("main") is hardcoded: referencing coder_agent.main here would be a cycle.
    if ! pgrep -x filebrowser > /dev/null; then
      filebrowser --noauth --root /home/coder --port 13339 \
        --baseurl "/@${data.coder_workspace_owner.me.name}/${data.coder_workspace.me.name}.main/apps/filebrowser" \
        >/tmp/filebrowser.log 2>&1 &
    fi

    # 4. marimo (Python notebooks), with no network: the workspace image has it with everything it needs in /opt/marimo
    # (marimo and its extras - SQL, charts, formatting, language server, uv - and a data stack; tkinter). Each user's
    # notebooks run in ~/.marimo-venv, which sees all of /opt/marimo (a .pth file) and takes the packages they add
    # (pip or uv; with no network, only what is already there). It is (re)made when missing, when an older one installed
    # marimo itself from the internet, or when the image's Python changes.
    MARIMO_VENV=/home/coder/.marimo-venv
    MARIMO_BASE="/@${data.coder_workspace_owner.me.name}/${data.coder_workspace.me.name}.main/apps/marimo"
    if [ -x /opt/marimo/bin/marimo ]; then
      MARIMO_STAMP="$(python3 -V) /opt/marimo $(/opt/marimo/bin/marimo --version)"
      if [ "$(cat "$MARIMO_VENV/.coder-workspace" 2>/dev/null)" != "$MARIMO_STAMP" ]; then
        rm -rf "$MARIMO_VENV"
        python3 -m venv "$MARIMO_VENV"
        /opt/marimo/bin/python -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])' \
          > "$("$MARIMO_VENV/bin/python" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')/coder-workspace-marimo.pth"
        echo "$MARIMO_STAMP" > "$MARIMO_VENV/.coder-workspace"
      fi
      # tkinter windows show in the notebook and stop with marimo's Stop button: coder_marimo_tk, loaded through the .pth.
      MARIMO_SITE="$("$MARIMO_VENV/bin/python" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"
      cat << 'CODER_MARIMO_TK' > "$MARIMO_SITE/coder_marimo_tk.py"
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
      grep -qx "import coder_marimo_tk" "$MARIMO_SITE/coder-workspace-marimo.pth" 2>/dev/null \
        || echo "import coder_marimo_tk" >> "$MARIMO_SITE/coder-workspace-marimo.pth"
      # marimo installs missing packages with the manager in its settings: one that is not installed here (pixi, rye,
      # poetry) cannot work, so it falls back to uv (in /opt/marimo). An installed choice is kept.
      MARIMO_TOML=~/.config/marimo/marimo.toml
      for m in pixi rye poetry; do
        if grep -qx "manager = \"$m\"" "$MARIMO_TOML" 2>/dev/null && ! command -v "$m" > /dev/null; then
          sed -i "s/^manager = \"$m\"$/manager = \"uv\"/" "$MARIMO_TOML"
        fi
      done
      # marimo's dark theme by default: set once per user (a later change in marimo's settings is kept).
      if [ ! -e ~/.config/marimo/.coder-workspace-dark ]; then
        mkdir -p ~/.config/marimo
        /opt/marimo/bin/python - "$MARIMO_TOML" << 'MARIMO_DARK' && touch ~/.config/marimo/.coder-workspace-dark
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
      if ! curl -fs "http://localhost:2718$MARIMO_BASE/health" > /dev/null; then
        DISPLAY=:1 XAUTHORITY=/home/coder/.Xauthority CODER_MARIMO_TK_DISPLAY=:2 \
        CODER_MARIMO_TK_VIEW="/@${data.coder_workspace_owner.me.name}/${data.coder_workspace.me.name}.main/apps/marimo-windows/" \
        PATH="$MARIMO_VENV/bin:/opt/marimo/bin:$PATH" PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_RETRIES=0 PIP_TIMEOUT=10 \
          UV_HTTP_TIMEOUT=10 "$MARIMO_VENV/bin/python" -m marimo edit --headless --host 127.0.0.1 --port 2718 \
          --no-token --skip-update-check --base-url "$MARIMO_BASE" /home/coder/notebooks >/tmp/marimo.log 2>&1 &
      fi
    else
      echo "marimo is not in this image (/opt/marimo): skipped"
    fi
  EOT

  metadata {
    display_name = "CPU Usage"
    key          = "0_cpu_usage"
    script       = "coder stat cpu"
    interval     = 10
    timeout      = 1
  }

  metadata {
    display_name = "RAM Usage"
    key          = "1_ram_usage"
    script       = "coder stat mem"
    interval     = 10
    timeout      = 1
  }

  metadata {
    display_name = "Home Disk"
    key          = "3_home_disk"
    script       = "coder stat disk --path $${HOME}"
    interval     = 60
    timeout      = 1
  }

  metadata {
    display_name = "CPU Usage (Host)"
    key          = "4_cpu_usage_host"
    script       = "coder stat cpu --host"
    interval     = 10
    timeout      = 1
  }

  metadata {
    display_name = "Memory Usage (Host)"
    key          = "5_mem_usage_host"
    script       = "coder stat mem --host"
    interval     = 10
    timeout      = 1
  }

  metadata {
    display_name = "Load Average (Host)"
    key          = "6_load_host"
    script       = <<EOT
      echo "`cat /proc/loadavg | awk '{ print $1 }'` `nproc`" | awk '{ printf "%0.2f", $1/$2 }'
    EOT
    interval = 60
    timeout  = 1
  }
}

resource "coder_app" "marimo" {
  agent_id     = coder_agent.main.id
  slug         = "marimo"
  display_name = "marimo"
  icon         = "/__icons/marimo.svg"   # files/icons/marimo.svg, served by templates/icons.yaml
  # Path-based, like the other apps. marimo serves under the full app path (--base-url in the
  # startup script), so the URL carries it too ("main" = agent name).
  url          = "http://localhost:2718/@${data.coder_workspace_owner.me.name}/${data.coder_workspace.me.name}.main/apps/marimo/"
  subdomain    = false
  share        = "owner"

  healthcheck {
    url       = "http://localhost:2718/@${data.coder_workspace_owner.me.name}/${data.coder_workspace.me.name}.main/apps/marimo/health"
    interval  = 5
    threshold = 60
  }
}

# The windows of marimo notebooks (tkinter), shown in their cells' output (coder_marimo_tk): not a button of its own.
resource "coder_app" "marimo_windows" {
  agent_id     = coder_agent.main.id
  slug         = "marimo-windows"
  display_name = "marimo windows"
  icon         = "/__icons/marimo.svg"
  url          = "http://localhost:6902"
  subdomain    = false
  share        = "owner"
  hidden       = true
}

resource "coder_app" "novnc" {
  agent_id     = coder_agent.main.id
  slug         = "novnc"
  display_name = "Desktop"
  icon         = "/icon/desktop.svg" # Coder's own icon (served by the dashboard; the noVNC logo was on the internet)
  # Path-based (no wildcard DNS/route needed). noVNC opens its websocket relative to the host
  # root, so pass the prefixed path explicitly ("main" = agent name, as in filebrowser above).
  url          = "http://localhost:6901?autoconnect=true&resize=remote&compression=0&path=@${data.coder_workspace_owner.me.name}/${data.coder_workspace.me.name}.main/apps/novnc/websockify"
  subdomain    = false
  share        = "owner"

  healthcheck {
    url       = "http://localhost:6901/"
    interval  = 5
    threshold = 3
  }
}

resource "coder_app" "filebrowser" {
  agent_id     = coder_agent.main.id
  slug         = "filebrowser"
  display_name = "File Browser"
  icon         = "/icon/folder.svg"
  url          = "http://localhost:13339"
  subdomain    = false
  share        = "owner"

  healthcheck {
    url       = "http://localhost:13339/health"
    interval  = 5
    threshold = 3
  }
}

resource "coder_app" "code-server" {
  agent_id     = coder_agent.main.id
  slug         = "code-server"
  display_name = "code-server"
  icon         = "/icon/code.svg"
  url          = "http://localhost:13337?folder=/home/coder"
  subdomain    = false
  share        = "owner"

  healthcheck {
    url       = "http://localhost:13337/healthz"
    interval  = 3
    threshold = 10
  }
}

resource "kubernetes_persistent_volume_claim_v1" "home" {
  metadata {
    name      = "coder-${data.coder_workspace.me.id}-home"
    namespace = var.namespace
    labels = {
      "app.kubernetes.io/name"     = "coder-pvc"
      "app.kubernetes.io/instance" = "coder-pvc-${data.coder_workspace.me.id}"
      "app.kubernetes.io/part-of"  = "coder"
      "com.coder.resource"         = "true"
      "com.coder.workspace.id"     = data.coder_workspace.me.id
      "com.coder.workspace.name"   = data.coder_workspace.me.name
      "com.coder.user.id"          = data.coder_workspace_owner.me.id
      "com.coder.user.username"    = data.coder_workspace_owner.me.name
    }
    annotations = {
      "com.coder.user.email" = data.coder_workspace_owner.me.email
    }
  }
  wait_until_bound = false

  spec {
    access_modes = ["ReadWriteOnce"]
    resources {
      requests = {
        storage = "${data.coder_parameter.home_disk_size.value}Gi"
      }
    }
  }

  # The parameter is the size the workspace was created with; the disk can grow later (the workspace's ⋮ menu >
  # Expand disk, done by the platform service), so later builds leave the volume's size alone.
  lifecycle {
    ignore_changes = [spec[0].resources]
  }
}

resource "kubernetes_deployment_v1" "main" {
  count = data.coder_workspace.me.start_count
  depends_on = [
    kubernetes_persistent_volume_claim_v1.home
  ]
  wait_for_rollout = false
  metadata {
    name      = "coder-${data.coder_workspace.me.id}"
    namespace = var.namespace
    labels = {
      "app.kubernetes.io/name"     = "coder-workspace"
      "app.kubernetes.io/instance" = "coder-workspace-${data.coder_workspace.me.id}"
      "app.kubernetes.io/part-of"  = "coder"
      "com.coder.resource"         = "true"
      "com.coder.workspace.id"     = data.coder_workspace.me.id
      "com.coder.workspace.name"   = data.coder_workspace.me.name
      "com.coder.user.id"          = data.coder_workspace_owner.me.id
      "com.coder.user.username"    = data.coder_workspace_owner.me.name
    }
    annotations = {
      "com.coder.user.email" = data.coder_workspace_owner.me.email
    }
  }

  spec {
    replicas = 1
    selector {
      match_labels = {
        "app.kubernetes.io/name"     = "coder-workspace"
        "app.kubernetes.io/instance" = "coder-workspace-${data.coder_workspace.me.id}"
        "app.kubernetes.io/part-of"  = "coder"
        "com.coder.resource"         = "true"
        "com.coder.workspace.id"     = data.coder_workspace.me.id
        "com.coder.workspace.name"   = data.coder_workspace.me.name
        "com.coder.user.id"          = data.coder_workspace_owner.me.id
        "com.coder.user.username"    = data.coder_workspace_owner.me.name
      }
    }
    strategy {
      type = "Recreate"
    }

    template {
      metadata {
        labels = {
          "app.kubernetes.io/name"     = "coder-workspace"
          "app.kubernetes.io/instance" = "coder-workspace-${data.coder_workspace.me.id}"
          "app.kubernetes.io/part-of"  = "coder"
          "com.coder.resource"         = "true"
          "com.coder.workspace.id"     = data.coder_workspace.me.id
          "com.coder.workspace.name"   = data.coder_workspace.me.name
          "com.coder.user.id"          = data.coder_workspace_owner.me.id
          "com.coder.user.username"    = data.coder_workspace_owner.me.name
        }
      }
      spec {
        security_context {
          run_as_user     = 1000
          fs_group        = 1000
          run_as_non_root = true
        }

        # Trust the local PKI: append the cluster root CA (secret "tls-ca") to the
        # image's stock bundle so curl, the agent, git and apt can reach
        # https://coder.xxx.com while public sites still verify.
        init_container {
          name              = "trust-local-ca"
          image             = "codercom/enterprise-base:ubuntu"
          image_pull_policy = "IfNotPresent"
          command = [
            "sh", "-c",
            "cat /etc/ssl/certs/ca-certificates.crt /local-ca/ca.crt > /ca-bundle/ca-certificates.crt",
          ]
          volume_mount {
            name       = "local-ca"
            mount_path = "/local-ca"
            read_only  = true
          }
          volume_mount {
            name       = "ca-bundle"
            mount_path = "/ca-bundle"
          }
        }

        container {
          name              = "dev"
          image             = "codercom/enterprise-base:ubuntu"
          # The workspace image is versioned (a new build gets a new tag), and offline there is nothing to pull from.
          image_pull_policy = "IfNotPresent"
          # No core dumps from the agent or anything it starts (terminals, the desktop, apps).
          command = ["sh", "-c", join("\n", ["ulimit -c 0", coder_agent.main.init_script])]
          security_context {
            run_as_user = "1000"
          }
          volume_mount {
            name       = "ca-bundle"
            mount_path = "/etc/ssl/certs/ca-certificates.crt"
            sub_path   = "ca-certificates.crt"
            read_only  = true
          }
          env {
            name  = "CODER_AGENT_TOKEN"
            value = coder_agent.main.token
          }
          resources {
            requests = {
              "cpu"    = "250m"
              "memory" = "512Mi"
            }
            limits = {
              "cpu"    = "${data.coder_parameter.cpu.value}"
              "memory" = "${data.coder_parameter.memory.value}Gi"
            }
          }
          volume_mount {
            mount_path = "/home/coder"
            name       = "home"
            read_only  = false
          }
          # Firefox's tabs share memory through /dev/shm; a pod's default 64 MiB makes them crash.
          volume_mount {
            mount_path = "/dev/shm"
            name       = "dshm"
          }
        }

        volume {
          name = "dshm"
          empty_dir {
            medium     = "Memory"
            size_limit = "1Gi"
          }
        }

        volume {
          name = "home"
          persistent_volume_claim {
            claim_name = kubernetes_persistent_volume_claim_v1.home.metadata.0.name
            read_only  = false
          }
        }

        volume {
          name = "local-ca"
          secret {
            secret_name = "tls-ca"
            items {
              key  = "ca.crt"
              path = "ca.crt"
            }
          }
        }

        volume {
          name = "ca-bundle"
          empty_dir {}
        }

        affinity {
          pod_anti_affinity {
            preferred_during_scheduling_ignored_during_execution {
              weight = 1
              pod_affinity_term {
                topology_key = "kubernetes.io/hostname"
                label_selector {
                  match_expressions {
                    key      = "app.kubernetes.io/name"
                    operator = "In"
                    values   = ["coder-workspace"]
                  }
                }
              }
            }
          }
        }
      }
    }
  }
}

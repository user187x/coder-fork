"""Lock Down: admins jail a workspace, for instance when an agent in it runs away. Three modes:

  network   the workspace can reach nothing but Coder: its agent stays connected, so admins can still open its
            terminal and apps and see what runs in it; everything else in or out is refused, open connections too
  sudo      nobody in the workspace can use sudo any more (a last sudoers rule denies every command)
  complete  both

It works on a running workspace, without a restart, so its processes keep running for the admins to inspect, and on a
stopped one, from its next start on. It stays until an admin lifts it.

  GET  /__coder-ui/api/lockdown                  admins: every locked workspace {workspace id: state}
  GET  /__coder-ui/api/lockdown/<workspace id>   the workspace's state (its owner and admins)
  POST /__coder-ui/api/lockdown/<workspace id>   admins: {mode: network | sudo | complete | off}

The settings are the ConfigMap coder-lockdown (made at the first lock; Helm and Argo CD do not manage it): key
<workspace id> = "network,sudo" (what the workspace template's pod reads at start: its init container sets Network,
a start script that blocks login sets Sudo), key <workspace id>.meta = who set it, when, and the workspace's name.
A running pod gets the setting at once from an ephemeral container: the workspace's own image, root with NET_ADMIN
(the pod's network namespace) and SYS_PTRACE (the workspace container's files, through its processes), running
lockdown.sh. The pod's annotation platform.coder/lockdown says what was applied; a watcher keeps every running
workspace as the ConfigMap says (a workspace from an older template version, a restart, or this service's own).
"""
import datetime
import json
import os
import re
import threading
import time
import urllib.parse

BASE = "/__coder-ui"
NAMESPACE = os.environ.get("CODER_NAMESPACE", "coder")
CONFIGMAP = "coder-lockdown"
ANNOTATION = "platform.coder/lockdown"
MODES = {"network": "network", "sudo": "sudo", "complete": "network,sudo", "off": "off"}
WATCH_SECONDS = 15
APPLY_TIMEOUT = 120
SCRIPT = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "lockdown.sh")).read()

LOG = KUBE = WORKSPACE_INFO = None
ADMIN_ROLES = set()
CODER_HOST = ""
LOCKS = {}  # workspace id -> lock (one change at a time per workspace)
LOCKS_GUARD = threading.Lock()


def setup(log, kube, workspace_info, admin_roles, access_url):
    global LOG, KUBE, WORKSPACE_INFO, ADMIN_ROLES, CODER_HOST
    LOG, KUBE, WORKSPACE_INFO, ADMIN_ROLES = log, kube, workspace_info, set(admin_roles)
    CODER_HOST = urllib.parse.urlsplit(access_url).hostname or ""


def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def lock_of(wid):
    with LOCKS_GUARD:
        return LOCKS.setdefault(wid, threading.Lock())


# --------------------------------------------------------------------------- the settings (ConfigMap)
def _configmap():
    status, cm = KUBE.call("GET", "/api/v1/namespaces/%s/configmaps/%s" % (NAMESPACE, CONFIGMAP))
    if status == 404:
        return None
    if status != 200 or not isinstance(cm, dict):
        raise RuntimeError("The Lock Down settings could not be read (Kubernetes answered %s)." % status)
    return cm


def settings():
    """{workspace id: {modes, by, at, workspace, owner}} of every locked workspace."""
    data = (_configmap() or {}).get("data") or {}
    out = {}
    for key, modes in data.items():
        if key.endswith(".meta") or modes in ("", "off"):
            continue
        try:
            meta = json.loads(data.get(key + ".meta") or "{}")
        except ValueError:
            meta = {}
        out[key] = dict(meta, modes=modes.split(","))
    return out


def save(wid, modes, meta):
    """Sets the workspace's modes ("off": removed), retried when someone else changed the ConfigMap meanwhile."""
    for _ in range(5):
        cm = _configmap()
        if cm is None:
            body = {"apiVersion": "v1", "kind": "ConfigMap",
                    "metadata": {"name": CONFIGMAP, "namespace": NAMESPACE,
                                 "labels": {"app.kubernetes.io/part-of": "coder",
                                            "coder-ui-updates/managed": "lockdown"}},
                    "data": {}}
            if modes != "off":
                body["data"] = {wid: modes, wid + ".meta": json.dumps(meta)}
            status, _ = KUBE.call("POST", "/api/v1/namespaces/%s/configmaps" % NAMESPACE, body)
        else:
            data = dict(cm.get("data") or {})
            data.pop(wid, None)
            data.pop(wid + ".meta", None)
            if modes != "off":
                data[wid] = modes
                data[wid + ".meta"] = json.dumps(meta)
            cm["data"] = data
            status, _ = KUBE.call("PUT", "/api/v1/namespaces/%s/configmaps/%s" % (NAMESPACE, CONFIGMAP), cm)
        if status in (200, 201):
            return
        if status != 409:
            raise RuntimeError("The Lock Down settings could not be saved (Kubernetes answered %s)." % status)
        time.sleep(0.3)
    raise RuntimeError("The Lock Down settings kept changing under this save: try again.")


# --------------------------------------------------------------------------- the running pod
def running_pods(wid=None):
    """Running workspace pods (of one workspace, or all) whose workspace container runs."""
    label = "com.coder.workspace.id" + ("=" + wid if wid else "")
    status, pods = KUBE.call("GET", "/api/v1/namespaces/%s/pods?labelSelector=%s" % (
        NAMESPACE, urllib.parse.quote(label)))
    out = []
    for p in (pods.get("items") or []) if status == 200 and isinstance(pods, dict) else []:
        st = p.get("status") or {}
        dev = next((c for c in st.get("containerStatuses") or [] if c.get("name") == "dev"), None)
        if st.get("phase") == "Running" and dev and (dev.get("state") or {}).get("running") \
                and not (p.get("metadata") or {}).get("deletionTimestamp"):
            out.append(p)
    return out


def applied(pod):
    return ((pod.get("metadata") or {}).get("annotations") or {}).get(ANNOTATION, "off")


def apply(pod, modes):
    """Runs lockdown.sh in an ephemeral container of the running pod: (ok, message)."""
    meta = pod["metadata"]
    name = "lockdown-%d" % int(time.time() * 1000)
    image = next((c["image"] for c in pod["spec"]["containers"] if c["name"] == "dev"), None)
    container = {
        "name": name, "image": image, "imagePullPolicy": "IfNotPresent", "targetContainerName": "dev",
        "command": ["sh", "-c", SCRIPT, "lockdown", modes, CODER_HOST, "/proc/1/root"],
        "securityContext": {"runAsUser": 0, "runAsNonRoot": False, "allowPrivilegeEscalation": False,
                            "capabilities": {"add": ["NET_ADMIN", "NET_RAW", "SYS_PTRACE"]}},
        "resources": {},
    }
    path = "/api/v1/namespaces/%s/pods/%s" % (NAMESPACE, meta["name"])
    status, res = KUBE.call("PATCH", path + "/ephemeralcontainers",
                            {"spec": {"ephemeralContainers": [container]}},
                            patch_type="application/strategic-merge-patch+json")
    if status != 200:
        return False, "The workspace's pod refused the Lock Down container (%s): %s" % (
            status, str((res or {}).get("message") if isinstance(res, dict) else res)[:300])
    deadline = time.time() + APPLY_TIMEOUT
    code = None
    while time.time() < deadline:
        status, p = KUBE.call("GET", path)
        cs = next((c for c in ((p or {}).get("status") or {}).get("ephemeralContainerStatuses") or []
                   if c.get("name") == name), None) if status == 200 else None
        term = ((cs or {}).get("state") or {}).get("terminated")
        if term:
            code = term.get("exitCode")
            break
        time.sleep(1)
    status, out = KUBE.call("GET", path + "/log?container=%s&tailLines=20" % name)
    message = (out if isinstance(out, str) else "").strip().splitlines()
    message = message[-1] if message else ""
    if code != 0:
        return False, "Lock Down did not apply in the running workspace (%s)%s" % (
            "timed out" if code is None else "exit %s" % code, ": " + message if message else "")
    KUBE.call("PATCH", path, {"metadata": {"annotations": {ANNOTATION: modes}}},
              patch_type="application/merge-patch+json")
    return True, message


def reconcile(wid, modes):
    """Every running pod of the workspace as `modes` says: [(pod, ok, message)]."""
    results = []
    with lock_of(wid):
        for pod in running_pods(wid):
            if applied(pod) == modes:
                results.append((pod["metadata"]["name"], True, "already in force"))
                continue
            ok, message = apply(pod, modes)
            results.append((pod["metadata"]["name"], ok, message))
            if LOG:
                LOG(event="lockdown-applied", workspace=wid, pod=pod["metadata"]["name"], modes=modes, ok=ok,
                    message=message[:200])
    return results


def watch():
    while True:
        try:
            wanted = {wid: ",".join(s["modes"]) for wid, s in settings().items()}
            for pod in running_pods():
                wid = ((pod.get("metadata") or {}).get("labels") or {}).get("com.coder.workspace.id")
                if wid and applied(pod) != wanted.get(wid, "off"):
                    reconcile(wid, wanted.get(wid, "off"))
        except Exception as e:  # noqa: BLE001 - tried again on the next round
            if LOG:
                LOG(event="lockdown-watch-failed", error=str(e)[:300])
        time.sleep(WATCH_SECONDS)


def start():
    if KUBE and KUBE.ok:
        threading.Thread(target=watch, daemon=True, name="lockdown").start()


# --------------------------------------------------------------------------- the API
def read_json(h):
    n = int(h.headers.get("Content-Length") or 0)
    data = json.loads(h.rfile.read(min(n, 65536)) or b"{}") if n else {}
    if not isinstance(data, dict):
        raise ValueError("Expected a JSON object.")
    return data


def state_of(wid):
    s = settings().get(wid)
    pods = running_pods(wid)
    return {"locked": bool(s), "modes": (s or {}).get("modes", []), "by": (s or {}).get("by"),
            "at": (s or {}).get("at"), "running": bool(pods),
            "inForce": all(applied(p) == ",".join((s or {}).get("modes") or ["off"]) for p in pods) if pods else None}


def handle(h, method, path, query):
    """True when it answered."""
    if not path.startswith(BASE + "/api/lockdown"):
        return False
    if method == "POST" and not h.csrf_ok():
        h.send_json(403, {"error": "Request rejected (cross-site protection)."})
        return True
    user, err = h.user()
    if err:
        return True
    admin = bool(set(user["roles"]) & ADMIN_ROLES)
    try:
        if path == BASE + "/api/lockdown" and method == "GET":
            if not admin:
                raise PermissionError
            return h.send_json(200, {"workspaces": settings()}) or True
        m = re.match(r"^%s/api/lockdown/([0-9a-f-]{36})$" % BASE, path)
        if not m:
            return h.send_json(404, {"error": "not found"}) or True
        wid = m.group(1)
        ws = WORKSPACE_INFO(h.token(), wid)
        if not ws:
            raise LookupError("No such workspace.")
        if method == "GET":
            if not admin and ws.get("owner_id") != user["id"]:
                raise PermissionError
            return h.send_json(200, dict(state_of(wid), canChange=admin)) or True
        if method != "POST":
            return h.send_json(405, {"error": "method not allowed"}) or True
        if not admin:
            raise PermissionError
        mode = str(read_json(h).get("mode") or "")
        if mode not in MODES:
            raise ValueError("A mode: network, sudo, complete or off.")
        modes = MODES[mode]
        save(wid, modes, {"by": user["username"], "at": now(), "workspace": ws.get("name"),
                          "owner": ws.get("owner_name")})
        if LOG:
            LOG(event="lockdown-set", workspace=ws.get("name"), owner=ws.get("owner_name"), id=wid, mode=mode,
                by=user["username"])
        results = reconcile(wid, modes)
        failed = [msg for _, ok, msg in results if not ok]
        return h.send_json(200, dict(state_of(wid), canChange=True, applied="now" if results else "next start",
                                     error=failed[0] if failed else None)) or True
    except PermissionError:
        h.send_json(403, {"error": "Only admins can lock a workspace down; its owner can see whether it is."})
    except LookupError as e:
        h.send_json(404, {"error": str(e)})
    except ValueError as e:
        h.send_json(400, {"error": str(e)})
    except RuntimeError as e:
        h.send_json(503, {"error": str(e)})
    return True

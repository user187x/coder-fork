"""Workspace extras in the platform service: a workspace's own icon, backups of its home volume (to S3 or a file to
download) and growing its home disk. Owners and admins (whoever Coder lets update the workspace) use them from the
workspace's ⋮ menu and its Settings.

  GET  /__coder-ui/api/workspace-icons                     every workspace's own icon {workspace id: icon}
  POST /__coder-ui/api/workspace-icons/<id>                {icon}: set ("" = the template's again)
  GET  /__coder-ui/api/workspace-volumes/<id>              the home volume (size, storage class, can it grow
                                                           online), its backups and the latest disk change
  POST /__coder-ui/api/workspace-volumes/<id>/backup       {target: download | s3}
  GET  /__coder-ui/api/workspace-volumes/<id>/backups/<n>/download
  POST /__coder-ui/api/workspace-volumes/<id>/expand       {size: GB}

Backups: a Job mounts the home volume read-only (beside the running workspace: the volume is ReadWriteOnce, and pods
on its node share it) and streams a .tar.gz of it to the service, which writes it to S3 (Images > Settings) or keeps
it on the image builder's volume for 24 hours. Growing a disk: on a storage class that allows volume expansion
(EBS gp3 / gp2 on EKS) the volume grows in place, and the workspace restarts only if the file system needs it; on
one that does not (local-path, hostPath) the workspace is stopped, its home copied onto a bigger volume, which takes
the old one's name, and started again. The template ignores the volume's size afterwards (lifecycle ignore_changes),
so a later build never shrinks it back.

The Jobs run this file: `python3 workspacevolumes.py archive SRC URL` (a .tar.gz of SRC, PUT to URL, chunked).
"""
import datetime
import http.client
import json
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request

import imagebuilder as ib

BASE = "/__coder-ui"
NAMESPACE = os.environ.get("WORKSPACE_NAMESPACE", ib.NAMESPACE)
CODER_URL = os.environ.get("CODER_URL", "http://coder.coder.svc.cluster.local").rstrip("/")
KEEP_DOWNLOADS = int(os.environ.get("WORKSPACE_BACKUP_KEEP_HOURS", "24")) * 3600
MAX_DISK_GB = int(os.environ.get("WORKSPACE_DISK_MAX_GB", "2000"))   # sizes are decimal GB (10^9 bytes)
ICON_RE = re.compile(r"^(/[A-Za-z0-9_./%+-]{1,500}|https://[^\s\"'<>]{1,500})$")

LOG = HTTP_JSON = KUBE = None
STORE = None
OPS = {}           # workspace id -> the running backup / disk operation (one at a time per workspace)
OPS_LOCK = threading.Lock()


def setup(log, http_json, kube):
    global LOG, HTTP_JSON, KUBE
    LOG, HTTP_JSON, KUBE = log, http_json, kube


def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- storage
class Store:
    def __init__(self, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.path = path
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS icons (workspace_id TEXT PRIMARY KEY, icon TEXT NOT NULL, by TEXT, at TEXT);
                CREATE TABLE IF NOT EXISTS ops (id INTEGER PRIMARY KEY AUTOINCREMENT, workspace_id TEXT, kind TEXT,
                    target TEXT, status TEXT, message TEXT, error TEXT, bytes INTEGER, path TEXT, bucket TEXT,
                    key TEXT, token TEXT, from_gi INTEGER, to_gi INTEGER, mode TEXT, by TEXT, created_at TEXT,
                    finished_at TEXT, label TEXT);
            """)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def icons(self):
        with self.connect() as db:
            return {r["workspace_id"]: r["icon"] for r in db.execute("SELECT workspace_id, icon FROM icons")}

    def set_icon(self, wid, icon, by):
        with self.connect() as db:
            if icon:
                db.execute("INSERT OR REPLACE INTO icons VALUES (?,?,?,?)", (wid, icon, by, now()))
            else:
                db.execute("DELETE FROM icons WHERE workspace_id=?", (wid,))

    def new_op(self, wid, kind, by, **fields):
        cols = ["workspace_id", "kind", "status", "by", "created_at"] + list(fields)
        vals = [wid, kind, "running", by, now()] + list(fields.values())
        with self.connect() as db:
            cur = db.execute("INSERT INTO ops (%s) VALUES (%s)" % (",".join(cols), ",".join("?" * len(cols))), vals)
            return cur.lastrowid

    def update(self, op_id, **fields):
        with self.connect() as db:
            db.execute("UPDATE ops SET %s WHERE id=?" % ",".join("%s=?" % k for k in fields),
                       list(fields.values()) + [op_id])

    def op(self, op_id=None, token=None):
        with self.connect() as db:
            r = db.execute("SELECT * FROM ops WHERE id=?" if op_id else "SELECT * FROM ops WHERE token=?",
                           (op_id or token,)).fetchone()
        return dict(r) if r else None

    def ops(self, wid, kind, limit=5):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM ops WHERE workspace_id=? AND kind=? ORDER BY id DESC "
                                                "LIMIT ?", (wid, kind, limit))]

    def expired_downloads(self):
        cut = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=KEEP_DOWNLOADS)
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM ops WHERE kind='backup' AND target='download' AND "
                                                "path IS NOT NULL AND created_at < ?", (cut.strftime("%Y-%m-%dT%H:%M:%SZ"),))]


def public(op):
    """An operation as the dashboard sees it (no token, no file path)."""
    out = {k: op.get(k) for k in ("id", "kind", "target", "status", "message", "error", "bytes", "bucket", "key",
                                  "mode", "by", "created_at", "finished_at")}
    out["fromGB"], out["toGB"] = op.get("from_gi"), op.get("to_gi")   # (columns named before sizes were GB)
    out["downloadable"] = bool(op.get("path") and op.get("status") == "done" and os.path.exists(op["path"]))
    return out


# --------------------------------------------------------------------------- Coder, as the user
def coder(token, method, path, body=None, timeout=20):
    return HTTP_JSON(CODER_URL + path, method=method, body=body, headers={"Coder-Session-Token": token},
                     timeout=timeout)


def workspace(token, wid):
    status, ws = coder(token, "GET", "/api/v2/workspaces/" + urllib.parse.quote(wid))
    if status != 200 or not isinstance(ws, dict):
        raise LookupError("No such workspace (or you cannot see it).")
    return ws


def can_update(token, ws):
    """Whoever Coder lets update the workspace: its owner, admins (Coder's own permission check)."""
    status, out = coder(token, "POST", "/api/v2/authcheck", {"checks": {"update": {
        "object": {"resource_type": "workspace", "resource_id": ws["id"], "owner_id": ws["owner_id"],
                   "organization_id": ws.get("organization_id", "")},
        "action": "update"}}})
    return status == 200 and bool((out or {}).get("update"))


def is_running(ws):
    b = ws.get("latest_build") or {}
    return b.get("transition") == "start" and b.get("status") in ("running", "starting", "pending")


def build(token, ws, transition):
    """A stop or start build (the same template version), then its end."""
    body = {"transition": transition}
    status, b = coder(token, "POST", "/api/v2/workspaces/%s/builds" % ws["id"], body)
    if status not in (200, 201):
        raise RuntimeError("Coder refused to %s the workspace: %s" % (transition, (b or {}).get("message", status)))
    deadline = time.time() + 1800
    while time.time() < deadline:
        time.sleep(3)
        status, cur = coder(token, "GET", "/api/v2/workspacebuilds/" + b["id"])
        job = (cur or {}).get("job") or {}
        if job.get("status") in ("succeeded",):
            return
        if job.get("status") in ("failed", "canceled"):
            raise RuntimeError("The %s build failed: %s" % (transition, job.get("error") or job.get("status")))
    raise RuntimeError("The %s build did not finish in 30 minutes." % transition)


# --------------------------------------------------------------------------- Kubernetes
def pvc_name(wid):
    return "coder-%s-home" % wid


def get_pvc(name):
    status, pvc = ib.kube_call("GET", "/api/v1/namespaces/%s/persistentvolumeclaims/%s" % (NAMESPACE, name))
    return pvc if status == 200 else None


def gb(q):
    """'25Gi' / '10G' / bytes -> decimal GB, to one place (25Gi = 26.8 GB)."""
    return round(ib_quantity(q) / 1e9, 1)


def ib_quantity(q):
    m = re.match(r"^([0-9.]+)([A-Za-z]*)$", str(q or "").strip())
    if not m:
        return 0
    n, unit = float(m.group(1)), m.group(2)
    mult = {"": 1, "Ki": 1024, "Mi": 1024 ** 2, "Gi": 1024 ** 3, "Ti": 1024 ** 4, "K": 1e3, "k": 1e3, "M": 1e6,
            "G": 1e9, "T": 1e12}.get(unit, 1)
    return n * mult


def volume_info(wid):
    pvc = get_pvc(pvc_name(wid))
    if not pvc:
        return None
    sc_name = (pvc.get("spec") or {}).get("storageClassName") or ""
    expandable = False
    if sc_name:
        status, sc = ib.kube_call("GET", "/apis/storage.k8s.io/v1/storageclasses/" + urllib.parse.quote(sc_name))
        expandable = status == 200 and bool((sc or {}).get("allowVolumeExpansion"))
    cap = ((pvc.get("status") or {}).get("capacity") or {}).get("storage")
    req = (((pvc.get("spec") or {}).get("resources") or {}).get("requests") or {}).get("storage")
    conditions = [c.get("type") for c in (pvc.get("status") or {}).get("conditions") or []]
    return {"name": pvc["metadata"]["name"], "sizeGB": gb(cap or req), "requestedGB": gb(req),
            "storageClass": sc_name, "expandable": expandable, "phase": (pvc.get("status") or {}).get("phase"),
            "conditions": conditions}


def workspace_node(wid):
    """The node of the workspace's running pod: a Job that mounts its volume beside it must run there (a
    ReadWriteOnce volume such as EBS is attached to one node)."""
    sel = urllib.parse.quote("com.coder.workspace.id=%s,app.kubernetes.io/name=coder-workspace" % wid)
    status, pods = ib.kube_call("GET", "/api/v1/namespaces/%s/pods?labelSelector=%s" % (NAMESPACE, sel))
    for p in (pods or {}).get("items") or [] if status == 200 else []:
        if (p.get("status") or {}).get("phase") == "Running" and (p.get("spec") or {}).get("nodeName"):
            return p["spec"]["nodeName"]
    return None


def job_body(name, op_id, containers, volumes, root=False, node=None):
    code = {"name": "code", "configMap": {"name": "coder-ui-updates", "items": [
        {"key": "workspacevolumes.py", "path": "workspacevolumes.py"}] + ib.CODE_ITEMS}}
    ctx = ({"runAsUser": 0, "runAsGroup": 0, "seccompProfile": {"type": "RuntimeDefault"}} if root else
           {"runAsNonRoot": True, "runAsUser": 1000, "runAsGroup": 1000, "seccompProfile": {"type": "RuntimeDefault"}})
    spec = {"restartPolicy": "Never", "securityContext": ctx, "containers": containers, "volumes": volumes + [code]}
    if node:
        spec["nodeName"] = node
    if ib.PULL_SECRETS:
        spec["imagePullSecrets"] = ib.PULL_SECRETS
    return {"apiVersion": "batch/v1", "kind": "Job",
            "metadata": {"name": name, "namespace": NAMESPACE,
                         "labels": {"app.kubernetes.io/part-of": "coder", "coder.workspace-volume-op": str(op_id)}},
            "spec": {"backoffLimit": 0, "ttlSecondsAfterFinished": 3600, "activeDeadlineSeconds": 6 * 3600,
                     "template": {"metadata": {"labels": {"coder.workspace-volume-op": str(op_id)}}, "spec": spec}}}


def run_job(body, timeout=6 * 3600):
    """Starts the Job and waits for it: None when it succeeded, else why it did not."""
    name = body["metadata"]["name"]
    status, out = ib.kube_call("POST", "/apis/batch/v1/namespaces/%s/jobs" % NAMESPACE, body)
    if status not in (200, 201):
        return "could not start the Job (HTTP %s): %s" % (status, str(out)[:300])
    deadline = time.time() + timeout
    try:
        while time.time() < deadline:
            time.sleep(3)
            status, job = ib.kube_call("GET", "/apis/batch/v1/namespaces/%s/jobs/%s" % (NAMESPACE, name))
            st = (job or {}).get("status") or {}
            if st.get("succeeded"):
                return None
            if st.get("failed"):
                return "the Job failed: " + job_log(name)
        return "the Job did not finish in time"
    finally:
        ib.kube_call("DELETE", "/apis/batch/v1/namespaces/%s/jobs/%s?propagationPolicy=Background" % (NAMESPACE, name))


def job_log(name):
    status, pods = ib.kube_call("GET", "/api/v1/namespaces/%s/pods?labelSelector=%s" % (
        NAMESPACE, urllib.parse.quote("job-name=" + name)))
    for p in (pods or {}).get("items") or []:
        try:
            r = KUBE.stream("/api/v1/namespaces/%s/pods/%s/log?tailLines=20" % (NAMESPACE, p["metadata"]["name"]),
                            timeout=20)
            return r.read().decode("utf-8", "replace").strip()[-600:] or "(no output)"
        except Exception:  # noqa: BLE001
            pass
    return "(no log)"


def tools_container(name, command, mounts, root=False):
    sc = {"allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]}}
    if root:
        # copying keeps every file's owner and mode
        sc["capabilities"]["add"] = ["CHOWN", "DAC_OVERRIDE", "DAC_READ_SEARCH", "FOWNER", "FSETID"]
    return {"name": name, "image": ib.TOOLS_IMAGE, "imagePullPolicy": "IfNotPresent", "command": command,
            "securityContext": sc, "volumeMounts": mounts + [{"name": "code", "mountPath": "/app"}],
            "resources": {"requests": {"cpu": "100m", "memory": "128Mi"}, "limits": {"memory": "512Mi"}}}


# --------------------------------------------------------------------------- backups
def start_backup(token, ws, target, user):
    wid = ws["id"]
    if target == "s3":
        cfg = ib.s3_settings(True)
        if not (cfg.get("bucket") and cfg.get("accessKey") and cfg.get("secretKey") and cfg.get("region")):
            raise ValueError("S3 is not set up (Templates > Images > Settings).")
    elif target != "download":
        raise ValueError("A backup goes to S3 or to a download.")
    if not get_pvc(pvc_name(wid)):
        raise ValueError("This workspace has no home volume.")
    with OPS_LOCK:
        if wid in OPS:
            raise ValueError("Another backup or disk change of this workspace is running.")
        tok = secrets.token_urlsafe(32)
        op_id = STORE.new_op(wid, "backup", user, target=target, token=tok, message="Starting the copy",
                             label="%s/%s" % (ws["owner_name"], ws["name"]))
        OPS[wid] = op_id
    threading.Thread(target=run_backup, args=(ws, op_id, tok), daemon=True).start()
    LOG(event="workspace-backup", workspace=ws["name"], owner=ws["owner_name"], target=target, by=user)
    return op_id


def run_backup(ws, op_id, tok):
    wid = ws["id"]
    try:
        url = "%s%s/internal/volumes/%s/archive" % (ib.SERVICE_URL, BASE, tok)
        body = job_body("coder-home-backup-%d" % op_id, op_id, [tools_container(
            "archive", ["python3", "/app/workspacevolumes.py", "archive", "/home/coder", url],
            [{"name": "home", "mountPath": "/home/coder", "readOnly": True}])],
            [{"name": "home", "persistentVolumeClaim": {"claimName": pvc_name(wid), "readOnly": True}}],
            node=workspace_node(wid))
        STORE.update(op_id, message="Copying the home volume")
        err = run_job(body)
        op = STORE.op(op_id)
        if err or op["status"] != "done":
            STORE.update(op_id, status="failed", finished_at=now(),
                         error=(err or op.get("error") or "The archive did not arrive.")[:1000])
    except Exception as e:  # noqa: BLE001
        STORE.update(op_id, status="failed", error=str(e)[:1000], finished_at=now())
    finally:
        with OPS_LOCK:
            OPS.pop(wid, None)


class Chunked:
    """The body of a chunked request (Transfer-Encoding: chunked), as a file to read."""

    def __init__(self, rfile):
        self.rfile, self.left, self.done = rfile, 0, False

    def read(self, n=65536):
        if self.done:
            return b""
        if self.left == 0:
            line = self.rfile.readline(1024).strip()
            size = int(line.split(b";")[0] or b"0", 16)
            if size == 0:
                while self.rfile.readline(1024).strip():
                    pass
                self.done = True
                return b""
            self.left = size
        data = self.rfile.read(min(n, self.left))
        self.left -= len(data)
        if self.left == 0:
            self.rfile.readline(4)
        return data


def receive_archive(h, op):
    """The Job's PUT: the .tar.gz, to S3 or a file (as it arrives; nothing is held in memory)."""
    ws_name = re.sub(r"[^A-Za-z0-9_.-]", "-", op["workspace_id"])
    src = Chunked(h.rfile) if "chunked" in (h.headers.get("Transfer-Encoding") or "").lower() else None
    left = None if src else int(h.headers.get("Content-Length") or 0)

    def chunks():
        nonlocal left
        while True:
            if src:
                d = src.read()
            else:
                if left <= 0:
                    return
                d = h.rfile.read(min(65536, left))
                left -= len(d)
            if not d:
                return
            yield d

    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    total = 0
    try:
        if op["target"] == "s3":
            cfg = ib.s3_settings(True)
            label = re.sub(r"[^A-Za-z0-9_./-]", "-", op.get("label") or ws_name)
            key = "%sworkspaces/%s/%s.tar.gz" % (cfg.get("prefix") or "", label, stamp)
            up = ib.S3Upload(cfg, key)
            try:
                for d in chunks():
                    up.write(d)
                    total += len(d)
                    if total % (256 * 1024 * 1024) < len(d):
                        STORE.update(op["id"], bytes=total)
                up.close()
            except Exception:
                up.abort()
                raise
            STORE.update(op["id"], status="done", bytes=total, bucket=cfg["bucket"], key=key, finished_at=now(),
                         message="In S3")
        else:
            folder = os.path.join(ib.FILES, "workspace-backups")
            os.makedirs(folder, exist_ok=True)
            path = os.path.join(folder, "%d-%s.tar.gz" % (op["id"], stamp))
            with open(path + ".part", "wb") as f:
                for d in chunks():
                    f.write(d)
                    total += len(d)
                    if total % (256 * 1024 * 1024) < len(d):
                        STORE.update(op["id"], bytes=total)
            os.replace(path + ".part", path)
            STORE.update(op["id"], status="done", bytes=total, path=path, finished_at=now(),
                         message="Ready to download (kept %d hours)" % (KEEP_DOWNLOADS // 3600))
        h.send_json(200, {"ok": True, "bytes": total})
    except OSError as e:
        STORE.update(op["id"], status="failed", error=("No room left for the download: %s" % e
                                                       if e.errno == 28 else str(e))[:1000], finished_at=now())
        h.send_json(500, {"error": str(e)})


def cleanup_loop():
    while True:
        try:
            for op in STORE.expired_downloads():
                try:
                    os.remove(op["path"])
                except OSError:
                    pass
                STORE.update(op["id"], path=None, message="Expired (downloads are kept %d hours)" %
                             (KEEP_DOWNLOADS // 3600))
        except Exception as e:  # noqa: BLE001
            LOG(event="workspace-backup-cleanup-failed", error=str(e)[:300])
        time.sleep(600)


# --------------------------------------------------------------------------- disk expansion
def start_expand(token, ws, size, user):
    wid = ws["id"]
    info = volume_info(wid)
    if not info:
        raise ValueError("This workspace has no home volume.")
    if not isinstance(size, int) or size <= info["sizeGB"] or size > MAX_DISK_GB:
        raise ValueError("A whole number of GB larger than now (%g GB), up to %d GB." % (info["sizeGB"], MAX_DISK_GB))
    mode = "online" if info["expandable"] else "copy"
    with OPS_LOCK:
        if wid in OPS:
            raise ValueError("Another backup or disk change of this workspace is running.")
        op_id = STORE.new_op(wid, "disk", user, from_gi=info["sizeGB"], to_gi=size, mode=mode,
                             message="Starting")
        OPS[wid] = op_id
    threading.Thread(target=run_expand, args=(token, ws, op_id, info, size, mode), daemon=True).start()
    LOG(event="workspace-disk-expand", workspace=ws["name"], owner=ws["owner_name"], size=size, mode=mode, by=user)
    return op_id


def run_expand(token, ws, op_id, info, size, mode):
    wid = ws["id"]
    try:
        if mode == "online":
            expand_online(token, ws, op_id, info, size)
        else:
            expand_by_copy(token, ws, op_id, info, size)
        STORE.update(op_id, status="done", finished_at=now(), message="The home disk is %d GB" % size)
    except Exception as e:  # noqa: BLE001
        STORE.update(op_id, status="failed", error=str(e)[:1500], finished_at=now())
        LOG(event="workspace-disk-expand-failed", workspace=ws["name"], error=str(e)[:300])
    finally:
        with OPS_LOCK:
            OPS.pop(wid, None)


def expand_online(token, ws, op_id, info, size):
    STORE.update(op_id, message="Growing the volume")
    status, out = KUBE.call("PATCH", "/api/v1/namespaces/%s/persistentvolumeclaims/%s" % (NAMESPACE, info["name"]),
                            {"spec": {"resources": {"requests": {"storage": "%dG" % size}}}},
                            "application/merge-patch+json")
    if status != 200:
        raise RuntimeError("Kubernetes refused to grow the volume: %s" % ((out or {}).get("message") or status))
    deadline = time.time() + 1800
    restarted = False
    while time.time() < deadline:
        time.sleep(5)
        cur = volume_info(ws["id"]) or {}
        if cur.get("sizeGB", 0) >= size and "FileSystemResizePending" not in cur.get("conditions", []):
            return
        if "FileSystemResizePending" in cur.get("conditions", []) and not restarted:
            # the file system grows when the volume is mounted again
            fresh = workspace(token, ws["id"])
            if is_running(fresh):
                STORE.update(op_id, message="Restarting the workspace so its file system grows")
                build(token, fresh, "stop")
                build(token, fresh, "start")
            restarted = True
    raise RuntimeError("The volume did not reach %d GB in 30 minutes (storage class %s)." % (size, info["storageClass"]))


def wait_gone(path, timeout=600):
    deadline = time.time() + timeout
    while time.time() < deadline:
        status, _ = ib.kube_call("GET", path)
        if status == 404:
            return
        time.sleep(3)
    raise RuntimeError("%s was not deleted in time." % path.rsplit("/", 1)[-1])


def wait_no_pods(wid, timeout=900):
    sel = urllib.parse.quote("com.coder.workspace.id=%s,app.kubernetes.io/name=coder-workspace" % wid)
    deadline = time.time() + timeout
    while time.time() < deadline:
        status, pods = ib.kube_call("GET", "/api/v1/namespaces/%s/pods?labelSelector=%s" % (NAMESPACE, sel))
        if status == 200 and not (pods or {}).get("items"):
            return
        time.sleep(3)
    raise RuntimeError("The workspace's pod did not go away.")


def new_pvc_body(old, name, size, temp=False):
    """A PVC like `old` (its labels and annotations, so Terraform and the dashboard know it), of `size` GB; a
    temporary one only says whose copy it is."""
    md = old["metadata"]
    keep = lambda d: {k: v for k, v in (d or {}).items() if not re.match(  # noqa: E731
        r"^(pv\.kubernetes\.io|volume\.kubernetes\.io|volume\.beta\.kubernetes\.io|kubectl\.kubernetes\.io)/", k)}
    spec = {"accessModes": old["spec"].get("accessModes") or ["ReadWriteOnce"],
            "resources": {"requests": {"storage": "%dG" % size}}}
    if old["spec"].get("storageClassName"):
        spec["storageClassName"] = old["spec"]["storageClassName"]
    if old["spec"].get("volumeMode"):
        spec["volumeMode"] = old["spec"]["volumeMode"]
    labels = ({"app.kubernetes.io/part-of": "coder", "coder.workspace-volume-copy-of": md["name"][:63]} if temp
              else keep(md.get("labels")))
    return {"apiVersion": "v1", "kind": "PersistentVolumeClaim",
            "metadata": {"name": name, "namespace": NAMESPACE, "labels": labels,
                         "annotations": {} if temp else keep(md.get("annotations"))}, "spec": spec}


def copy_job(op_id, step, src, dst):
    """`cp -a` of one volume onto another (as root, so every owner and mode is kept), then a check that they match."""
    script = ("set -e; cp -a /from/. /to/; chown \"$(stat -c %u:%g /from)\" /to; chmod \"$(stat -c %a /from)\" /to; "
              "a=$(cd /from && find . | wc -l); b=$(cd /to && find . | wc -l); "
              "echo \"files: $a -> $b\"; [ \"$a\" -le \"$b\" ]")
    return job_body("coder-home-%s-%d" % (step, op_id), op_id,
                    [tools_container("copy", ["sh", "-c", script],
                                     [{"name": "from", "mountPath": "/from", "readOnly": True},
                                      {"name": "to", "mountPath": "/to"}], root=True)],
                    [{"name": "from", "persistentVolumeClaim": {"claimName": src, "readOnly": True}},
                     {"name": "to", "persistentVolumeClaim": {"claimName": dst}}], root=True)


def expand_by_copy(token, ws, op_id, info, size):
    name = info["name"]
    temp = "%s-grow-%d" % (name, op_id)
    pvcs = "/api/v1/namespaces/%s/persistentvolumeclaims" % NAMESPACE
    fresh = workspace(token, ws["id"])
    was_running = is_running(fresh)
    if was_running:
        STORE.update(op_id, message="Stopping the workspace")
        build(token, fresh, "stop")
    wait_no_pods(ws["id"])
    old = get_pvc(name)
    STORE.update(op_id, message="Copying the home volume onto a %d GB one" % size)
    status, out = ib.kube_call("POST", pvcs, new_pvc_body(old, temp, size, temp=True))
    if status not in (200, 201):
        raise RuntimeError("Could not create the new volume: %s" % ((out or {}).get("message") or status))
    err = run_job(copy_job(op_id, "copy", name, temp))
    if err:
        ib.kube_call("DELETE", "%s/%s" % (pvcs, temp))
        raise RuntimeError("The copy failed, nothing was changed (the workspace stays stopped): " + err)
    # from here the data is safe on the new volume: it takes the old one's name
    STORE.update(op_id, message="Swapping the volumes")
    ib.kube_call("DELETE", "%s/%s" % (pvcs, name))
    wait_gone("%s/%s" % (pvcs, name))
    status, out = ib.kube_call("POST", pvcs, new_pvc_body(old, name, size))
    if status not in (200, 201):
        raise RuntimeError("Could not recreate %s: %s. The home is safe on %s." % (
            name, (out or {}).get("message") or status, temp))
    err = run_job(copy_job(op_id, "back", temp, name))
    if err:
        raise RuntimeError("The copy onto %s failed: %s. The home is safe on %s." % (name, err, temp))
    ib.kube_call("DELETE", "%s/%s" % (pvcs, temp))
    if was_running:
        STORE.update(op_id, message="Starting the workspace")
        build(token, workspace(token, ws["id"]), "start")


# --------------------------------------------------------------------------- HTTP
def handle_internal(h, method, path):
    m = re.match(r"^%s/internal/volumes/([A-Za-z0-9_-]{20,})/archive$" % BASE, path)
    if not m or not STORE:
        return False
    op = STORE.op(token=m.group(1))
    if not op or op["status"] != "running" or method != "PUT":
        h.send(404, "not found\n")
        return True
    STORE.update(op["id"], token=None)   # one upload per token
    receive_archive(h, op)
    return True


def handle(h, method, path, query):
    """True when it answered."""
    if not (path.startswith(BASE + "/api/workspace-icons") or path.startswith(BASE + "/api/workspace-volumes")):
        return False
    if not STORE:
        h.send_json(404, {"error": "Workspace volumes need the image builder's volume (imageBuilder.enabled)."})
        return True
    if method == "POST" and not h.csrf_ok():
        h.send_json(403, {"error": "Request rejected (cross-site protection)."})
        return True
    user, err = h.user()
    if err:
        return True
    token = h.token()
    try:
        return route(h, method, path, user, token)
    except LookupError as e:
        h.send_json(404, {"error": str(e)})
    except PermissionError:
        h.send_json(403, {"error": "Only the workspace's owner and admins can do this."})
    except ValueError as e:
        h.send_json(400, {"error": str(e)})
    return True


def read_json(h):
    n = int(h.headers.get("Content-Length") or 0)
    data = json.loads(h.rfile.read(min(n, 65536)) or b"{}") if n else {}
    if not isinstance(data, dict):
        raise ValueError("Expected a JSON object.")
    return data


def route(h, method, path, user, token):
    if path == BASE + "/api/workspace-icons" and method == "GET":
        return h.send_json(200, {"icons": STORE.icons()}) or True
    m = re.match(r"^%s/api/workspace-icons/([0-9a-f-]{36})$" % BASE, path)
    if m and method == "POST":
        ws = workspace(token, m.group(1))
        if not can_update(token, ws):
            raise PermissionError
        icon = str(read_json(h).get("icon") or "").strip()
        if icon and not ICON_RE.match(icon):
            raise ValueError("An icon is a path on Coder (/icon/..., /emojis/...) or an https:// address.")
        STORE.set_icon(ws["id"], icon, user["username"])
        LOG(event="workspace-icon", workspace=ws["name"], owner=ws["owner_name"], by=user["username"])
        return h.send_json(200, {"icon": icon}) or True
    m = re.match(r"^%s/api/workspace-volumes/([0-9a-f-]{36})(/.*)?$" % BASE, path)
    if not m:
        return h.send_json(404, {"error": "not found"}) or True
    ws = workspace(token, m.group(1))
    sub = m.group(2) or ""
    allowed = can_update(token, ws)
    if not allowed:
        raise PermissionError
    if sub == "" and method == "GET":
        s3 = ib.s3_settings(True) if ib.STORE else {}
        return h.send_json(200, {
            "volume": volume_info(ws["id"]), "running": is_running(ws),
            "busy": ws["id"] in OPS,
            "backups": [public(o) for o in STORE.ops(ws["id"], "backup")],
            "disk": [public(o) for o in STORE.ops(ws["id"], "disk", 1)],
            "s3": bool(s3.get("bucket") and s3.get("accessKey") and s3.get("secretKey")),
            "keepHours": KEEP_DOWNLOADS // 3600, "maxGB": MAX_DISK_GB}) or True
    if sub == "/backup" and method == "POST":
        op_id = start_backup(token, ws, str(read_json(h).get("target") or ""), user["username"])
        return h.send_json(200, {"backup": op_id}) or True
    if sub == "/expand" and method == "POST":
        size = read_json(h).get("size")
        op_id = start_expand(token, ws, int(size) if isinstance(size, (int, float)) else None, user["username"])
        return h.send_json(200, {"disk": op_id}) or True
    mb = re.match(r"^/backups/(\d+)/download$", sub)
    if mb and method == "GET":
        op = STORE.op(int(mb.group(1)))
        if not op or op["workspace_id"] != ws["id"] or not op.get("path") or not os.path.exists(op["path"]):
            raise LookupError("That backup is not here (any more).")
        size = os.path.getsize(op["path"])
        fname = "%s-%s-%s" % (ws["owner_name"], ws["name"], os.path.basename(op["path"]).split("-", 1)[1])
        h.send_response(200)
        h.send_header("Content-Type", "application/gzip")
        h.send_header("Content-Length", str(size))
        h.send_header("Content-Disposition", 'attachment; filename="%s"' % re.sub(r'[^A-Za-z0-9_.-]', "-", fname))
        h.send_header("Connection", "close")
        h.end_headers()
        h.close_connection = True
        with open(op["path"], "rb") as f:
            shutil.copyfileobj(f, h.wfile, 1024 * 1024)
        LOG(event="workspace-backup-download", workspace=ws["name"], owner=ws["owner_name"], by=user["username"])
        return True
    return h.send_json(404, {"error": "not found"}) or True


def start():
    global STORE
    if ib.ENABLED:
        STORE = Store(os.path.join(ib.FILES, "workspaces.sqlite"))
        threading.Thread(target=cleanup_loop, daemon=True).start()


# --------------------------------------------------------------------------- the Job's side
def archive(src, url):
    """A .tar.gz of SRC, streamed (chunked) to URL."""
    proc = subprocess.Popen(["tar", "-czf", "-", "-C", src, "."], stdout=subprocess.PIPE)
    u = urllib.parse.urlsplit(url)
    conn = (http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection)(
        u.hostname, u.port, timeout=600)

    def body():
        while True:
            d = proc.stdout.read(1024 * 1024)
            if not d:
                return
            yield d

    conn.request("PUT", u.path, body=body(), headers={"Content-Type": "application/gzip"}, encode_chunked=True)
    r = conn.getresponse()
    out = r.read().decode("utf-8", "replace")
    code = proc.wait()
    if code not in (0, 1):  # 1: a file changed while it was read (a live workspace); the archive is still good
        sys.exit("tar failed (%d)" % code)
    if r.status != 200:
        sys.exit("the service refused the archive: %s %s" % (r.status, out[:300]))
    print("sent:", out)


if __name__ == "__main__":
    if sys.argv[1:2] == ["archive"]:
        archive(sys.argv[2], sys.argv[3])
    else:
        sys.exit(__doc__)

"""The Cloud Drive (named the safe before; its API and objects keep that name): each account's own files on S3 (the
chart's userStorage, or an S3 service an admin set in its Settings), a prefix users/<user id>/ in one bucket.

Every workspace of the account mounts it (PersistentVolumeClaim coder-safe-<user id>, ReadWriteMany, through the CSI
driver ru.yandex.s3.csi), and the Workspaces page opens it. Its owner may disconnect a workspace from it (and connect it
again; a new workspace is connected): the workspace template mounts the volume out of the way (/mnt/cloud-drive) and a
watcher in the workspace links ~/cloud-drive to it while its pod's annotation platform.coder/cloud-drive says "on" -
which it reads through the downward API, so a change shows in the running workspace within a second, no restart. The
disconnected workspaces are kept in LINKS_FILE; a watcher here keeps every running workspace pod's annotation so (a
pod starts "on", from its template). This module keeps, every RECONCILE_EVERY seconds: the
bucket; the Secret the safes' volumes name for mounting (coder-user-storage-mount: the keys, and an endpoint the nodes
reach - GeeseFS runs on the node, outside the cluster's DNS, so the chart's MinIO by its ClusterIP); and for every
account a PersistentVolume (Retain: removing it never removes files) and its claim.

  GET  /__coder-ui/api/safe                   the caller's safe: used, quota, where it is mounted
  GET  /__coder-ui/api/safe/list?path=a/b     a folder of it: folders and files
  GET  /__coder-ui/api/safe/download?path=    a file, as an attachment
  PUT  /__coder-ui/api/safe/upload?path=      a file (the body; Content-Length), within the quota
  POST /__coder-ui/api/safe/folder {path}     a new folder
  POST /__coder-ui/api/safe/delete {path}     a file, or a folder (path ending in /) with everything in it
  GET  /__coder-ui/api/safe/settings[?check=1]   where its files are (the backend) and whether it is connected
  POST /__coder-ui/api/safe/settings {endpoint, region, bucket, accessKey, secretKey} | {reset: true}   admins
  POST /__coder-ui/api/safe/settings/test {...}  admins: try a backend without saving it
  POST /__coder-ui/api/safe/settings/quota {quotaGiB} | {reset: true}   admins: every account's capacity
  GET  /__coder-ui/api/safe/connections       the caller's workspaces disconnected from it, and those whose running
                                              pod predates the connections (their template version)
  POST /__coder-ui/api/safe/connections/<workspace id> {connected}   the caller's own workspace (dis)connected

The backend is the chart's (userStorage: its MinIO, or an S3 service of yours) until an admin sets another in the
dashboard (Cloud Drive > Settings): kept in SETTINGS_FILE on the service's volume, keys included. A new bucket takes
each account's volume over once none of its workspaces runs (files already stored stay where they were).

Only ever the caller's own prefix: paths are checked segment by segment (no "..", no control characters).

The shared libraries (the Images view), each a prefix of the same bucket mounted read-only in workspaces (one shared
volume each, ReadOnlyMany); everyone sees them, admins and template admins add and remove their files:
  - the ISO library: disk images for every workspace, library/isos/, at ~/isos (volume coder-isos);
  - the model library: GGUF language models for the AI workspaces (LM Studio, OpenCode), library/models/, at ~/models
    (volume coder-models).

  GET  /__coder-ui/api/<isos|models>                   the library, and the imports under way
  PUT  /__coder-ui/api/<isos|models>/upload?name=      a file (the body; Content-Length)
  POST /__coder-ui/api/<isos|models>/import {url, name}   a file the service downloads (http/https), in the background
  POST /__coder-ui/api/<isos|models>/delete {name}
"""
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request

import s3client

BASE = "/__coder-ui"
ENABLED = os.environ.get("SAFE_ENABLED", "false").lower() == "true"
PREFIX = "users/"
MOUNT_PATH = os.environ.get("SAFE_MOUNT_PATH", "/home/coder/cloud-drive")
QUOTA_BYTES = int(float(os.environ.get("SAFE_QUOTA_GIB", "10")) * 1024 ** 3)
MAX_UPLOAD_BYTES = int(float(os.environ.get("SAFE_MAX_UPLOAD_GIB", "5")) * 1024 ** 3)
NAMESPACE = os.environ.get("SAFE_NAMESPACE", "coder")
S3_ENDPOINT = os.environ.get("S3_ENDPOINT", "")
S3_REGION = os.environ.get("S3_REGION", "")
# The endpoint the nodes mount from: an S3 service's own, or the chart's MinIO by its Service's ClusterIP.
NODE_ENDPOINT = os.environ.get("SAFE_NODE_ENDPOINT", "")
NODE_SERVICE = os.environ.get("SAFE_NODE_SERVICE", "")
NODE_PORT = int(os.environ.get("SAFE_NODE_PORT", "9000"))
MOUNT_OPTIONS = os.environ.get("SAFE_MOUNT_OPTIONS", "--memory-limit 256 --dir-mode 0750 --file-mode 0640 --uid 1000 --gid 1000 --stat-cache-ttl 10s")
MOUNT_SECRET = "coder-user-storage-mount"
CSI_DRIVER = "ru.yandex.s3.csi"
CLAIM_PREFIX = "coder-safe-"
RECONCILE_EVERY = int(os.environ.get("SAFE_RECONCILE_SECONDS", "30"))
CSRF_HEADER = ("X-Requested-With", "coder-ui")
MAX_PATH = 900
USAGE_TTL = 30
# The shared libraries, by the API's name for them: their prefix, volume, mount path, largest file, file names.
LIBRARIES = {
    "isos": {"title": "the ISO library", "noun": "ISO", "prefix": "library/isos/", "claim": "coder-isos",
             "mountPath": os.environ.get("SAFE_ISO_MOUNT_PATH", "/home/coder/isos"),
             "maxBytes": int(float(os.environ.get("SAFE_ISO_MAX_GIB", "20")) * 1024 ** 3),
             "nameRe": re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,120}\.(iso|img)$", re.I),
             "nameRule": "ending in .iso or .img"},
    "models": {"title": "the model library", "noun": "model", "prefix": "library/models/", "claim": "coder-models",
               "mountPath": os.environ.get("SAFE_MODEL_MOUNT_PATH", "/home/coder/models"),
               "maxBytes": int(float(os.environ.get("SAFE_MODEL_MAX_GIB", "100")) * 1024 ** 3),
               "nameRe": re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,160}\.gguf$", re.I),
               "nameRule": "ending in .gguf"},
}

SETTINGS_FILE = os.environ.get("SAFE_SETTINGS_FILE", "/data/cloud-drive.json")
# Every account's capacity as an admin set it (Cloud Drive > Settings), over the chart's (QUOTA_BYTES).
QUOTA_FILE = os.environ.get("SAFE_QUOTA_FILE", "/data/cloud-drive-quota.json")
QUOTA_GIB_RANGE = (1, 10240)
# The workspaces their owners disconnected: {workspace id: {owner, by, at}}.
LINKS_FILE = os.environ.get("SAFE_LINKS_FILE", "/data/cloud-drive-links.json")
LINK_ANNOTATION = "platform.coder/cloud-drive"
LINK_WATCH_SECONDS = 5
WORKSPACE_ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

LOG = KUBE = USERS = CAN_EDIT = IS_ADMIN = WORKSPACE_INFO = None
# The backend's last check (every reconcile round, and on demand): connected or not, and how fast.
_status = {"connected": None, "error": None, "checkedAt": None, "latencyMs": None}
_imports = {}  # (library, file name) -> its download from a URL: {name, url, bytes, total, error, done, by, startedAt}
_usage = {}  # user id -> (time, bytes, files)
_volumes = {}  # user id -> time its volume was last seen in place
_lock = threading.Lock()
_links_lock = threading.Lock()


def setup(log, kube, users, can_edit=None, is_admin=None, workspace_info=None):
    """users(): [(id, username)] of every account (Coder's database); can_edit(user): may change the libraries;
    is_admin(user): may change the backend; workspace_info(token, id): the workspace (Coder's API), if the session may
    see it."""
    global LOG, KUBE, USERS, CAN_EDIT, IS_ADMIN, WORKSPACE_INFO
    LOG, KUBE, USERS, CAN_EDIT, IS_ADMIN, WORKSPACE_INFO = log, kube, users, can_edit, is_admin, workspace_info


# ---------------------------------------------------------------------------------------------------- the backend
def chart_backend():
    """The chart's storage: its MinIO (the nodes reach it by its Service's ClusterIP), or the S3 service in values."""
    return {"source": "chart", "kind": "minio" if NODE_SERVICE else "s3", "endpoint": S3_ENDPOINT,
            "region": S3_REGION, "bucket": os.environ.get("SAFE_BUCKET", "coder-user-storage"),
            "accessKey": os.environ.get("S3_ACCESS_KEY", ""), "secretKey": os.environ.get("S3_SECRET_KEY", "")}


def load_custom():
    try:
        with open(SETTINGS_FILE) as f:
            data = json.load(f)
        return data if isinstance(data, dict) and data.get("endpoint") else None
    except (OSError, ValueError):
        return None


def backend():
    """Where the files are: an admin's backend (Cloud Drive > Settings) over the chart's."""
    custom = load_custom()
    if not custom:
        return chart_backend()
    return {"source": "custom", "kind": "s3", "endpoint": custom["endpoint"], "region": custom.get("region", ""),
            "bucket": custom["bucket"], "accessKey": custom.get("accessKey", ""), "secretKey": custom.get("secretKey", "")}


def bucket():
    return backend()["bucket"]


def client(cfg=None):
    cfg = cfg or backend()
    return s3client.S3(cfg["endpoint"], cfg["region"], cfg["accessKey"], cfg["secretKey"], timeout=20)


BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")


def clean_backend(body, keep_secret=""):
    """A backend an admin gave: checked, the secret kept from before when left empty."""
    cfg = {"endpoint": str(body.get("endpoint") or "").strip().rstrip("/"), "region": str(body.get("region") or "").strip(),
           "bucket": str(body.get("bucket") or "").strip(), "accessKey": str(body.get("accessKey") or "").strip(),
           "secretKey": str(body.get("secretKey") or "") or keep_secret}
    if not re.match(r"^https?://[^\s/]+(/[^\s]*)?$", cfg["endpoint"]):
        raise ValueError("The endpoint is an http:// or https:// address, such as https://s3.us-east-1.amazonaws.com.")
    if not BUCKET_RE.match(cfg["bucket"]):
        raise ValueError("A bucket name of 3-63 lowercase letters, digits, dots and dashes.")
    if len(cfg["region"]) > 64 or not re.match(r"^[a-z0-9-]*$", cfg["region"]):
        raise ValueError("A region such as us-east-1 (or empty).")
    if not cfg["accessKey"] or not cfg["secretKey"]:
        raise ValueError("The access key and its secret.")
    return cfg


def check(cfg=None):
    """Connects to the backend: {connected, error, latencyMs, bucketExists, checkedAt}."""
    t0 = time.time()
    out = {"checkedAt": int(t0)}
    try:
        exists = client(cfg).bucket_exists((cfg or backend())["bucket"])
        out.update(connected=True, error=None, bucketExists=exists)
    except s3client.S3Error as e:
        out.update(connected=False, error="%s: %s" % (e.code or e.status, e.message or "refused"), bucketExists=None)
    except (OSError, ValueError) as e:
        out.update(connected=False, error=str(e)[:200], bucketExists=None)
    out["latencyMs"] = int((time.time() - t0) * 1000)
    return out


def settings_view(user, fresh=False):
    cfg = backend()
    if fresh or not _status.get("checkedAt"):
        _status.update(check())
    chart = chart_backend()
    return {"canEdit": bool(IS_ADMIN and IS_ADMIN(user)), "source": cfg["source"], "kind": cfg["kind"],
            "endpoint": cfg["endpoint"], "region": cfg["region"], "bucket": cfg["bucket"],
            "accessKey": cfg["accessKey"] if IS_ADMIN and IS_ADMIN(user) else "", "hasSecret": bool(cfg["secretKey"]),
            "chart": {"kind": chart["kind"], "endpoint": chart["endpoint"], "bucket": chart["bucket"]},
            "mountPath": MOUNT_PATH, "status": dict(_status), "quota": quota_view()}


def save_settings(user, body):
    if not (IS_ADMIN and IS_ADMIN(user)):
        raise PermissionError
    if body.get("reset"):
        try:
            os.remove(SETTINGS_FILE)
        except FileNotFoundError:
            pass
        LOG(event="cloud-drive-backend", action="chart", by=user["username"])
    else:
        cur = load_custom() or {}
        cfg = clean_backend(body, keep_secret=cur.get("secretKey", "") if cur.get("endpoint") else "")
        result = check(cfg)
        if not result["connected"]:
            raise ValueError("Not saved: it could not be reached (%s)." % result["error"])
        if not result["bucketExists"]:
            client(cfg).create_bucket(cfg["bucket"])
        tmp = SETTINGS_FILE + ".tmp"
        with open(tmp, "w") as f:
            os.fchmod(f.fileno(), 0o600)
            json.dump(cfg, f)
        os.replace(tmp, SETTINGS_FILE)
        LOG(event="cloud-drive-backend", action="custom", endpoint=cfg["endpoint"], bucket=cfg["bucket"],
            by=user["username"])
    # every volume and the mount Secret looked at again now
    _volumes.clear()
    _status.update(check())
    _wake.set()
    return settings_view(user)


def custom_quota_gib():
    """The capacity an admin set (GiB), or None: the chart's."""
    try:
        with open(QUOTA_FILE) as f:
            gib = json.load(f).get("quotaGiB")
        return gib if isinstance(gib, int) and QUOTA_GIB_RANGE[0] <= gib <= QUOTA_GIB_RANGE[1] else None
    except (OSError, ValueError, AttributeError):
        return None


def quota_bytes():
    """Every account's capacity: an admin's, else the chart's."""
    gib = custom_quota_gib()
    return gib * 1024 ** 3 if gib else QUOTA_BYTES


def save_quota(user, body):
    """An admin sets every account's capacity (GiB), or {reset: true} for the chart's."""
    if not (IS_ADMIN and IS_ADMIN(user)):
        raise PermissionError
    if body.get("reset"):
        try:
            os.remove(QUOTA_FILE)
        except FileNotFoundError:
            pass
        LOG(event="cloud-drive-quota", action="chart", gib=QUOTA_BYTES // 1024 ** 3, by=user["username"])
        return quota_view()
    gib = body.get("quotaGiB")
    if isinstance(gib, float) and gib.is_integer():
        gib = int(gib)
    if not isinstance(gib, int) or isinstance(gib, bool) or not QUOTA_GIB_RANGE[0] <= gib <= QUOTA_GIB_RANGE[1]:
        raise ValueError("A capacity of %d to %d GiB, a whole number." % QUOTA_GIB_RANGE)
    tmp = QUOTA_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"quotaGiB": gib, "by": user["username"], "at": int(time.time())}, f)
    os.replace(tmp, QUOTA_FILE)
    LOG(event="cloud-drive-quota", action="set", gib=gib, by=user["username"])
    return quota_view()


def quota_view():
    return {"gib": quota_bytes() // 1024 ** 3, "chartGib": QUOTA_BYTES // 1024 ** 3,
            "custom": custom_quota_gib() is not None, "min": QUOTA_GIB_RANGE[0], "max": QUOTA_GIB_RANGE[1]}


def capacity():
    return "%dGi" % max(1, quota_bytes() // 1024 ** 3)


# ---------------------------------------------------------------------------------------------------- connections
def disconnected():
    """{workspace id: {owner, by, at}}: the workspaces their owners disconnected from their Cloud Drive."""
    try:
        with open(LINKS_FILE) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def link_pods(wid=None):
    """Running pods (of one workspace, or all) whose template links the Cloud Drive (they carry the annotation)."""
    label = "com.coder.workspace.id" + ("=" + wid if wid else "")
    status, pods = KUBE.call("GET", "/api/v1/namespaces/%s/pods?labelSelector=%s" % (
        NAMESPACE, urllib.parse.quote(label)))
    if status != 200 or not isinstance(pods, dict):
        return [], []
    linked, older = [], []
    for p in pods.get("items") or []:
        meta = p.get("metadata") or {}
        if (p.get("status") or {}).get("phase") != "Running" or meta.get("deletionTimestamp"):
            continue
        (linked if LINK_ANNOTATION in (meta.get("annotations") or {}) else older).append(p)
    return linked, older


def link(pod, want):
    """Sets the pod's annotation ("on" / "off") where it says otherwise; True when it changed."""
    meta = pod["metadata"]
    if (meta.get("annotations") or {}).get(LINK_ANNOTATION) == want:
        return False
    status, _ = KUBE.call("PATCH", "/api/v1/namespaces/%s/pods/%s" % (NAMESPACE, meta["name"]),
                          {"metadata": {"annotations": {LINK_ANNOTATION: want}}},
                          patch_type="application/merge-patch+json")
    if status != 200:
        raise RuntimeError("pod %s: HTTP %s" % (meta["name"], status))
    return True


def links_once():
    """Every running workspace pod's annotation as LINKS_FILE says (a restarted workspace starts "on")."""
    off = disconnected()
    linked, _ = link_pods()
    for pod in linked:
        wid = ((pod.get("metadata") or {}).get("labels") or {}).get("com.coder.workspace.id")
        if wid and link(pod, "off" if wid in off else "on"):
            LOG(event="cloud-drive-link", workspace=wid, pod=pod["metadata"]["name"],
                connected=wid not in off, by="watcher")


def _links_loop():
    last = None
    while True:
        try:
            links_once()
            last = None
        except Exception as e:  # noqa: BLE001 - reported once, retried next round
            if str(e) != last:
                LOG(event="cloud-drive-links-failed", error=str(e)[:300])
            last = str(e)
        time.sleep(LINK_WATCH_SECONDS)


def connections(user):
    """The caller's workspaces disconnected from their Cloud Drive; and those running in a pod that cannot be (from a
    template version before the connections: it takes effect when they are updated)."""
    off = sorted(wid for wid, entry in disconnected().items() if entry.get("owner") == user["id"])
    older = []
    if KUBE and KUBE.ok:
        _, pods = link_pods()
        labels = [(p.get("metadata") or {}).get("labels") or {} for p in pods]
        older = sorted({lb.get("com.coder.workspace.id") for lb in labels
                        if lb.get("com.coder.user.id") == user["id"]} - {None})
    return {"disconnected": off, "older": older}


def set_connected(h, user, wid, body):
    """The caller's own workspace connected to their Cloud Drive, or disconnected from it: at once in its running pod."""
    if not WORKSPACE_ID_RE.match(wid or ""):
        raise ValueError("Not a workspace id.")
    want = body.get("connected")
    if not isinstance(want, bool):
        raise ValueError("connected: true or false.")
    ws = WORKSPACE_INFO(h.token(), wid) if WORKSPACE_INFO else None
    if not ws:
        raise ValueError("There is no such workspace.")
    if ws.get("owner_id") != user["id"]:
        raise PermissionError
    with _links_lock:
        data = disconnected()
        if want:
            data.pop(wid, None)
        else:
            data[wid] = {"owner": user["id"], "by": user["username"], "at": int(time.time())}
        tmp = LINKS_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f)
        os.replace(tmp, LINKS_FILE)
    applied = False
    if KUBE and KUBE.ok:
        linked, _ = link_pods(wid)
        for pod in linked:
            link(pod, "on" if want else "off")
            applied = True
    LOG(event="cloud-drive-link", workspace=wid, name=ws.get("name"), connected=want, by=user["username"],
        running=applied)
    return {**connections(user), "workspace": wid, "connected": want, "applied": applied}


def root_of(user_id):
    if not re.match(r"^[0-9a-f-]{36}$", user_id or ""):
        raise PermissionError("not an account id")
    return PREFIX + user_id + "/"


def clean_path(path):
    """A path inside a safe, a/b/c (no leading or trailing slash; "" the top); ValueError for anything that could
    step outside it or that S3 or a file system would choke on."""
    path = (path or "").strip("/")
    if not path:
        return ""
    if len(path.encode()) > MAX_PATH:
        raise ValueError("That path is too long.")
    for part in path.split("/"):
        if part in ("", ".", "..") or len(part.encode()) > 255 or any(ord(c) < 32 or ord(c) == 127 for c in part):
            raise ValueError("That is not a valid name: %r" % part[:60])
    return path


# ---------------------------------------------------------------------------------------------------- reconciling
def node_endpoint():
    cfg = backend()
    if cfg["source"] == "custom":
        return cfg["endpoint"]
    if NODE_ENDPOINT:
        return NODE_ENDPOINT
    if not NODE_SERVICE:
        return S3_ENDPOINT
    status, svc = KUBE.call("GET", "/api/v1/namespaces/%s/services/%s" % (NAMESPACE, NODE_SERVICE))
    ip = (svc.get("spec") or {}).get("clusterIP") if status == 200 and isinstance(svc, dict) else None
    if not ip or ip == "None":
        raise RuntimeError("Service %s has no ClusterIP yet" % NODE_SERVICE)
    return "http://%s:%d" % ("[%s]" % ip if ":" in ip else ip, NODE_PORT)


def ensure_mount_secret():
    """The Secret the safes' volumes name for mounting: the keys and the nodes' endpoint (kept up to date)."""
    import base64
    cfg = backend()
    want = {"accessKeyID": cfg["accessKey"], "secretAccessKey": cfg["secretKey"], "endpoint": node_endpoint()}
    if cfg["region"]:
        want["region"] = cfg["region"]
    path = "/api/v1/namespaces/%s/secrets/%s" % (NAMESPACE, MOUNT_SECRET)
    status, cur = KUBE.call("GET", path)
    data = {k: base64.b64encode(v.encode()).decode() for k, v in want.items()}
    body = {"apiVersion": "v1", "kind": "Secret", "type": "Opaque",
            "metadata": {"name": MOUNT_SECRET, "namespace": NAMESPACE,
                         "labels": {"app.kubernetes.io/name": "coder-user-storage", "app.kubernetes.io/part-of": "coder"}},
            "data": data}
    if status == 404:
        status, out = KUBE.call("POST", "/api/v1/namespaces/%s/secrets" % NAMESPACE, body)
        if status not in (200, 201):
            raise RuntimeError("mount Secret: HTTP %s %s" % (status, str(out)[:200]))
        LOG(event="safe-mount-secret", action="created", endpoint=want["endpoint"])
    elif status == 200 and (cur.get("data") or {}) != data:
        body["metadata"]["resourceVersion"] = cur["metadata"]["resourceVersion"]
        status, out = KUBE.call("PUT", path, body)
        if status != 200:
            raise RuntimeError("mount Secret: HTTP %s %s" % (status, str(out)[:200]))
        LOG(event="safe-mount-secret", action="updated", endpoint=want["endpoint"])
    elif status != 200:
        raise RuntimeError("mount Secret: HTTP %s" % status)


def volume_objects(user_id, username=""):
    """The account's PersistentVolume and claim."""
    name = CLAIM_PREFIX + user_id
    labels = {"app.kubernetes.io/name": "coder-safe", "app.kubernetes.io/part-of": "coder", "platform.coder/user-id": user_id}
    ref = {"name": MOUNT_SECRET, "namespace": NAMESPACE}
    pv = {"apiVersion": "v1", "kind": "PersistentVolume",
          "metadata": {"name": name, "labels": labels, "annotations": {"platform.coder/username": username}},
          "spec": {"capacity": {"storage": capacity()}, "accessModes": ["ReadWriteMany"],
                   # Retain: deleting the volume (or the account) never deletes the files.
                   "persistentVolumeReclaimPolicy": "Retain", "storageClassName": "",
                   "claimRef": {"namespace": NAMESPACE, "name": name},
                   "csi": {"driver": CSI_DRIVER, "volumeHandle": "%s/%s" % (bucket(), root_of(user_id).rstrip("/")),
                           "nodeStageSecretRef": ref, "nodePublishSecretRef": ref,
                           "volumeAttributes": {"capacity": capacity(), "mounter": "geesefs", "options": MOUNT_OPTIONS}}}}
    pvc = {"apiVersion": "v1", "kind": "PersistentVolumeClaim",
           "metadata": {"name": name, "namespace": NAMESPACE, "labels": labels,
                        "annotations": {"platform.coder/username": username}},
           "spec": {"accessModes": ["ReadWriteMany"], "storageClassName": "", "volumeName": name,
                    "resources": {"requests": {"storage": capacity()}}}}
    return pv, pvc


def library_volume_objects(kind):
    """A library's volume and claim: its prefix, read-only, for every workspace."""
    lib = LIBRARIES[kind]
    labels = {"app.kubernetes.io/name": lib["claim"], "app.kubernetes.io/part-of": "coder"}
    ref = {"name": MOUNT_SECRET, "namespace": NAMESPACE}
    pv = {"apiVersion": "v1", "kind": "PersistentVolume", "metadata": {"name": lib["claim"], "labels": labels},
          "spec": {"capacity": {"storage": "%dGi" % max(1, lib["maxBytes"] // 1024 ** 3)},
                   "accessModes": ["ReadOnlyMany"], "persistentVolumeReclaimPolicy": "Retain", "storageClassName": "",
                   "claimRef": {"namespace": NAMESPACE, "name": lib["claim"]},
                   "csi": {"driver": CSI_DRIVER, "volumeHandle": "%s/%s" % (bucket(), lib["prefix"].rstrip("/")),
                           "readOnly": True, "nodeStageSecretRef": ref, "nodePublishSecretRef": ref,
                           "volumeAttributes": {"capacity": "1Gi", "mounter": "geesefs", "options": MOUNT_OPTIONS}}}}
    pvc = {"apiVersion": "v1", "kind": "PersistentVolumeClaim",
           "metadata": {"name": lib["claim"], "namespace": NAMESPACE, "labels": labels},
           "spec": {"accessModes": ["ReadOnlyMany"], "storageClassName": "", "volumeName": lib["claim"],
                    "resources": {"requests": {"storage": pv["spec"]["capacity"]["storage"]}}}}
    return pv, pvc


def claims_in_use():
    """The claims pods use now (a volume in use is not replaced)."""
    status, pods = KUBE.call("GET", "/api/v1/namespaces/%s/pods" % NAMESPACE)
    if status != 200 or not isinstance(pods, dict):
        raise RuntimeError("pods: HTTP %s" % status)
    return {(v.get("persistentVolumeClaim") or {}).get("claimName") for p in pods.get("items") or []
            if (p.get("status") or {}).get("phase") not in ("Succeeded", "Failed")
            for v in (p.get("spec") or {}).get("volumes") or []} - {None}


def ensure_volume(user_id, username="", objects=None, in_use=None):
    """Makes the account's volume and claim (or the given pair) where missing, and replaces them when they name another
    bucket (the backend changed) and nothing uses them; True when both are in place."""
    pv, pvc = objects or volume_objects(user_id, username)
    name = pv["metadata"]["name"]
    status, cur = KUBE.call("GET", "/api/v1/persistentvolumes/" + name)
    handle = ((cur or {}).get("spec") or {}).get("csi", {}).get("volumeHandle") if status == 200 else None
    if handle and handle != pv["spec"]["csi"]["volumeHandle"]:
        if name in (in_use if in_use is not None else claims_in_use()):
            return False  # its workspace runs: replaced once it stops
        for path in ("/api/v1/namespaces/%s/persistentvolumeclaims/%s" % (NAMESPACE, name),
                     "/api/v1/persistentvolumes/" + name):
            KUBE.call("DELETE", path)
        for _ in range(30):
            if KUBE.call("GET", "/api/v1/persistentvolumes/" + name)[0] == 404:
                break
            time.sleep(1)
        LOG(event="safe-volume", user=username or user_id, replaced=handle, now=pv["spec"]["csi"]["volumeHandle"])
    made = []
    for get_path, post_path, obj in (
            ("/api/v1/persistentvolumes/" + name, "/api/v1/persistentvolumes", pv),
            ("/api/v1/namespaces/%s/persistentvolumeclaims/%s" % (NAMESPACE, name),
             "/api/v1/namespaces/%s/persistentvolumeclaims" % NAMESPACE, pvc)):
        status, _ = KUBE.call("GET", get_path)
        if status == 404:
            status, out = KUBE.call("POST", post_path, obj)
            if status not in (200, 201, 409):
                raise RuntimeError("%s %s: HTTP %s %s" % (obj["kind"], name, status, str(out)[:200]))
            made.append(obj["kind"])
        elif status != 200:
            raise RuntimeError("%s %s: HTTP %s" % (obj["kind"], name, status))
    if made:
        LOG(event="safe-volume", user=username or user_id, made=made)
    return True


def reconcile():
    _status.update(check())
    if not _status["connected"]:
        raise RuntimeError("the backend is not reachable: %s" % _status["error"])
    s3 = client()
    if not _status.get("bucketExists"):
        s3.create_bucket(bucket())
        LOG(event="safe-bucket", action="created", bucket=bucket())
    ensure_mount_secret()
    now = time.time()
    in_use = claims_in_use()
    for kind, lib in LIBRARIES.items():
        if now - _volumes.get(lib["claim"], 0) >= 600:
            if ensure_volume("", lib["title"], objects=library_volume_objects(kind), in_use=in_use):
                _volumes[lib["claim"]] = now
    for user_id, username in USERS() or []:
        # Seen in place in the last 10 minutes: not asked again every round.
        if now - _volumes.get(user_id, 0) < 600:
            continue
        try:
            if ensure_volume(user_id, username, in_use=in_use):
                _volumes[user_id] = now
        except Exception as e:  # noqa: BLE001 - this account's turn comes again next round
            LOG(event="safe-volume-failed", user=username, error=str(e)[:300])


def _loop():
    last = None
    while True:
        try:
            reconcile()
            last = None
        except Exception as e:  # noqa: BLE001 - reported once, retried next round
            if str(e) != last:
                LOG(event="safe-reconcile-failed", error=str(e)[:300])
            last = str(e)
        _wake.wait(RECONCILE_EVERY)
        _wake.clear()


_wake = threading.Event()  # a new backend: the next round now


def start():
    if ENABLED and KUBE and KUBE.ok:
        threading.Thread(target=_loop, name="safe", daemon=True).start()
        threading.Thread(target=_links_loop, name="safe-links", daemon=True).start()


# ---------------------------------------------------------------------------------------------------- the files
def usage(user_id, fresh=False):
    """(bytes, files) in the account's safe, cached USAGE_TTL seconds."""
    with _lock:
        hit = _usage.get(user_id)
    if hit and not fresh and time.time() - hit[0] < USAGE_TTL:
        return hit[1], hit[2]
    objects, _ = client().list(bucket(), prefix=root_of(user_id))
    files = [o for o in objects if not o["key"].endswith("/")]
    used = sum(o["size"] for o in objects)
    with _lock:
        _usage[user_id] = (time.time(), used, len(files))
    return used, len(files)


def summary(user):
    used, files = usage(user["id"])
    status, pvc = KUBE.call("GET", "/api/v1/namespaces/%s/persistentvolumeclaims/%s" % (NAMESPACE, CLAIM_PREFIX + user["id"])) \
        if KUBE and KUBE.ok else (0, None)
    bound = status == 200 and ((pvc or {}).get("status") or {}).get("phase") == "Bound"
    return {"enabled": True, "mountPath": MOUNT_PATH, "usedBytes": used, "files": files, "quotaBytes": quota_bytes(),
            "maxUploadBytes": MAX_UPLOAD_BYTES, "volume": "ready" if bound else "pending"}


def listing(user, path):
    """A folder of the caller's safe: its folders and files, folders first, by name."""
    path = clean_path(path)
    prefix = root_of(user["id"]) + (path + "/" if path else "")
    objects, prefixes = client().list(bucket(), prefix=prefix, delimiter="/")
    folders = sorted({p[len(prefix):].rstrip("/") for p in prefixes if p != prefix}, key=str.lower)
    files = sorted(({"name": o["key"][len(prefix):], "size": o["size"], "modified": o["modified"]}
                    for o in objects if o["key"] != prefix and not o["key"].endswith("/")), key=lambda f: f["name"].lower())
    return {"path": path, "folders": [{"name": f} for f in folders], "files": files}


def upload_csrf_ok(h):
    """Writes come from the dashboard itself: its header, and the browser's same-origin markers."""
    if h.headers.get(CSRF_HEADER[0]) != CSRF_HEADER[1]:
        return False
    origin = h.headers.get("Origin")
    if origin and urllib.parse.urlsplit(origin).netloc != h.headers.get("Host"):
        return False
    return h.headers.get("Sec-Fetch-Site", "same-origin") in ("same-origin", "none")


def read_json(h, limit=8192):
    n = int(h.headers.get("Content-Length") or 0)
    if not 0 < n <= limit:
        raise ValueError("Expected a JSON object.")
    data = json.loads(h.rfile.read(n))
    if not isinstance(data, dict):
        raise ValueError("Expected a JSON object.")
    return data


def download(h, user, path):
    path = clean_path(path)
    if not path:
        raise ValueError("Choose a file.")
    resp = client().get(bucket(), root_of(user["id"]) + path)
    try:
        name = path.rsplit("/", 1)[-1]
        ascii_name = re.sub(r'[^A-Za-z0-9._ -]', "_", name) or "download"
        h.send_response(200)
        h.send_header("Content-Type", "application/octet-stream")
        h.send_header("Content-Length", resp.headers.get("Content-Length", "0"))
        h.send_header("Content-Disposition", "attachment; filename=\"%s\"; filename*=UTF-8''%s" % (
            ascii_name, urllib.parse.quote(name, safe="")))
        h.send_header("Cache-Control", "no-store")
        h.send_header("X-Content-Type-Options", "nosniff")
        h.end_headers()
        while True:
            chunk = resp.read(1024 * 1024)
            if not chunk:
                break
            h.wfile.write(chunk)
    finally:
        resp.close()
        resp.conn.close()


def upload(h, user, path):
    path = clean_path(path)
    if not path:
        raise ValueError("Name the file.")
    length = int(h.headers.get("Content-Length") or -1)
    if length < 0:
        raise ValueError("The upload needs a Content-Length.")
    if length > MAX_UPLOAD_BYTES:
        return h.send_json(413, {"error": "That file is larger than the %d GiB an upload may be." % (MAX_UPLOAD_BYTES // 1024 ** 3)})
    key = root_of(user["id"]) + path
    s3 = client()
    used, _ = usage(user["id"], fresh=True)
    existing = 0
    try:
        existing = int(s3.head(bucket(), key).headers.get("Content-Length") or 0)
    except s3client.S3Error as e:
        if e.status != 404:
            raise
    quota = quota_bytes()
    if used - existing + length > quota:
        return h.send_json(413, {"error": "Your Cloud Drive holds %s; this file would take it past its %s." % (
            human(used), human(quota))})
    s3.put_large(bucket(), key, h.rfile, length)
    with _lock:
        _usage.pop(user["id"], None)
    LOG(event="safe-upload", user=user["username"], path=path, bytes=length)
    return h.send_json(200, {"ok": True, "path": path, "size": length})


def make_folder(user, path):
    path = clean_path(path)
    if not path:
        raise ValueError("Name the folder.")
    client().put(bucket(), root_of(user["id"]) + path + "/", b"")
    return {"ok": True, "path": path}


def delete(user, path):
    """A file, or a folder (path ending in "/") and everything under it."""
    folder = (path or "").endswith("/")
    path = clean_path(path)
    if not path:
        raise ValueError("Choose what to delete.")
    s3 = client()
    key = root_of(user["id"]) + path
    if folder:
        objects, _ = s3.list(bucket(), prefix=key + "/")
        keys = [o["key"] for o in objects]
        failed = s3.delete_many(bucket(), keys) if keys else []
        if failed:
            raise RuntimeError("%d could not be deleted (%s)" % (len(failed), failed[0][1]))
        removed = len([k for k in keys if not k.endswith("/")])
    else:
        s3.delete(bucket(), key)
        removed = 1
    with _lock:
        _usage.pop(user["id"], None)
    LOG(event="safe-delete", user=user["username"], path=path + ("/" if folder else ""), files=removed)
    return {"ok": True, "removed": removed}


def human(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return ("%d %s" if unit == "B" else "%.1f %s") % (n, unit)
        n /= 1024.0


# ------------------------------------------------------------------------------------------------- the libraries
def library_name(kind, name):
    lib = LIBRARIES[kind]
    name = str(name or "").strip()
    if not lib["nameRe"].match(name):
        raise ValueError("A file name of letters, digits and . _ + -, %s." % lib["nameRule"])
    return name


def library(kind, user):
    lib = LIBRARIES[kind]
    prefix = lib["prefix"]
    objects, _ = client().list(bucket(), prefix=prefix)
    files = sorted(({"name": o["key"][len(prefix):], "size": o["size"], "modified": o["modified"]}
                    for o in objects if "/" not in o["key"][len(prefix):] and o["key"] != prefix),
                   key=lambda f: f["name"].lower())
    with _lock:
        imports = [dict(i) for (k, _n), i in _imports.items() if k == kind]
    return {"enabled": True, "canEdit": bool(CAN_EDIT and CAN_EDIT(user)), "mountPath": lib["mountPath"],
            "maxBytes": lib["maxBytes"], "files": files, "usedBytes": sum(f["size"] for f in files),
            "imports": sorted(imports, key=lambda i: i["startedAt"], reverse=True)}


def library_upload(kind, h, user, name):
    lib = LIBRARIES[kind]
    name = library_name(kind, name)
    length = int(h.headers.get("Content-Length") or -1)
    if length < 0:
        raise ValueError("The upload needs a Content-Length.")
    if length > lib["maxBytes"]:
        return h.send_json(413, {"error": "A%s %s can be at most %s." % (
            "n" if lib["noun"][0] in "AEIOU" else "", lib["noun"], human(lib["maxBytes"]))})
    client().put_large(bucket(), lib["prefix"] + name, h.rfile, length)
    LOG(event="library-upload", library=kind, user=user["username"], name=name, bytes=length)
    return h.send_json(200, {"ok": True, "name": name, "size": length})


def _download(kind, state, url):
    """A file from a URL into a library, in parts as it comes; its progress in state."""
    lib = LIBRARIES[kind]
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "coder-platform"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            total = int(resp.headers.get("Content-Length") or -1)
            if total < 0:
                raise ValueError("the server does not say how large the file is (no Content-Length)")
            if total > lib["maxBytes"]:
                raise ValueError("it is %s, more than the %s a%s %s may be" % (
                    human(total), human(lib["maxBytes"]), "n" if lib["noun"][0] in "AEIOU" else "", lib["noun"]))
            state["total"] = total

            def progress(n):
                state["bytes"] = n
            client().put_large(bucket(), lib["prefix"] + state["name"], resp, total, progress=progress)
        state["done"] = True
        LOG(event="library-import", library=kind, user=state["by"], name=state["name"], url=url,
            bytes=state.get("total"))
    except Exception as e:  # noqa: BLE001 - shown in the library until dismissed by the next import of the name
        state["error"] = str(e)[:300]
        LOG(event="library-import-failed", library=kind, user=state["by"], name=state["name"], error=str(e)[:300])


def library_import(kind, user, body):
    lib = LIBRARIES[kind]
    url = str(body.get("url") or "").strip()
    if not re.match(r"^https?://[^\s]+$", url):
        raise ValueError("An http:// or https:// address of the %s." % lib["noun"])
    name = library_name(kind, body.get("name") or
                        urllib.parse.unquote(urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1]))
    with _lock:
        cur = _imports.get((kind, name))
        if cur and not cur.get("done") and not cur.get("error"):
            raise ValueError("%s is being imported already." % name)
        state = {"name": name, "url": url, "bytes": 0, "total": None, "error": None, "done": False,
                 "by": user["username"], "startedAt": time.time()}
        _imports[(kind, name)] = state
    threading.Thread(target=_download, args=(kind, state, url), name="library-import", daemon=True).start()
    return {"ok": True, "name": name}


def handle_libraries(h, method, path, query):
    """The libraries' API (/api/isos, /api/models). True when it answered."""
    kind = next((k for k in LIBRARIES if path == BASE + "/api/" + k or path.startswith(BASE + "/api/" + k + "/")),
                None)
    if kind is None:
        return False
    lib = LIBRARIES[kind]
    root = BASE + "/api/" + kind
    if not ENABLED:
        h.send_json(404, {"error": "%s needs the Cloud Drive's storage (global.userStorage)." %
                                   lib["title"][0].upper() + lib["title"][1:]})
        return True
    if method != "GET" and not upload_csrf_ok(h):
        h.send_json(403, {"error": "Request rejected (cross-site protection)."})
        return True
    user, err = h.user()
    if err:
        return True
    q = dict(urllib.parse.parse_qsl(query or "", keep_blank_values=True))
    try:
        if method == "GET" and path == root:
            return h.send_json(200, library(kind, user)) or True
        if not (CAN_EDIT and CAN_EDIT(user)):
            raise PermissionError
        if method == "PUT" and path == root + "/upload":
            library_upload(kind, h, user, q.get("name", ""))
        elif method == "POST" and path == root + "/import":
            h.send_json(200, library_import(kind, user, read_json(h)))
        elif method == "POST" and path == root + "/delete":
            name = library_name(kind, read_json(h).get("name"))
            client().delete(bucket(), lib["prefix"] + name)
            with _lock:
                _imports.pop((kind, name), None)
            LOG(event="library-delete", library=kind, user=user["username"], name=name)
            h.send_json(200, {"ok": True})
        else:
            h.send_json(404, {"error": "not found"})
    except ValueError as e:
        h.send_json(400, {"error": str(e)})
    except PermissionError:
        h.send_json(403, {"error": "Only admins and template admins change %s." % lib["title"]})
    except s3client.S3Error as e:
        LOG(event="library-s3-failed", library=kind, user=user["username"], error=str(e)[:300])
        h.send_json(502, {"error": "The library's storage answered: %s" % (e.message or e.code or e.status)})
    except (OSError, RuntimeError) as e:
        h.send_json(503, {"error": "The library's storage could not be reached: %s" % str(e)[:200]})
    return True


def handle(h, method, path, query):
    """True when it answered."""
    if not path.startswith(BASE + "/api/safe"):
        return False
    if not ENABLED:
        h.send_json(404, {"error": "The Cloud Drive is not enabled on this deployment."})
        return True
    if method != "GET" and not upload_csrf_ok(h):
        h.send_json(403, {"error": "Request rejected (cross-site protection)."})
        return True
    user, err = h.user()
    if err:
        return True
    q = dict(urllib.parse.parse_qsl(query or "", keep_blank_values=True))
    try:
        if method == "GET" and path == BASE + "/api/safe":
            h.send_json(200, summary(user))
        elif method == "GET" and path == BASE + "/api/safe/list":
            h.send_json(200, listing(user, q.get("path", "")))
        elif method == "GET" and path == BASE + "/api/safe/download":
            download(h, user, q.get("path", ""))
        elif method == "PUT" and path == BASE + "/api/safe/upload":
            upload(h, user, q.get("path", ""))
        elif method == "POST" and path == BASE + "/api/safe/folder":
            h.send_json(200, make_folder(user, read_json(h).get("path", "")))
        elif method == "POST" and path == BASE + "/api/safe/delete":
            h.send_json(200, delete(user, str(read_json(h).get("path", ""))))
        elif method == "GET" and path == BASE + "/api/safe/settings":
            h.send_json(200, settings_view(user, fresh=q.get("check") == "1"))
        elif method == "POST" and path == BASE + "/api/safe/settings":
            h.send_json(200, save_settings(user, read_json(h)))
        elif method == "POST" and path == BASE + "/api/safe/settings/quota":
            h.send_json(200, save_quota(user, read_json(h)))
        elif method == "GET" and path == BASE + "/api/safe/connections":
            h.send_json(200, connections(user))
        elif method == "POST" and path.startswith(BASE + "/api/safe/connections/"):
            h.send_json(200, set_connected(h, user, path[len(BASE + "/api/safe/connections/"):], read_json(h)))
        elif method == "POST" and path == BASE + "/api/safe/settings/test":
            if not (IS_ADMIN and IS_ADMIN(user)):
                raise PermissionError
            cur = load_custom() or {}
            h.send_json(200, check(clean_backend(read_json(h), keep_secret=cur.get("secretKey", "")
                                                 or (chart_backend()["secretKey"] if not cur else ""))))
        else:
            h.send_json(404, {"error": "not found"})
    except ValueError as e:
        h.send_json(400, {"error": str(e)})
    except PermissionError:
        h.send_json(403, {"error": "Only admins change where the Cloud Drive keeps files." if "/settings" in path
                          else "Not yours."})
    except s3client.S3Error as e:
        if e.status == 404:
            h.send_json(404, {"error": "It is not in your Cloud Drive (any more)."})
        else:
            LOG(event="safe-s3-failed", user=user["username"], error=str(e)[:300])
            h.send_json(502, {"error": "The Cloud Drive's storage answered: %s" % (e.message or e.code or e.status)})
    except (OSError, RuntimeError) as e:
        LOG(event="safe-failed", user=user["username"], error=str(e)[:300])
        h.send_json(503, {"error": "The Cloud Drive's storage could not be reached: %s" % str(e)[:200]})
    return True

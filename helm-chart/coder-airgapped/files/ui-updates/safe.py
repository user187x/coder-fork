"""The safe: each account's own files on S3 (the chart's userStorage), a prefix users/<user id>/ in one bucket.

Every workspace of the account mounts it (PersistentVolumeClaim coder-safe-<user id>, ReadWriteMany, through the CSI
driver ru.yandex.s3.csi), and the Workspaces page opens it. This module keeps, every RECONCILE_EVERY seconds: the
bucket; the Secret the safes' volumes name for mounting (coder-user-storage-mount: the keys, and an endpoint the nodes
reach - GeeseFS runs on the node, outside the cluster's DNS, so the chart's MinIO by its ClusterIP); and for every
account a PersistentVolume (Retain: removing it never removes files) and its claim.

  GET  /__coder-ui/api/safe                   the caller's safe: used, quota, where it is mounted
  GET  /__coder-ui/api/safe/list?path=a/b     a folder of it: folders and files
  GET  /__coder-ui/api/safe/download?path=    a file, as an attachment
  PUT  /__coder-ui/api/safe/upload?path=      a file (the body; Content-Length), within the quota
  POST /__coder-ui/api/safe/folder {path}     a new folder
  POST /__coder-ui/api/safe/delete {path}     a file, or a folder (path ending in /) with everything in it

Only ever the caller's own prefix: paths are checked segment by segment (no "..", no control characters).

The ISO library (the Images view): disk images for every workspace, a prefix library/isos/ in the same bucket, mounted
read-only at ~/isos in each workspace (one shared volume, coder-isos, ReadOnlyMany). Everyone sees them; admins and
template admins add and remove them.

  GET  /__coder-ui/api/isos                   the library, and the imports under way
  PUT  /__coder-ui/api/isos/upload?name=      an ISO (the body; Content-Length)
  POST /__coder-ui/api/isos/import {url, name}   an ISO the service downloads (http/https), in the background
  POST /__coder-ui/api/isos/delete {name}
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
BUCKET = os.environ.get("SAFE_BUCKET", "coder-user-storage")
PREFIX = "users/"
MOUNT_PATH = os.environ.get("SAFE_MOUNT_PATH", "/home/coder/safe")
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
# The ISO library.
ISO_PREFIX = "library/isos/"
ISO_MOUNT_PATH = os.environ.get("SAFE_ISO_MOUNT_PATH", "/home/coder/isos")
ISO_MAX_BYTES = int(float(os.environ.get("SAFE_ISO_MAX_GIB", "20")) * 1024 ** 3)
ISO_CLAIM = "coder-isos"
ISO_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,120}\.(iso|img)$", re.I)

LOG = KUBE = USERS = CAN_EDIT = None
_imports = {}  # ISO name -> its download from a URL: {name, url, bytes, total, error, done, by, startedAt}
_usage = {}  # user id -> (time, bytes, files)
_volumes = {}  # user id -> time its volume was last seen in place
_lock = threading.Lock()


def setup(log, kube, users, can_edit=None):
    """users(): [(id, username)] of every account (Coder's database); can_edit(user): may change the ISO library."""
    global LOG, KUBE, USERS, CAN_EDIT
    LOG, KUBE, USERS, CAN_EDIT = log, kube, users, can_edit


def client():
    return s3client.S3(S3_ENDPOINT, S3_REGION, os.environ.get("S3_ACCESS_KEY", ""), os.environ.get("S3_SECRET_KEY", ""))


def capacity():
    return "%dGi" % max(1, QUOTA_BYTES // 1024 ** 3)


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
    want = {"accessKeyID": os.environ.get("S3_ACCESS_KEY", ""), "secretAccessKey": os.environ.get("S3_SECRET_KEY", ""),
            "endpoint": node_endpoint()}
    if S3_REGION:
        want["region"] = S3_REGION
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
                   "csi": {"driver": CSI_DRIVER, "volumeHandle": "%s/%s" % (BUCKET, root_of(user_id).rstrip("/")),
                           "nodeStageSecretRef": ref, "nodePublishSecretRef": ref,
                           "volumeAttributes": {"capacity": capacity(), "mounter": "geesefs", "options": MOUNT_OPTIONS}}}}
    pvc = {"apiVersion": "v1", "kind": "PersistentVolumeClaim",
           "metadata": {"name": name, "namespace": NAMESPACE, "labels": labels,
                        "annotations": {"platform.coder/username": username}},
           "spec": {"accessModes": ["ReadWriteMany"], "storageClassName": "", "volumeName": name,
                    "resources": {"requests": {"storage": capacity()}}}}
    return pv, pvc


def iso_volume_objects():
    """The ISO library's volume and claim: the library's prefix, read-only, for every workspace."""
    labels = {"app.kubernetes.io/name": "coder-isos", "app.kubernetes.io/part-of": "coder"}
    ref = {"name": MOUNT_SECRET, "namespace": NAMESPACE}
    pv = {"apiVersion": "v1", "kind": "PersistentVolume", "metadata": {"name": ISO_CLAIM, "labels": labels},
          "spec": {"capacity": {"storage": "%dGi" % max(1, ISO_MAX_BYTES // 1024 ** 3)},
                   "accessModes": ["ReadOnlyMany"], "persistentVolumeReclaimPolicy": "Retain", "storageClassName": "",
                   "claimRef": {"namespace": NAMESPACE, "name": ISO_CLAIM},
                   "csi": {"driver": CSI_DRIVER, "volumeHandle": "%s/%s" % (BUCKET, ISO_PREFIX.rstrip("/")),
                           "readOnly": True, "nodeStageSecretRef": ref, "nodePublishSecretRef": ref,
                           "volumeAttributes": {"capacity": "1Gi", "mounter": "geesefs", "options": MOUNT_OPTIONS}}}}
    pvc = {"apiVersion": "v1", "kind": "PersistentVolumeClaim",
           "metadata": {"name": ISO_CLAIM, "namespace": NAMESPACE, "labels": labels},
           "spec": {"accessModes": ["ReadOnlyMany"], "storageClassName": "", "volumeName": ISO_CLAIM,
                    "resources": {"requests": {"storage": pv["spec"]["capacity"]["storage"]}}}}
    return pv, pvc


def ensure_volume(user_id, username="", objects=None):
    """Makes the account's volume and claim (or the given pair) where missing; True when both are there."""
    pv, pvc = objects or volume_objects(user_id, username)
    name = pv["metadata"]["name"]
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
    s3 = client()
    if not s3.bucket_exists(BUCKET):
        s3.create_bucket(BUCKET)
        LOG(event="safe-bucket", action="created", bucket=BUCKET)
    ensure_mount_secret()
    now = time.time()
    if now - _volumes.get(ISO_CLAIM, 0) >= 600:
        ensure_volume("", "the ISO library", objects=iso_volume_objects())
        _volumes[ISO_CLAIM] = now
    for user_id, username in USERS() or []:
        # Seen in place in the last 10 minutes: not asked again every round.
        if now - _volumes.get(user_id, 0) < 600:
            continue
        try:
            ensure_volume(user_id, username)
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
        time.sleep(RECONCILE_EVERY)


def start():
    if ENABLED and KUBE and KUBE.ok:
        threading.Thread(target=_loop, name="safe", daemon=True).start()


# ---------------------------------------------------------------------------------------------------- the files
def usage(user_id, fresh=False):
    """(bytes, files) in the account's safe, cached USAGE_TTL seconds."""
    with _lock:
        hit = _usage.get(user_id)
    if hit and not fresh and time.time() - hit[0] < USAGE_TTL:
        return hit[1], hit[2]
    objects, _ = client().list(BUCKET, prefix=root_of(user_id))
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
    return {"enabled": True, "mountPath": MOUNT_PATH, "usedBytes": used, "files": files, "quotaBytes": QUOTA_BYTES,
            "maxUploadBytes": MAX_UPLOAD_BYTES, "volume": "ready" if bound else "pending"}


def listing(user, path):
    """A folder of the caller's safe: its folders and files, folders first, by name."""
    path = clean_path(path)
    prefix = root_of(user["id"]) + (path + "/" if path else "")
    objects, prefixes = client().list(BUCKET, prefix=prefix, delimiter="/")
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
    resp = client().get(BUCKET, root_of(user["id"]) + path)
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
        existing = int(s3.head(BUCKET, key).headers.get("Content-Length") or 0)
    except s3client.S3Error as e:
        if e.status != 404:
            raise
    if used - existing + length > QUOTA_BYTES:
        return h.send_json(413, {"error": "Your safe holds %s; this file would take it past its %s." % (
            human(used), human(QUOTA_BYTES))})
    s3.put_large(BUCKET, key, h.rfile, length)
    with _lock:
        _usage.pop(user["id"], None)
    LOG(event="safe-upload", user=user["username"], path=path, bytes=length)
    return h.send_json(200, {"ok": True, "path": path, "size": length})


def make_folder(user, path):
    path = clean_path(path)
    if not path:
        raise ValueError("Name the folder.")
    client().put(BUCKET, root_of(user["id"]) + path + "/", b"")
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
        objects, _ = s3.list(BUCKET, prefix=key + "/")
        keys = [o["key"] for o in objects]
        failed = s3.delete_many(BUCKET, keys) if keys else []
        if failed:
            raise RuntimeError("%d could not be deleted (%s)" % (len(failed), failed[0][1]))
        removed = len([k for k in keys if not k.endswith("/")])
    else:
        s3.delete(BUCKET, key)
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


# ---------------------------------------------------------------------------------------------------- the ISO library
def iso_name(name):
    name = str(name or "").strip()
    if not ISO_NAME_RE.match(name):
        raise ValueError("A file name of letters, digits and . _ + -, ending in .iso or .img.")
    return name


def iso_library(user):
    objects, _ = client().list(BUCKET, prefix=ISO_PREFIX)
    files = sorted(({"name": o["key"][len(ISO_PREFIX):], "size": o["size"], "modified": o["modified"]}
                    for o in objects if "/" not in o["key"][len(ISO_PREFIX):] and o["key"] != ISO_PREFIX),
                   key=lambda f: f["name"].lower())
    with _lock:
        imports = [dict(i) for i in _imports.values()]
    return {"enabled": True, "canEdit": bool(CAN_EDIT and CAN_EDIT(user)), "mountPath": ISO_MOUNT_PATH,
            "maxBytes": ISO_MAX_BYTES, "files": files, "usedBytes": sum(f["size"] for f in files),
            "imports": sorted(imports, key=lambda i: i["startedAt"], reverse=True)}


def iso_upload(h, user, name):
    name = iso_name(name)
    length = int(h.headers.get("Content-Length") or -1)
    if length < 0:
        raise ValueError("The upload needs a Content-Length.")
    if length > ISO_MAX_BYTES:
        return h.send_json(413, {"error": "An ISO can be at most %s." % human(ISO_MAX_BYTES)})
    client().put_large(BUCKET, ISO_PREFIX + name, h.rfile, length)
    LOG(event="iso-upload", user=user["username"], name=name, bytes=length)
    return h.send_json(200, {"ok": True, "name": name, "size": length})


def _download(state, url):
    """An ISO from a URL into the library, in parts as it comes; its progress in state."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "coder-platform"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            total = int(resp.headers.get("Content-Length") or -1)
            if total < 0:
                raise ValueError("the server does not say how large the file is (no Content-Length)")
            if total > ISO_MAX_BYTES:
                raise ValueError("it is %s, more than the %s an ISO may be" % (human(total), human(ISO_MAX_BYTES)))
            state["total"] = total

            def progress(n):
                state["bytes"] = n
            client().put_large(BUCKET, ISO_PREFIX + state["name"], resp, total, progress=progress)
        state["done"] = True
        LOG(event="iso-import", user=state["by"], name=state["name"], url=url, bytes=state.get("total"))
    except Exception as e:  # noqa: BLE001 - shown in the library until dismissed by the next import of the name
        state["error"] = str(e)[:300]
        LOG(event="iso-import-failed", user=state["by"], name=state["name"], error=str(e)[:300])


def iso_import(user, body):
    url = str(body.get("url") or "").strip()
    if not re.match(r"^https?://[^\s]+$", url):
        raise ValueError("An http:// or https:// address of the ISO.")
    name = iso_name(body.get("name") or urllib.parse.unquote(urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1]))
    with _lock:
        cur = _imports.get(name)
        if cur and not cur.get("done") and not cur.get("error"):
            raise ValueError("%s is being imported already." % name)
        state = {"name": name, "url": url, "bytes": 0, "total": None, "error": None, "done": False,
                 "by": user["username"], "startedAt": time.time()}
        _imports[name] = state
    threading.Thread(target=_download, args=(state, url), name="iso-import", daemon=True).start()
    return {"ok": True, "name": name}


def handle_isos(h, method, path, query):
    """The ISO library's API. True when it answered."""
    if not path.startswith(BASE + "/api/isos"):
        return False
    if not ENABLED:
        h.send_json(404, {"error": "The ISO library needs the safe's storage (global.userStorage)."})
        return True
    if method != "GET" and not upload_csrf_ok(h):
        h.send_json(403, {"error": "Request rejected (cross-site protection)."})
        return True
    user, err = h.user()
    if err:
        return True
    q = dict(urllib.parse.parse_qsl(query or "", keep_blank_values=True))
    try:
        if method == "GET" and path == BASE + "/api/isos":
            return h.send_json(200, iso_library(user)) or True
        if not (CAN_EDIT and CAN_EDIT(user)):
            raise PermissionError
        if method == "PUT" and path == BASE + "/api/isos/upload":
            iso_upload(h, user, q.get("name", ""))
        elif method == "POST" and path == BASE + "/api/isos/import":
            h.send_json(200, iso_import(user, read_json(h)))
        elif method == "POST" and path == BASE + "/api/isos/delete":
            name = iso_name(read_json(h).get("name"))
            client().delete(BUCKET, ISO_PREFIX + name)
            with _lock:
                _imports.pop(name, None)
            LOG(event="iso-delete", user=user["username"], name=name)
            h.send_json(200, {"ok": True})
        else:
            h.send_json(404, {"error": "not found"})
    except ValueError as e:
        h.send_json(400, {"error": str(e)})
    except PermissionError:
        h.send_json(403, {"error": "Only admins and template admins change the ISO library."})
    except s3client.S3Error as e:
        LOG(event="iso-s3-failed", user=user["username"], error=str(e)[:300])
        h.send_json(502, {"error": "The library's storage answered: %s" % (e.message or e.code or e.status)})
    except (OSError, RuntimeError) as e:
        h.send_json(503, {"error": "The library's storage could not be reached: %s" % str(e)[:200]})
    return True


def handle(h, method, path, query):
    """True when it answered."""
    if not path.startswith(BASE + "/api/safe"):
        return False
    if not ENABLED:
        h.send_json(404, {"error": "The safe is not enabled on this deployment."})
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
        else:
            h.send_json(404, {"error": "not found"})
    except ValueError as e:
        h.send_json(400, {"error": str(e)})
    except PermissionError:
        h.send_json(403, {"error": "Not yours."})
    except s3client.S3Error as e:
        if e.status == 404:
            h.send_json(404, {"error": "It is not in your safe (any more)."})
        else:
            LOG(event="safe-s3-failed", user=user["username"], error=str(e)[:300])
            h.send_json(502, {"error": "The safe's storage answered: %s" % (e.message or e.code or e.status)})
    except (OSError, RuntimeError) as e:
        LOG(event="safe-failed", user=user["username"], error=str(e)[:300])
        h.send_json(503, {"error": "The safe's storage could not be reached: %s" % str(e)[:200]})
    return True

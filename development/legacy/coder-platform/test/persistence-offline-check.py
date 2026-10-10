#!/usr/bin/env python3
"""General > Persistence without a cluster: loads ../coder-ui-updates/resources/ui-service.py, answers its Kubernetes
API calls and database queries from fixtures, and checks the report and every fix (the requests they make).
   test/persistence-offline-check.py
Exit 1 on the first failed expectation."""
import copy
import urllib.parse
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "..", "coder-ui-updates", "resources", "ui-service.py")
os.environ.setdefault("CODER_NAMESPACE", "coder")
os.environ.setdefault("CODER_DEPLOYMENT", "coder")
os.environ.setdefault("CODER_DB_SECRET", "coder-db-app")
os.environ.setdefault("PG_IMAGE", "registry.local/cloudnative-pg/postgresql:18.4")
sys.path.insert(0, os.path.dirname(SRC))
sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location("ui_service", SRC)
ui = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ui)

GI = 2**30
CNPG = "/apis/postgresql.cnpg.io/v1/namespaces/coder"
Q = urllib.parse.quote  # the service encodes label selectors the same way
OPERATOR = "/apis/apps/v1/deployments?labelSelector=" + Q(
    "app.kubernetes.io/name=cloudnative-pg"
)
SELECT = "?labelSelector=" + Q("cnpg.io/cluster=coder-db")


def base_world():
    pod = lambda name, role, node: {  # noqa: E731
        "metadata": {
            "name": name,
            "namespace": "coder",
            "labels": {
                "cnpg.io/cluster": "coder-db",
                "cnpg.io/instanceRole": role,
                "cnpg.io/podRole": "instance",
            },
        },
        "spec": {"nodeName": node},
        "status": {
            "podIP": "10.0.0.1",
            "phase": "Running",
            "startTime": "2026-10-01T00:00:00Z",
            "containerStatuses": [{"ready": True, "restartCount": 0}],
        },
    }
    pvc = lambda name: {
        "metadata": {"name": name},
        "spec": {
            "storageClassName": "longhorn",  # noqa: E731
            "resources": {"requests": {"storage": "10Gi"}},
        },
        "status": {"phase": "Bound", "capacity": {"storage": "10Gi"}},
    }
    return {
        "/apis/apps/v1/namespaces/coder/deployments/coder": {
            "spec": {
                "template": {
                    "spec": {
                        "containers": [
                            {
                                "env": [
                                    {
                                        "name": "CODER_PG_CONNECTION_URL",
                                        "valueFrom": {
                                            "secretKeyRef": {
                                                "name": "coder-db-app",
                                                "key": "uri",
                                            }
                                        },
                                    }
                                ]
                            }
                        ]
                    }
                }
            }
        },
        "/api/v1/namespaces/coder/secrets/coder-db-app": {
            "data": {
                "uri": __import__("base64")
                .b64encode(
                    b"postgresql://app:secret@coder-db-rw.coder:5432/app?sslmode=require"
                )
                .decode()
            }
        },
        "/apis/apiextensions.k8s.io/v1/customresourcedefinitions/clusters.postgresql.cnpg.io": {
            "metadata": {}
        },
        OPERATOR: {
            "items": [
                {
                    "metadata": {"namespace": "cnpg-system"},
                    "status": {"readyReplicas": 1},
                    "spec": {
                        "template": {
                            "spec": {
                                "containers": [
                                    {
                                        "image": "ghcr.io/cloudnative-pg/cloudnative-pg:1.27.0"
                                    }
                                ]
                            }
                        }
                    },
                }
            ]
        },
        "/apis/storage.k8s.io/v1/storageclasses": {
            "items": [
                {
                    "metadata": {
                        "name": "longhorn",
                        "annotations": {
                            "storageclass.kubernetes.io/is-default-class": "true"
                        },
                    },
                    "provisioner": "driver.longhorn.io",
                    "allowVolumeExpansion": True,
                    "reclaimPolicy": "Delete",
                },
                {
                    "metadata": {"name": "local-path"},
                    "provisioner": "rancher.io/local-path",
                    "reclaimPolicy": "Delete",
                },
            ]
        },
        "/apis/snapshot.storage.k8s.io/v1/volumesnapshotclasses": {
            "items": [{"metadata": {"name": "longhorn-snap"}}]
        },
        CNPG
        + "/clusters/coder-db": {
            "metadata": {
                "name": "coder-db",
                "namespace": "coder",
                "labels": {"app.kubernetes.io/managed-by": "Helm"},
                "annotations": {"meta.helm.sh/release-name": "coder-platform"},
            },
            "spec": {"instances": 1, "storage": {"size": "10Gi"}},
            "status": {
                "phase": "Cluster in healthy state",
                "readyInstances": 1,
                "currentPrimary": "coder-db-1",
            },
        },
        "/api/v1/namespaces/coder/pods"
        + SELECT: {"items": [pod("coder-db-1", "primary", "node-a")]},
        "/api/v1/namespaces/coder/persistentvolumeclaims"
        + SELECT: {"items": [pvc("coder-db-1")]},
        CNPG + "/scheduledbackups": {"items": []},
        CNPG + "/backups": {"items": []},
        CNPG + "/clusters": {"items": []},
        "/api/v1/nodes": {
            "items": [
                {
                    "metadata": {
                        "name": "node-a",
                        "labels": {"topology.kubernetes.io/zone": "zone-a"},
                    }
                }
            ]
        },
        "/api/v1/pods?fieldSelector="
        + Q("status.phase!=Succeeded,status.phase!=Failed"): {"items": []},
        "/version": {"gitVersion": "v1.31.0"},
    }


class FakeKube:
    def __init__(self, world):
        self.world, self.writes = world, []

    def call(self, method, path, body=None, patch_type=None):
        if method == "GET":
            return (
                (200, copy.deepcopy(self.world[path]))
                if path in self.world
                else (404, {"message": "not found"})
            )
        self.writes.append((method, path, body))
        if method == "POST":
            self.world[path + "/" + body["metadata"]["name"]] = body
            return 201, body
        if method == "PATCH" and path in self.world:
            merge(self.world[path], body)
            return 200, self.world[path]
        return 404, {}

    def secret(self, ns, name):
        found = self.world.get("/api/v1/namespaces/%s/secrets/%s" % (ns, name))
        if not found:
            return None
        return {
            k: __import__("base64").b64decode(v).decode()
            for k, v in found["data"].items()
        }


def merge(into, patch):
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(into.get(k), dict):
            merge(into[k], v)
        else:
            into[k] = v


class FakePool:
    def __init__(self, size):
        self.size = size

    def query(self, sql, params=()):
        if "pg_stat_replication" in sql:
            return []
        return [(str(self.size), "12", "100", "18.4", "2026-10-01 00:00:00+00")]


class FakeLogos:
    def __init__(self, size):
        self.pool = FakePool(size)


failures = []


def expect(cond, what):
    print(("ok    " if cond else "FAIL  ") + what)
    if not cond:
        failures.append(what)


def run(world, db_bytes=8.6 * GI):
    kube = FakeKube(world)
    ui.KUBE, ui.LOGOS = kube, FakeLogos(db_bytes)
    ui.CACHE = ui.TTLCache()
    return kube, ui.persistence_report("token")


def by_id(report):
    return {c["id"]: c for c in report["checks"]}


# 1. One instance, 86% full, no backups: three suggested fixes.
kube, report = run(base_world())
c = by_id(report)
expect(report["mode"] == "cloudnative-pg", "mode is cloudnative-pg")
expect(
    report["facts"]["coderDatabase"]["host"] == "coder-db-rw.coder"
    and "password" not in str(report),
    "connection read, no password",
)
expect(
    c["cnpg.operator"]["status"] == "ok" and "1.27.0" in c["cnpg.operator"]["current"],
    "operator found with its version",
)
expect(
    c["cnpg.instances"]["fix"] == "cnpg.instances",
    "single instance: replicas suggested",
)
expect(
    c["cnpg.storage"]["fix"] == "cnpg.storage"
    and c["cnpg.storage"]["expected"] == "20Gi",
    "86% full: grow to 20Gi suggested",
)
expect(
    c["cnpg.backup"]["fix"] == "cnpg.backup", "no backups: snapshot backups suggested"
)
expect(
    c["cnpg.owner"]["current"] == "Helm release coder-platform",
    "Helm ownership reported",
)
expect(
    report["facts"]["instances"][0]["zone"] == "zone-a"
    and report["facts"]["instances"][0]["pvc"]["capacityBytes"] == 10 * GI,
    "instance with its zone and volume",
)
s = report["facts"]["settings"]
expect(
    s["instances"] == 3
    and s["storageClass"] == "longhorn"
    and s["snapshotClass"] == "longhorn-snap",
    "suggested settings",
)
expect(not kube.writes, "reading changes nothing")

# 2. Apply the three fixes.
s = {**s, "storageSize": "20Gi"}
done = ui.apply_persistence(
    ["cnpg.instances", "cnpg.storage", "cnpg.backup"], s, "admin"
)
patch = [w for w in kube.writes if w[0] == "PATCH"]
posts = [w for w in kube.writes if w[0] == "POST"]
expect(
    len(patch) == 1
    and patch[0][2]
    == {
        "spec": {
            "instances": 3,
            "storage": {"size": "20Gi"},
            "backup": {"volumeSnapshot": {"className": "longhorn-snap"}},
        }
    },
    "one merge patch: instances, storage, snapshot class",
)
expect(
    len(posts) == 1
    and posts[0][2]["kind"] == "ScheduledBackup"
    and posts[0][2]["spec"]["method"] == "volumeSnapshot"
    and posts[0][2]["spec"]["schedule"] == "0 0 2 * * *",
    "ScheduledBackup created",
)
expect(any("deployed from" in d for d in done), "warns that Helm manages the cluster")

# 3. The report after the fixes.
kube.world[CNPG + "/scheduledbackups"] = {
    "items": [kube.world[CNPG + "/scheduledbackups/coder-db-daily"]]
}
ui.KUBE = kube
report = ui.persistence_report("token")
c = by_id(report)
expect(c["cnpg.instances"]["status"] == "ok", "3 instances now")
expect(c["cnpg.backup"]["status"] == "ok", "backups now scheduled")
kube.world[CNPG + "/clusters/coder-db"]["spec"]["instances"] = 2
report = ui.persistence_report("token")
expect(
    report["facts"]["settings"]["instances"] == 2,
    "a cluster with replicas keeps its instance count as the suggestion",
)

# 4. Validation.
for bad, why in (
    ({**s, "storageSize": "5Gi"}, "shrink"),
    ({**s, "clusterName": "Bad_Name"}, "bad name"),
    ({**s, "instances": 12}, "too many instances"),
    ({**s, "backupSchedule": "0 2 * * *"}, "5-field cron"),
):
    try:
        ui.apply_persistence(["cnpg.storage"], bad, "admin")
        expect(False, "rejects " + why)
    except ValueError:
        expect(True, "rejects " + why)

# 5. Creating a cluster (operator installed, Coder not on CloudNativePG).
world = base_world()
del world[CNPG + "/clusters/coder-db"]
kube, report = run(world)
c = by_id(report)
expect(
    report["facts"]["cluster"] is None and c["cnpg.cluster"]["fix"] == "cnpg.cluster",
    "no cluster: creating one suggested",
)
done = ui.apply_persistence(
    ["cnpg.cluster", "cnpg.backup"],
    {**report["facts"]["settings"], "clusterName": "coder-db2"},
    "admin",
)
created = next(
    w[2] for w in kube.writes if w[0] == "POST" and w[2]["kind"] == "Cluster"
)
expect(
    created["spec"]["instances"] == 3
    and created["spec"]["storage"] == {"size": "10Gi", "storageClass": "longhorn"}
    and created["spec"]["imageName"] == os.environ["PG_IMAGE"],
    "Cluster body: instances, storage, air-gap image",
)
expect(
    created["metadata"]["labels"]["app.kubernetes.io/created-by"] == "coder-ui-updates",
    "Cluster labelled as made here",
)
expect(
    any(w[2].get("kind") == "ScheduledBackup" for w in kube.writes if w[0] == "POST"),
    "backups scheduled for the new cluster",
)

# 6. No operator.
world = base_world()
del world[
    "/apis/apiextensions.k8s.io/v1/customresourcedefinitions/clusters.postgresql.cnpg.io"
]
world[OPERATOR] = {"items": []}
kube, report = run(world)
c = by_id(report)
expect(
    c["cnpg.operator"]["fix"] == "cnpg.operator"
    and c["cnpg.operator"]["status"] == "error",
    "operator missing: guided setup",
)

# 7. Amazon RDS.
world = base_world()
world["/api/v1/namespaces/coder/secrets/coder-db-app"]["data"]["uri"] = (
    __import__("base64")
    .b64encode(
        b"postgres://coder:pw@coder.abc123.us-east-1.rds.amazonaws.com:5432/coder?sslmode=verify-full"
    )
    .decode()
)
kube, report = run(world)
c = by_id(report)
expect(
    report["mode"] == "external" and report["facts"]["provider"] == "Amazon RDS",
    "RDS recognised",
)
expect(
    "cnpg.operator" not in c and "cnpg.cluster" not in c,
    "no CloudNativePG suggestions for a cloud database",
)

print("ALL OK" if not failures else "%d FAILED" % len(failures))
sys.exit(1 if failures else 0)

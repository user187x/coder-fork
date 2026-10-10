#!/usr/bin/env python3
"""General > Persistence against a real cluster, as the coder-ui-updates ServiceAccount (so its RBAC is what is tested):
create Coder's CloudNativePG cluster if it has none, wait for it, scale it to 2 instances, and check the report at every
step. The CloudNativePG operator must be installed; Coder's Deployment must read CODER_PG_CONNECTION_URL from Secret
<cluster>-app.
   test/persistence-live-check.py <api server url> <ca file> <service account token> [namespace] [cluster]
Exit 1 on the first failed expectation. Leaves the cluster in place."""
import os
import sys
import tempfile
import time
import urllib.parse

api, ca, token = sys.argv[1:4]
ns = sys.argv[4] if len(sys.argv) > 4 else "coder"
name = sys.argv[5] if len(sys.argv) > 5 else "coder-db"
u = urllib.parse.urlsplit(api)
os.environ.update(
    {
        "KUBERNETES_SERVICE_HOST": u.hostname,
        "KUBERNETES_SERVICE_PORT": str(u.port or 443),
        "CODER_NAMESPACE": ns,
        "CODER_DEPLOYMENT": "coder",
        "CODER_DB_SECRET": name + "-app",
    }
)
sa = tempfile.mkdtemp()
with open(os.path.join(sa, "token"), "w") as f:
    f.write(token)
with open(ca) as src, open(os.path.join(sa, "ca.crt"), "w") as f:
    f.write(src.read())

sys.dont_write_bytecode = True
SRC = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..",
    "..",
    "coder-ui-updates",
    "resources",
    "ui-service.py",
)
sys.path.insert(0, os.path.dirname(SRC))
import importlib.util  # noqa: E402

spec = importlib.util.spec_from_file_location("ui_service", SRC)
ui = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ui)
ui.Kube.SA = sa
ui.KUBE = ui.Kube()

failures = []


def expect(cond, what):
    print(("ok    " if cond else "FAIL  ") + what, flush=True)
    if not cond:
        failures.append(what)
        print("%d FAILED" % len(failures))
        sys.exit(1)


def report():
    ui.CACHE = ui.TTLCache()
    r = ui.persistence_report("unused")
    return r, {c["id"]: c for c in r["checks"]}


def wait_ready(instances, timeout=600):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r, c = report()
        cl = r["facts"]["cluster"]
        if (
            cl
            and cl["readyInstances"] >= instances
            and len([i for i in r["facts"]["instances"] if i["ready"]]) >= instances
        ):
            return r, c
        time.sleep(5)
    return report()


r, c = report()
expect(
    r["facts"]["operator"]["installed"] and r["facts"]["operator"]["ready"],
    "operator found through the ClusterRole (%s %s)"
    % (r["facts"]["operator"]["namespace"], r["facts"]["operator"]["version"]),
)
expect(
    bool(r["facts"]["storageClasses"]),
    "storage classes readable: %s" % [s["name"] for s in r["facts"]["storageClasses"]],
)
for check in r["checks"]:
    print(
        "      %-7s %-18s %s" % (check["status"], check["id"], check["current"] or "")
    )

if r["facts"]["cluster"] is None:
    expect(
        c.get("cnpg.cluster", {}).get("fix") == "cnpg.cluster",
        "no cluster: creating one is suggested",
    )
    settings = {
        **r["facts"]["settings"],
        "clusterName": name,
        "instances": 1,
        "storageSize": "1Gi",
    }
    for line in ui.apply_persistence(["cnpg.cluster"], settings, "live-check"):
        print("      done: " + line)
    r, c = wait_ready(1)
expect(
    r["facts"]["cluster"] is not None and r["facts"]["cluster"]["name"] == name,
    "Coder's connection resolves to cluster %s" % name,
)
expect(
    r["facts"]["cluster"]["readyInstances"] >= 1,
    "cluster ready (%s)" % r["facts"]["cluster"]["phase"],
)
primary = next(i for i in r["facts"]["instances"] if i["role"] == "primary")
expect(
    primary["pvc"] and primary["pvc"]["capacityBytes"],
    "primary %s on volume %s (%s)"
    % (primary["name"], primary["pvc"]["name"], primary["pvc"]["storageClass"]),
)

if r["facts"]["cluster"]["instances"] < 2:
    expect(
        c["cnpg.instances"]["fix"] == "cnpg.instances",
        "one instance: replicas suggested",
    )
    for line in ui.apply_persistence(
        ["cnpg.instances"], {**r["facts"]["settings"], "instances": 2}, "live-check"
    ):
        print("      done: " + line)
    r, c = wait_ready(2)
expect(
    c["cnpg.instances"]["status"] == "ok",
    "replicas: %s" % c["cnpg.instances"]["current"],
)
expect(
    c["cnpg.ready"]["status"] == "ok",
    "instances ready: %s" % c["cnpg.ready"]["current"],
)
replica = next((i for i in r["facts"]["instances"] if i["role"] == "replica"), None)
expect(
    replica is not None and replica["ready"],
    "replica %s ready" % (replica or {}).get("name"),
)

sc = next(
    s
    for s in r["facts"]["storageClasses"]
    if s["name"] == (primary["pvc"]["storageClass"])
)
try:
    ui.apply_persistence(
        ["cnpg.storage"], {**r["facts"]["settings"], "storageSize": "2Gi"}, "live-check"
    )
    expect(sc["allowExpansion"], "growing storage accepted (class allows expansion)")
except ValueError as e:
    expect(
        not sc["allowExpansion"], "growing storage refused on %s: %s" % (sc["name"], e)
    )

r, c = report()
for check in r["checks"]:
    print(
        "      %-7s %-18s %s" % (check["status"], check["id"], check["current"] or "")
    )
print("ALL OK")

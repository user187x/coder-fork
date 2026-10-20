"""Unit tests of the platform service (helm-chart/coder-airgapped/files/ui-updates): the parts that decide access,
keep it fast under load, and keep the image builder honest offline. Imported as modules, nothing started.

  python3 -m unittest platform_unit_test.py"""
import contextlib, importlib.util, io, json, os, sys, threading, unittest, urllib.parse
from unittest import mock

SRC = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "../../../helm-chart/coder-airgapped/files/ui-updates"))
sys.path.insert(0, SRC)


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, os.path.join(SRC, file))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ui = load("ui_service", "ui-service.py")
ib = load("imagebuilder", "imagebuilder.py")
ld = load("lockdown", "lockdown.py")
pv = load("provisioners", "provisioners.py")


class NodeScanTest(unittest.TestCase):
    """Images: any image can be scanned - from the builder's registry, else from a node that holds it (air-gapped,
    preloaded), with the image's files copied out by the tools image's busybox run through musl's loader."""

    def test_where_an_image_is_scanned_from(self):
        nodes = {"ghcr.io/x/ws:1": {"sizeBytes": 1, "nodes": ["n2", "n1"]}}
        with mock.patch.object(ib, "node_images", return_value=nodes):
            self.assertEqual(ib.scan_target("ghcr.io/x/ws:1"), ("ghcr.io/x/ws:1", "n1"))
            self.assertEqual(ib.scan_target("harbor.local/x/other:2"), ("harbor.local/x/other:2", None))
            self.assertEqual(ib.scan_target(ib.PULL_PREFIX + "/coder-images/a:3"),
                             (ib.REGISTRY + "/coder-images/a:3", None))

    def test_scanned_entries_are_named_after_the_image(self):
        a = ib.scanned_id("ghcr.io/user187x/coder-workspace-dataflow:1.1.0")
        self.assertRegex(a, r"^scan-coder-workspace-dataflow-1-1-0-[0-9a-f]{6}$")
        self.assertNotEqual(a, ib.scanned_id("other.io/user187x/coder-workspace-dataflow:1.1.0"))

    def test_the_node_job_copies_the_files_as_root_and_runs_nothing_privileged(self):
        _, job = ib.scan_job("scan-x", 7, 1, "tok", ref="ghcr.io/x/ws:1", node="n1")
        spec = job["spec"]["template"]["spec"]
        self.assertEqual(spec["nodeName"], "n1")
        names = [c["name"] for c in spec["initContainers"]]
        self.assertEqual(names[:3], ["scan-tools", "image-user", "image-files"])
        user, copy = spec["initContainers"][1], spec["initContainers"][2]
        for c in (user, copy):
            self.assertEqual(c["image"], "ghcr.io/x/ws:1")
            self.assertEqual(c["imagePullPolicy"], "Never")       # the node's copy, never pulled
            self.assertEqual(c["command"][:2], ib.NODE_BUSYBOX)    # the loader runs busybox in any image
            self.assertFalse(c["securityContext"]["allowPrivilegeEscalation"])
        self.assertNotIn("runAsUser", user["securityContext"])   # the image's own default user
        self.assertEqual(copy["securityContext"]["capabilities"], {"drop": ["ALL"], "add": ["DAC_READ_SEARCH"]})
        self.assertIn("scan-rootfs", spec["containers"][0]["command"])

    def test_reads_a_copied_image_like_a_registry_one(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "etc"))
            with open(os.path.join(d, "etc/os-release"), "w") as f:
                f.write('PRETTY_NAME="Ubuntu 24.04 LTS"\n')
            with open(os.path.join(d, "etc/shadow"), "w") as f:
                f.write("root:!:1::::::\ncoder::1::::::\n")
            files, contents = ib.walk_rootfs(d)
            self.assertIn("etc/shadow", files)
            self.assertEqual(files["etc"][2], "d")
            data = {"stamp": {"vulnerabilityDbUpdatedAt": "2026-10-01"}, "java": True, "rules": None}
            trivy = {"Results": [{"Target": "x", "Vulnerabilities": [
                {"VulnerabilityID": "CVE-1", "PkgName": "openssl", "Severity": "HIGH", "FixedVersion": "3.1"}]}]}
            report = ib._report("ghcr.io/x/ws:1", trivy, None, data, {"config": {"User": "1000"}}, files, contents,
                                "node")
        self.assertEqual(report["source"], "node")
        self.assertEqual(report["os"], "Ubuntu 24.04 LTS")
        self.assertEqual(report["vulnerabilities"]["counts"]["HIGH"], 1)
        self.assertEqual(next(c for c in report["checks"] if c["id"] == "passwords")["status"], "fail")
        self.assertEqual(next(c for c in report["checks"] if c["id"] == "user")["status"], "pass")


class ProvisionersTest(unittest.TestCase):
    """Provisioner Daemons: the terraform processes read from a Coder pod, the exec WebSocket's frames, and a re-kick
    that stops only the processes of the jobs it was given."""

    def test_parses_processes_and_their_subcommand(self):
        procs = pv.parse_processes("812 S 930 /usr/local/bin/terraform apply -auto-approve -json\n"
                                   "900 Z 12 terraform\njunk\n")
        self.assertEqual([(p["pid"], p["command"], p["defunct"], p["ageSeconds"]) for p in procs],
                         [(812, "apply", False, 930), (900, "", True, 12)])

    def test_reads_websocket_frames(self):
        import struct as st
        big = b"\x01" + b"x" * 300
        frames = (bytes([0x82, len(b"\x01hi")]) + b"\x01hi"
                  + bytes([0x82, 126]) + st.pack(">H", len(big)) + big
                  + bytes([0x82, 0x80 | 3]) + b"\x00\x00\x00\x00" + b"\x02ok"
                  + bytes([0x88, 0]))

        class Sock:
            def __init__(self, data):
                self.data = data

            def recv(self, n):
                out, self.data = self.data[:n], self.data[n:]
                return out
        r = pv._Frames(Sock(frames[5:]), frames[:5])
        self.assertEqual(r.next(), (2, b"\x01hi"))
        self.assertEqual(r.next(), (2, big))
        self.assertEqual(r.next(), (2, b"\x02ok"))
        self.assertEqual(r.next()[0], 8)
        self.assertIsNone(r.next())

    def test_rekick_stops_only_what_predates_the_jobs_still_running(self):
        stuck = {"id": "j1", "status": "running", "ageSeconds": 3600, "workspaceBuildId": "b1", "workspace": "w"}
        healthy = {"id": "j2", "status": "running", "ageSeconds": 60, "workspaceBuildId": "b2", "workspace": "v"}
        procs = {"coder-0": [{"pid": 10, "state": "S", "defunct": False, "ageSeconds": 3590, "command": "apply"},
                             {"pid": 11, "state": "S", "defunct": False, "ageSeconds": 50, "command": "apply"},
                             {"pid": 12, "state": "Z", "defunct": True, "ageSeconds": 4000, "command": ""}]}
        calls = []
        with mock.patch.object(pv, "jobs", side_effect=lambda *a, **k: [stuck, healthy]), \
                mock.patch.object(pv, "processes", return_value=procs), \
                mock.patch.object(pv, "cancel", return_value=None), \
                mock.patch.object(pv, "exec_in", side_effect=lambda pod, cmd, **k: calls.append((pod, cmd)) or (0, "", "")), \
                mock.patch.object(pv, "CANCEL_WAIT", 0), mock.patch.object(pv.time, "sleep"):
            out = pv.rekick("token", ["j1"], "admin")
        self.assertEqual([p["pid"] for p in out["stopped"]], [10])   # not the healthy job's, not the zombie
        self.assertIn("kill -TERM 10", calls[0][1][2])
        self.assertEqual(out["stillRunning"], ["j1"])
        with mock.patch.object(pv, "jobs", return_value=[]):
            with self.assertRaises(LookupError):
                pv.rekick("token", ["gone"], "admin")


class TTLCacheTest(unittest.TestCase):
    def test_lru_and_expiry(self):
        c = ui.TTLCache()
        c.MAX = 3
        for k in "abcd":
            c.put(k, k, 60)
        self.assertIsNone(c.get("a"))           # the least recently used went
        c.get("b"); c.put("e", "e", 60)
        self.assertEqual(c.get("b"), "b")       # used, so kept
        self.assertIsNone(c.get("c"))
        c.put("x", 1, -1)
        self.assertIsNone(c.get("x"))           # expired

    def test_a_large_team_fits(self):
        c = ui.TTLCache()
        for i in range(15000):
            c.put("me:%d" % i, i, 60)
        self.assertEqual(c.get("me:0"), 0)      # no wholesale clear past 2000 entries any more
        self.assertLessEqual(len(c.items), c.MAX)

    def test_threads(self):
        c = ui.TTLCache()
        ts = [threading.Thread(target=lambda n=n: [c.put("%d-%d" % (n, i), i, 60) or c.get("%d-%d" % (n, i)) for i in range(2000)])
              for n in range(8)]
        [t.start() for t in ts]; [t.join() for t in ts]
        self.assertLessEqual(len(c.items), c.MAX)


class ClusterAdminTargetsTest(unittest.TestCase):
    KC = [{"id": "k-admin", "username": "admin"}, {"id": "k-greg", "username": "greg", "email": "greg@greg.com"},
          {"id": "k-bob", "username": "bob", "email": "bob@other.com"}]

    def by(self, field, value):
        return [u for u in self.KC if (u.get(field) or "").lower() == value.lower()]

    def test_email_first_then_username(self):
        out = ui.cluster_admin_targets([{"username": "admin", "email": "admin@admin.com"},
                                        {"username": "greg2", "email": "greg@greg.com"}], self.by)
        self.assertEqual(out, {"k-admin": "admin", "k-greg": "greg"})

    def test_no_match_no_member(self):
        self.assertEqual(ui.cluster_admin_targets([{"username": "nobody", "email": "x@y.z"}], self.by), {})


class ImageBuilderOfflineTest(unittest.TestCase):
    def test_gnome_without_sources_is_refused_up_front(self):
        avail = {p: {"available": False} for p in ib.GNOME}
        problems = ib.spec_problems({"desktop": "gnome", "packages": [], "installations": []}, avail)
        self.assertTrue(any("GNOME" in p and "apt mirror" in p for p in problems), problems)

    def test_package_without_sources_is_refused(self):
        problems = ib.spec_problems({"desktop": "keep", "packages": ["htop"], "installations": []},
                                    {"htop": {"available": False}})
        self.assertTrue(any("htop" in p for p in problems))

    def test_xfce_needs_nothing(self):
        self.assertEqual(ib.spec_problems({"desktop": "xfce", "packages": [], "installations": []}, {}), [])

    def test_sources_parse(self):
        src = "Types: deb\nURIs: http://mirror.local/ubuntu/\nSuites: resolute resolute-updates\nComponents: main universe\n"
        self.assertEqual(ib.parse_sources(src), [("http://mirror.local/ubuntu", "resolute", ["main", "universe"]),
                                                 ("http://mirror.local/ubuntu", "resolute-updates", ["main", "universe"])])

    def test_base_seeded_or_fallback(self):
        spec = {"base": {"kind": "workspace"}}
        with mock.patch.object(ib, "BASE_IMAGE", ib.REGISTRY + "/coder-images/base/coder-workspace:1.8.0"), \
                mock.patch.object(ib, "BASE_FALLBACK", "reg.example/user187x/coder-workspace:1.8.0"):
            with mock.patch.object(ib, "registry", side_effect=OSError("404")):
                self.assertEqual(ib.base_ref_for(spec), "reg.example/user187x/coder-workspace:1.8.0")
            with mock.patch.object(ib, "registry", return_value=mock.MagicMock()):
                self.assertEqual(ib.base_ref_for(spec), ib.REGISTRY + "/coder-images/base/coder-workspace:1.8.0")
        with mock.patch.object(ib, "BASE_IMAGE", ib.REGISTRY + "/coder-images/base/coder-workspace:1.8.0"), \
                mock.patch.object(ib, "BASE_FALLBACK", ""), mock.patch.object(ib, "registry", side_effect=OSError("404")):
            with self.assertRaisesRegex(ValueError, "seed-builder"):
                ib.base_ref_for(spec)


class CoderUptimeTest(unittest.TestCase):
    """The HUD's System Uptime: since the oldest ready Coder replica's coder container started."""

    @staticmethod
    def pod(started, ready=True, name="coder"):
        return {"status": {"containerStatuses": [{"name": name, "ready": ready,
                                                  "state": {"running": {"startedAt": started}}}]}}

    def test_oldest_ready_replica(self):
        items = [self.pod("2026-10-13T12:02:07Z"), self.pod("2026-10-12T08:00:00Z"),
                 self.pod("2026-10-01T00:00:00Z", ready=False),       # not serving: does not count
                 self.pod("2026-09-01T00:00:00Z", name="sidecar")]   # another container
        with mock.patch.object(ui, "KUBE", mock.MagicMock(call=mock.MagicMock(return_value=(200, {"items": items})))):
            self.assertEqual(ui.coder_uptime(), {"since": "2026-10-12T08:00:00Z", "replicas": 2})

    def test_none_ready_or_no_answer(self):
        with mock.patch.object(ui, "KUBE", mock.MagicMock(call=mock.MagicMock(return_value=(200, {"items": []})))):
            self.assertEqual(ui.coder_uptime(), {"since": None, "replicas": 0})
        with mock.patch.object(ui, "KUBE", mock.MagicMock(call=mock.MagicMock(return_value=(403, {})))):
            self.assertIsNone(ui.coder_uptime())
        with mock.patch.object(ui, "KUBE", mock.MagicMock(call=mock.MagicMock(side_effect=OSError("down")))):
            self.assertIsNone(ui.coder_uptime())


class LockDownTest(unittest.TestCase):
    """Lock Down: the settings in the ConfigMap, the ephemeral container for a running pod, who may do what."""

    WID = "5250ca5d-f2a9-4d0d-8153-5960fbb9fb02"

    def kube(self, configmap=None, pods=(), ephemeral_exit=0):
        calls, state = [], {"cm": configmap}

        def call(method, path, body=None, patch_type=None):
            calls.append((method, path, body, patch_type))
            if path.endswith("/configmaps/coder-lockdown"):
                if method == "GET":
                    return (200, state["cm"]) if state["cm"] else (404, {})
                state["cm"] = body
                return 200, body
            if path.endswith("/configmaps") and method == "POST":
                state["cm"] = body
                return 201, body
            if "labelSelector" in path:
                return 200, {"items": list(pods)}
            if path.endswith("/ephemeralcontainers"):
                return 200, {}
            if "/log?" in path:
                return 200, "lockdown: network on, sudo on"
            if method == "GET":  # the pod, polled for the ephemeral container's end
                name = calls[-2][2]["spec"]["ephemeralContainers"][0]["name"] if calls[-2][2] else ""
                return 200, {"status": {"ephemeralContainerStatuses": [
                    {"name": n, "state": {"terminated": {"exitCode": ephemeral_exit}}}
                    for n in [c[2]["spec"]["ephemeralContainers"][0]["name"] for c in calls
                              if c[1].endswith("/ephemeralcontainers")]]}}
            return 200, {}
        return mock.MagicMock(call=call, ok=True), calls, state

    def pod(self, applied=None):
        meta = {"name": "ws-pod", "labels": {"com.coder.workspace.id": self.WID}}
        if applied:
            meta["annotations"] = {ld.ANNOTATION: applied}
        return {"metadata": meta, "spec": {"containers": [{"name": "dev", "image": "ghcr.io/user187x/coder-workspace:1.8.3"}]},
                "status": {"phase": "Running", "containerStatuses": [{"name": "dev", "state": {"running": {"startedAt": "2026-10-13T12:00:00Z"}}}]}}

    def setUp(self):
        for name, value in (("LOG", None), ("CODER_HOST", "coder.example.com"), ("APPLY_TIMEOUT", 5)):
            p = mock.patch.object(ld, name, value)
            p.start()
            self.addCleanup(p.stop)

    def test_settings_saved_and_read(self):
        kube, calls, state = self.kube()
        with mock.patch.object(ld, "KUBE", kube):
            ld.save(self.WID, "network,sudo", {"by": "admin", "at": "t", "workspace": "zzz", "owner": "admin"})
            self.assertEqual(state["cm"]["data"][self.WID], "network,sudo")   # what the pod reads at start
            self.assertEqual(ld.settings()[self.WID]["modes"], ["network", "sudo"])
            self.assertEqual(ld.settings()[self.WID]["by"], "admin")
            ld.save(self.WID, "off", {})
            self.assertEqual(ld.settings(), {})
            self.assertNotIn(self.WID, state["cm"]["data"])

    def test_running_pod_locked_live_by_an_ephemeral_container(self):
        kube, calls, _ = self.kube(pods=[self.pod()])
        with mock.patch.object(ld, "KUBE", kube):
            results = ld.reconcile(self.WID, "network,sudo")
        self.assertEqual([r[1] for r in results], [True])
        patch = next(c for c in calls if c[1].endswith("/ephemeralcontainers"))
        c = patch[2]["spec"]["ephemeralContainers"][0]
        self.assertEqual(c["targetContainerName"], "dev")                      # shares the workspace's processes
        self.assertEqual(c["image"], "ghcr.io/user187x/coder-workspace:1.8.3")
        self.assertEqual(c["command"][4:], ["network,sudo", "coder.example.com", "/proc/1/root"])
        self.assertEqual(c["securityContext"]["runAsUser"], 0)
        self.assertEqual(set(c["securityContext"]["capabilities"]["add"]), {"NET_ADMIN", "NET_RAW", "SYS_PTRACE"})
        annotate = [c for c in calls if c[0] == "PATCH" and c[3] == "application/merge-patch+json"]
        self.assertEqual(annotate[0][2]["metadata"]["annotations"][ld.ANNOTATION], "network,sudo")

    def test_in_force_already_or_failing(self):
        kube, calls, _ = self.kube(pods=[self.pod(applied="sudo")])
        with mock.patch.object(ld, "KUBE", kube):
            self.assertEqual(ld.reconcile(self.WID, "sudo")[0][2], "already in force")
        self.assertFalse(any(c[1].endswith("/ephemeralcontainers") for c in calls))
        kube, calls, _ = self.kube(pods=[self.pod()], ephemeral_exit=1)
        with mock.patch.object(ld, "KUBE", kube):
            ok, message = ld.reconcile(self.WID, "network")[0][1:]
        self.assertFalse(ok)
        self.assertIn("exit 1", message)
        self.assertFalse(any(c[3] == "application/merge-patch+json" for c in calls))   # not recorded as in force

    def test_a_new_pod_locked_at_its_start_is_unlocked_when_lifted(self):
        """A restart's new pod has no annotation yet, but its start locked it: lifting must undo that."""
        pod = self.pod()
        pod["spec"]["initContainers"] = [{"name": "lockdown"}]
        kube, calls, _ = self.kube(pods=[pod])
        with mock.patch.object(ld, "KUBE", kube):
            results = ld.reconcile(self.WID, "off")
        self.assertTrue(results[0][1])
        self.assertTrue(any(c[1].endswith("/ephemeralcontainers") for c in calls))   # applied, not "already off"

    def test_watcher_marks_never_locked_pods_without_a_container(self):
        pod = self.pod()
        pod["spec"]["initContainers"] = [{"name": "lockdown"}]
        kube, calls, _ = self.kube(pods=[pod])
        real = kube.call

        def call(method, path, body=None, patch_type=None):
            if "/log?container=lockdown" in path:
                return 200, "lockdown: modes off\nlockdown: network off (sudo is set by ...)"
            return real(method, path, body, patch_type)
        kube.call = call
        with mock.patch.object(ld, "KUBE", kube):
            ld.watch_once()
        self.assertFalse(any(c[1].endswith("/ephemeralcontainers") for c in calls))
        annotate = [c for c in calls if c[0] == "PATCH" and c[3] == "application/merge-patch+json"]
        self.assertEqual(annotate[0][2]["metadata"]["annotations"][ld.ANNOTATION], "off")

    def test_watcher_lifts_a_lock_a_pod_started_with(self):
        pod = self.pod()
        pod["spec"]["initContainers"] = [{"name": "lockdown"}]
        kube, calls, _ = self.kube(pods=[pod])
        real = kube.call

        def call(method, path, body=None, patch_type=None):
            if "/log?container=lockdown" in path:
                return 200, "lockdown: modes sudo\nlockdown: network off (sudo is set by ...)"
            return real(method, path, body, patch_type)
        kube.call = call
        with mock.patch.object(ld, "KUBE", kube):
            ld.watch_once()
        c = next(c for c in calls if c[1].endswith("/ephemeralcontainers"))[2]["spec"]["ephemeralContainers"][0]
        self.assertEqual(c["command"][4], "off")

    def test_only_admins_lock_owners_see(self):
        kube, _, _ = self.kube()
        sent = []
        h = mock.MagicMock()
        h.csrf_ok.return_value = True
        h.token.return_value = "t"
        h.send_json.side_effect = lambda status, body: sent.append((status, body))
        h.headers = {"Content-Length": "18"}
        h.rfile.read.return_value = b'{"mode":"network"}'
        ws = {"id": self.WID, "name": "zzz", "owner_name": "dev1", "owner_id": "u-dev1"}
        with mock.patch.object(ld, "KUBE", kube), mock.patch.object(ld, "WORKSPACE_INFO", lambda t, w: ws), \
                mock.patch.object(ld, "ADMIN_ROLES", {"owner"}):
            h.user.return_value = ({"id": "u-dev1", "username": "dev1", "roles": []}, None)
            ld.handle(h, "POST", "/__coder-ui/api/lockdown/" + self.WID, "")
            self.assertEqual(sent[-1][0], 403)                                    # an owner cannot lock (or unlock)
            ld.handle(h, "GET", "/__coder-ui/api/lockdown/" + self.WID, "")
            self.assertEqual(sent[-1][0], 200)                                    # but sees whether it is locked
            h.user.return_value = ({"id": "u-other", "username": "other", "roles": []}, None)
            ld.handle(h, "GET", "/__coder-ui/api/lockdown/" + self.WID, "")
            self.assertEqual(sent[-1][0], 403)                                    # someone else's: no
            h.user.return_value = ({"id": "u-admin", "username": "admin", "roles": ["owner"]}, None)
            ld.handle(h, "POST", "/__coder-ui/api/lockdown/" + self.WID, "")
            self.assertEqual(sent[-1][0], 200)
            self.assertEqual(sent[-1][1]["modes"], ["network"])
            self.assertEqual(sent[-1][1]["applied"], "next start")                # not running here
            h.rfile.read.return_value = b'{"mode":"wifi!!!!}'
            ld.handle(h, "POST", "/__coder-ui/api/lockdown/" + self.WID, "")
            self.assertEqual(sent[-1][0], 400)


class FreezeTest(unittest.TestCase):
    """Lock Down > Freeze: everything paused, the processes recorded, the home volume snapshotted (a VolumeSnapshot
    where the cluster can, else a copy by a Job on the workspace's node), and kept until deleted or replaced."""

    WID = "5250ca5d-f2a9-4d0d-8153-5960fbb9fb02"
    LOG_TEXT = ("lockdown: frozen processes:\nlockdown:   30 coder ./coder agent --no-reap\n"
                "lockdown:   212 coder /usr/lib/code-server --auth none\n"
                "lockdown: network on, sudo on, freeze on (2 processes paused)")

    def pod(self):
        return {"metadata": {"name": "ws-pod", "labels": {"com.coder.workspace.id": self.WID}},
                "spec": {"nodeName": "node-2", "tolerations": [{"key": "dedicated", "operator": "Exists"}],
                         "containers": [{"name": "dev", "image": "ghcr.io/user187x/coder-workspace:1.8.3",
                                         "volumeMounts": [{"name": "tmp", "mountPath": "/tmp"},
                                                          {"name": "home", "mountPath": "/home/coder"}]}],
                         "volumes": [{"name": "tmp", "emptyDir": {}},
                                     {"name": "home", "persistentVolumeClaim": {"claimName": "coder-x-home"}}]},
                "status": {"phase": "Running", "containerStatuses": [{"name": "dev", "state": {"running": {"startedAt": "t"}}}]}}

    def kube(self, running=True, csi_class=None):
        calls, state = [], {"cm": None, "objects": {}}
        pods = [self.pod()] if running else []

        def call(method, path, body=None, patch_type=None):
            calls.append((method, path, body, patch_type))
            if path.endswith("/configmaps/coder-lockdown"):
                if method == "GET":
                    return (200, state["cm"]) if state["cm"] else (404, {})
                state["cm"] = body
                return 200, body
            if path.endswith("/configmaps") and method == "POST":
                state["cm"] = body
                return 201, body
            if "labelSelector" in path:
                return 200, {"items": pods}
            if path.endswith("/ephemeralcontainers"):
                return 200, {}
            if "/log?" in path:
                return 200, self.LOG_TEXT
            if path.endswith("/persistentvolumeclaims/coder-x-home") or path.endswith(
                    "/persistentvolumeclaims/coder-%s-home" % self.WID):
                return 200, {"spec": {"volumeName": "pv-1", "storageClassName": "gp3",
                                      "resources": {"requests": {"storage": "20Gi"}}}}
            if path == "/api/v1/persistentvolumes/pv-1":
                return 200, {"spec": {"csi": {"driver": "ebs.csi.aws.com"}}} if csi_class else {"spec": {"local": {}}}
            if path.endswith("/volumesnapshotclasses"):
                return (200, {"items": [{"metadata": {"name": csi_class}, "driver": "ebs.csi.aws.com"}]}) \
                    if csi_class else (404, {})
            if method == "POST":
                state["objects"][path] = body
                return 201, body
            if method == "DELETE":
                state["objects"].setdefault("deleted", []).append(path)
                return 200, {}
            if method == "GET" and "/pods/" in path:
                names = [c[2]["spec"]["ephemeralContainers"][0]["name"] for c in calls if c[1].endswith("/ephemeralcontainers")]
                return 200, {"status": {"ephemeralContainerStatuses": [
                    {"name": n, "state": {"terminated": {"exitCode": 0}}} for n in names]}}
            return 404, {}
        return mock.MagicMock(call=call, ok=True), calls, state

    def setUp(self):
        for name, value in (("LOG", None), ("CODER_HOST", "coder.example.com"), ("APPLY_TIMEOUT", 5),
                            ("COPY_IMAGE", "ghcr.io/user187x/coder-workspace:1.8.3")):
            p = mock.patch.object(ld, name, value)
            p.start()
            self.addCleanup(p.stop)

    def post(self, kube, mode=None, path=""):
        sent = []
        h = mock.MagicMock()
        h.csrf_ok.return_value = True
        h.token.return_value = "t"
        h.send_json.side_effect = lambda status, body: sent.append((status, body))
        body = b'{"mode":"%s"}' % mode.encode() if mode else b"{}"
        h.headers = {"Content-Length": str(len(body))}
        h.rfile.read.return_value = body
        h.user.return_value = ({"id": "u-admin", "username": "admin", "roles": ["owner"]}, None)
        ws = {"id": self.WID, "name": "zzz", "owner_name": "dev1", "owner_id": "u-dev1"}
        with mock.patch.object(ld, "KUBE", kube), mock.patch.object(ld, "WORKSPACE_INFO", lambda t, w: ws), \
                mock.patch.object(ld, "ADMIN_ROLES", {"owner"}):
            ld.handle(h, "POST", "/__coder-ui/api/lockdown/" + self.WID + path, "")
        return sent[-1]

    def test_freeze_pauses_everything_and_copies_the_home_volume_on_its_node(self):
        kube, calls, state = self.kube()
        status, res = self.post(kube, "freeze")
        self.assertEqual(status, 200)
        self.assertEqual(res["modes"], ["network", "sudo", "freeze"])          # Complete, and paused
        c = next(c for c in calls if c[1].endswith("/ephemeralcontainers"))[2]["spec"]["ephemeralContainers"][0]
        self.assertEqual(c["command"][4], "network,sudo,freeze")
        pvc = next(b for p, b in state["objects"].items() if p.endswith("/persistentvolumeclaims"))
        job = next(b for p, b in state["objects"].items() if p.endswith("/jobs"))
        self.assertEqual(pvc["spec"]["storageClassName"], "gp3")
        self.assertEqual(pvc["spec"]["resources"]["requests"]["storage"], "20Gi")
        spec = job["spec"]["template"]["spec"]
        self.assertEqual(spec["volumes"][0]["persistentVolumeClaim"], {"claimName": "coder-x-home", "readOnly": True})
        self.assertEqual(spec["volumes"][1]["persistentVolumeClaim"]["claimName"], pvc["metadata"]["name"])
        terms = spec["affinity"]["nodeAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"]["nodeSelectorTerms"]
        self.assertEqual(terms[0]["matchExpressions"][0]["values"], ["node-2"])   # where the volume is attached
        self.assertEqual(spec["tolerations"], [{"key": "dedicated", "operator": "Exists"}])
        self.assertTrue(spec["containers"][0]["volumeMounts"][0]["readOnly"])
        with mock.patch.object(ld, "KUBE", kube):
            snap = ld.snapshot_of(self.WID)
        self.assertEqual(snap["kind"], "PersistentVolumeClaim")
        self.assertEqual(snap["processes"], ["30 coder ./coder agent --no-reap",
                                             "212 coder /usr/lib/code-server --auth none"])
        self.assertEqual(res["snapshot"]["processes"], 2)

    def test_volumesnapshot_where_the_cluster_can(self):
        kube, calls, state = self.kube(csi_class="ebs-snapshots")
        self.post(kube, "freeze")
        vs = next(b for p, b in state["objects"].items() if p.endswith("/volumesnapshots"))
        self.assertEqual(vs["spec"], {"volumeSnapshotClassName": "ebs-snapshots",
                                      "source": {"persistentVolumeClaimName": "coder-x-home"}})
        self.assertFalse(any(p.endswith("/jobs") for p in state["objects"]))

    def test_a_stopped_workspace_is_copied_as_it_was_left(self):
        kube, calls, state = self.kube(running=False)
        status, res = self.post(kube, "freeze")
        self.assertEqual(res["applied"], "next start")
        job = next(b for p, b in state["objects"].items() if p.endswith("/jobs"))
        spec = job["spec"]["template"]["spec"]
        self.assertEqual(spec["volumes"][0]["persistentVolumeClaim"]["claimName"], "coder-%s-home" % self.WID)
        self.assertEqual(spec["containers"][0]["image"], "ghcr.io/user187x/coder-workspace:1.8.3")
        self.assertEqual(spec["affinity"], {})                                # wherever the volume can be

    def test_kept_after_lifting_replaced_by_the_next_freeze_and_deleted_by_admins(self):
        kube, calls, state = self.kube()
        self.post(kube, "freeze")
        with mock.patch.object(ld, "KUBE", kube):
            first = ld.snapshot_of(self.WID)["name"]
        self.post(kube, "off")
        with mock.patch.object(ld, "KUBE", kube):
            self.assertEqual(ld.snapshot_of(self.WID)["name"], first)          # lifting keeps it
        self.post(kube, "freeze")                                            # a new freeze replaces it
        self.assertTrue(any(first in p for p in state["objects"].get("deleted", [])))
        status, res = self.post(kube, path="/snapshot/delete")
        self.assertEqual(status, 200)
        self.assertIsNone(res["snapshot"])

    def test_already_frozen_no_new_snapshot(self):
        kube, calls, state = self.kube()
        self.post(kube, "freeze")
        made = sum(1 for c in calls if c[0] == "POST" and c[1].endswith("/jobs"))
        self.post(kube, "freeze")
        self.assertEqual(sum(1 for c in calls if c[0] == "POST" and c[1].endswith("/jobs")), made)


class ClusterNetworkTest(unittest.TestCase):
    """The Settings menu's network graph: rates from the kubelets' counters, kept with the HUD's samples."""

    def setUp(self):
        import tempfile
        self.dir = tempfile.TemporaryDirectory()
        self.hud = ui.HudHistory(os.path.join(self.dir.name, "hud.sqlite"))

    def tearDown(self):
        self.dir.cleanup()

    def test_rates_from_counters_and_a_restarted_node_left_out(self):
        rounds = [{"a": (1000, 500), "b": (0, 0)}, {"a": (4000, 2000), "b": (100, 50)}, {"a": (5000, 2500), "b": (10, 5)}]
        clock = [100.0, 115.0, 130.0]
        with mock.patch.object(ui, "node_network_counters", side_effect=rounds), \
                mock.patch.object(ui.time, "time", side_effect=clock):
            self.assertEqual(self.hud.network_rates(), (None, None))           # first round: nothing to compare
            rx, tx = self.hud.network_rates()
            self.assertAlmostEqual(rx, (3000 + 100) / 15)
            self.assertAlmostEqual(tx, (1500 + 50) / 15)
            rx, tx = self.hud.network_rates()                                  # b restarted: its counters went back
            self.assertAlmostEqual(rx, 1000 / 15)
            self.assertAlmostEqual(tx, 500 / 15)

    def test_an_older_samples_table_gains_the_columns_and_recent_reads_them(self):
        import sqlite3, time as t
        path = os.path.join(self.dir.name, "old.sqlite")
        with sqlite3.connect(path) as db:                                      # as 1.38 left it
            db.execute("""CREATE TABLE samples (t INTEGER PRIMARY KEY, live INTEGER, cpu REAL, cpu_ws REAL,
                cpu_total REAL, mem REAL, mem_ws REAL, mem_total REAL, lat_p50 REAL, lat_p95 REAL, lat_max REAL,
                connections INTEGER)""")
            db.execute("INSERT INTO samples (t, live, cpu) VALUES (?, 1, 0.5)", (int(t.time()) - 30,))
        hud = ui.HudHistory(path)
        self.assertIsNone(hud.network_recent())                                # no traffic recorded yet
        with hud.connect() as db:
            db.execute("INSERT INTO samples (t, live, net_rx, net_tx) VALUES (?, 1, 2048.25, 512.5)", (int(t.time()),))
        recent = hud.network_recent()
        self.assertEqual((recent["rx"], recent["tx"], len(recent["points"])), (2048.2, 512.5, 1))

    def test_cpu_and_memory_of_the_last_15_minutes(self):
        import time as t
        now = int(t.time())
        with self.hud.connect() as db:
            for at, cpu, mem in ((now - 1200, 9.0, 9.0), (now - 30, 0.5, 2.0), (now, 1.25, 3.5)):
                db.execute("INSERT INTO samples (t, live, cpu, cpu_total, mem, mem_total) VALUES (?, 1, ?, 8, ?, 16)",
                           (at, cpu, mem))
            db.execute("INSERT INTO samples (t, live, net_rx, net_tx) VALUES (?, 1, 10, 20)", (now - 15,))  # traffic only
        recent = self.hud.usage_recent()
        self.assertEqual(recent["every"], ui.HUD_SAMPLE_EVERY)
        self.assertEqual(recent["points"], [                                   # older than 15 minutes: left out
            {"t": now - 30, "cpu": 0.5, "cpuTotal": 8, "memory": 2 * 1024 ** 3, "memoryTotal": 16 * 1024 ** 3},
            {"t": now, "cpu": 1.25, "cpuTotal": 8, "memory": int(3.5 * 1024 ** 3), "memoryTotal": 16 * 1024 ** 3}])
        self.assertIsNone(ui.HudHistory(os.path.join(self.dir.name, "empty.sqlite")).usage_recent())


class BuiltinBackgroundTest(unittest.TestCase):
    """A background that is one of the dashboard's own pictures: stored as its path (once Coder serves it as a
    picture), drawn from Coder, never uploaded."""

    def test_a_dashboard_picture_by_its_path(self):
        resp = mock.MagicMock()
        resp.__enter__.return_value.headers = {"Content-Type": "image/gif", "Content-Length": "3959943"}
        with mock.patch.object(ui.urllib.request, "urlopen", return_value=resp) as urlopen:
            ctype, data = ui.parse_background_picture("/pictures/creeper.gif")
        self.assertEqual(urlopen.call_args[0][0].full_url, ui.CODER_URL + "/pictures/creeper.gif")
        self.assertEqual(urlopen.call_args[0][0].get_method(), "HEAD")
        bg = {"type": ctype, "data": data, "sha": "x", "opacity": 0.06, "fit": "cover", "layer": "under"}
        self.assertEqual(ui.background_picture(bg), ("/pictures/creeper.gif", 3959943))
        pub = ui.background_public("dashboard", bg)
        self.assertEqual((pub["url"], pub["kind"], pub["builtin"]), ("/pictures/creeper.gif", "image", True))

    def test_only_the_dashboard_pictures_and_only_pictures(self):
        for bad in ("/etc/passwd", "/pictures/../x.gif", "https://x/a.gif", "/pictures/a.svg", "/assets/a.gif"):
            with self.assertRaises(ValueError, msg=bad):
                ui.parse_background_picture(bad)
        resp = mock.MagicMock()
        resp.__enter__.return_value.headers = {"Content-Type": "text/html", "Content-Length": "100"}
        with mock.patch.object(ui.urllib.request, "urlopen", return_value=resp):
            with self.assertRaises(ValueError):
                ui.parse_background_picture("/pictures/missing.gif")   # Coder's index page: not a picture


class ImageStoreTest(unittest.TestCase):
    """The image store: its baked pictures (with their previews) listed for every chooser and served publicly,
    streamed with a version for an ETag; backgrounds may reference them."""

    def setUp(self):
        import tempfile
        self.dir = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.dir, "pictures"))
        os.makedirs(os.path.join(self.dir, "thumbs"))
        for name, data in (("synthsun.gif", b"GIF89a" + b"x" * 64), ("notes.txt", b"no"), ("still.PNG", b"\x89PNG")):
            with open(os.path.join(self.dir, "pictures", name), "wb") as f:
                f.write(data)
        with open(os.path.join(self.dir, "thumbs", "synthsun.gif.webp"), "wb") as f:
            f.write(b"RIFF....WEBP")
        self.store = ui.ImageStore(self.dir)
        self.patch = mock.patch.object(ui, "STORE_IMAGES", self.store)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        import shutil
        shutil.rmtree(self.dir)

    def test_listed_with_previews_pictures_only(self):
        listed = {i["name"]: i for i in self.store.listing()}
        self.assertEqual(sorted(listed), ["still.PNG", "synthsun.gif"])
        sun = listed["synthsun.gif"]
        self.assertEqual((sun["type"], sun["bytes"], sun["source"]), ("image/gif", 70, "store"))
        self.assertTrue(sun["url"].startswith("/__coder-ui/image-store/synthsun.gif?v="))
        self.assertTrue(sun["thumb"].startswith("/__coder-ui/image-store/thumbs/synthsun.gif?v="))
        self.assertIsNone(listed["still.PNG"]["thumb"])
        # every chooser's list: the store's, then the gallery's and the announcement's uploads
        with mock.patch.object(ui, "GALLERY", mock.Mock(listing=lambda: [{"name": "cube.png", "url": "/g"}])), \
                mock.patch.object(ui, "BANNER_PICTURES", mock.Mock(pool=True, listing=lambda: [
                    {"name": "flag", "source": "uploaded", "url": "/__coder-ui/banner-picture/0123456789abcdef"}])):
            sources = [(i["name"], i["source"]) for i in ui.image_store_listing()]
        self.assertEqual(sources[-2:], [("cube.png", "gallery"), ("flag", "uploaded")])

    def test_a_background_may_reference_a_store_picture(self):
        ctype, data = ui.parse_background_picture("/__coder-ui/image-store/synthsun.gif")
        bg = {"type": ctype, "data": data, "sha": "x", "opacity": 0.06, "fit": "cover", "layer": "under"}
        self.assertEqual(ui.background_picture(bg), ("/__coder-ui/image-store/synthsun.gif", 70))
        self.assertEqual(ui.background_public("login", bg)["url"], "/__coder-ui/image-store/synthsun.gif")
        for bad in ("/__coder-ui/image-store/missing.gif", "/__coder-ui/image-store/../ui-service.py",
                    "/__coder-ui/image-store/notes.txt"):
            with self.assertRaises(ValueError, msg=bad):
                ui.parse_background_picture(bad)

    def test_streamed_with_its_version_for_an_etag(self):
        import io

        class H(ui.Handler):
            def __init__(self, inm=None):
                self.headers = {"If-None-Match": inm} if inm else {}
                self.command = "GET"
                self.wfile = io.BytesIO()
                self.sent = []
                self.close_connection = False

            def send_response(self, code, message=None):
                self.sent.append(("status", code))

            def send_header(self, k, v):
                self.sent.append((k, v))

            def end_headers(self):
                pass

        hit = self.store.items()["synthsun.gif"]
        h = H()
        h.send_file(hit["path"], hit["type"], hit["tag"], versioned=True)
        head = dict(h.sent)
        self.assertEqual((head["status"], head["Content-Type"], head["Content-Length"]), (200, "image/gif", "70"))
        self.assertEqual(head["Cache-Control"], "public, max-age=31536000, immutable")
        self.assertEqual(h.wfile.getvalue(), b"GIF89a" + b"x" * 64)
        again = H(inm=head["ETag"])
        again.send_file(hit["path"], hit["type"], hit["tag"], versioned=False)
        self.assertEqual(dict(again.sent)["status"], 304)
        self.assertEqual(again.wfile.getvalue(), b"")


class QuickMenuTest(unittest.TestCase):
    """Admin Settings > Quick Menu: the wheel's wedges, checked (IDs, each once, 3 to 12) and kept; none: the default."""

    def test_checked(self):
        self.assertEqual(ui.clean_quick_menu({"items": ["workspaces", "accounts", "oauth2-applications"]}),
                         {"items": ["workspaces", "accounts", "oauth2-applications"]})
        for bad, why in (({"items": ["a", "a", "b"]}, "once"), ({"items": ["a", "b"]}, "3 to 12"),
                         ({"items": ["x%d" % i for i in range(13)]}, "3 to 12"), ({"items": "workspaces"}, "IDs"),
                         ({"items": ["Workspaces", "b", "c"]}, "IDs"), ({"items": ["../x", "b", "c"]}, "IDs")):
            with self.assertRaises(ValueError) as e:
                ui.clean_quick_menu(bad)
            self.assertIn(why, str(e.exception))

    def test_none_saved_is_the_default(self):
        with mock.patch.object(ui, "STORE", mock.Mock(get_setting=lambda key, default=None: None)):
            self.assertEqual(ui.quick_menu_settings()["items"], None)
        saved = {"items": ["hud", "images", "security"], "updatedBy": "admin", "updatedAt": "t"}
        with mock.patch.object(ui, "STORE", mock.Mock(get_setting=lambda key, default=None: saved)):
            got = ui.quick_menu_settings()
        self.assertEqual((got["items"], got["wedges"]), (["hud", "images", "security"], [3, 12]))


class BannerPictureTest(unittest.TestCase):
    """Insert picture: what is accepted, and how pictures are kept (named by their content, the newest kept)."""

    @staticmethod
    def data_url(ctype, data):
        import base64
        return "data:%s;base64,%s" % (ctype, base64.b64encode(data).decode())

    def test_an_animated_gif_is_kept_as_it_is(self):
        gif = b"GIF89a" + b"\x00" * 64 + b"NETSCAPE2.0" + b"\x00" * 64   # an animated GIF's looping block
        self.assertEqual(ui.parse_banner_picture(self.data_url("image/gif", gif)), ("image/gif", gif))

    def test_refused(self):
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
        for url, why in ((self.data_url("image/gif", png), "not a valid GIF"),            # the content is a PNG
                         (self.data_url("image/svg+xml", b"<svg/>"), "Upload a PNG"),     # SVG is not offered
                         ("not a data url", "Upload a PNG")):
            with self.assertRaises(ValueError) as e:
                ui.parse_banner_picture(url)
            self.assertIn(why, str(e.exception))
        with mock.patch.object(ui, "BANNER_PICTURE_MAX_BYTES", 16), self.assertRaises(ValueError):
            ui.parse_banner_picture(self.data_url("image/png", png))

    def test_named_by_content_and_bounded(self):
        queries = []

        class Pool:
            def query(self, sql, params=()):
                queries.append((" ".join(sql.split()), list(params)))
                return [["image/gif", b"GIF89a"]] if sql.startswith("SELECT content_type") else []

        store = ui.BannerPictureStore(mock.Mock(pool=Pool()))
        pid = store.put("image/gif", b"GIF89a-frames", "flag", "admin")
        self.assertEqual(pid, __import__("hashlib").sha256(b"GIF89a-frames").hexdigest()[:16])
        self.assertTrue(any(q.startswith("DELETE FROM coder_ui_updates.banner_picture") and p == [ui.BANNER_PICTURE_KEEP]
                            for q, p in queries))
        self.assertEqual(store.get(pid), ("image/gif", b"GIF89a"))
        self.assertIsNone(store.get("../../etc/passwd"))                         # only ids are looked up


class AnnouncementRecordsTest(unittest.TestCase):
    """Acknowledgements' Clear All: every announcement's record and receipts go; the live one starts again."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()
        self.store = ui.Store(os.path.join(self.tmp, "ui.sqlite"))
        self.user = {"id": "u1", "username": "dev", "name": "Dev", "email": "dev@example.com"}

    def banner(self, bid, message):
        return {"id": bid, "level": "info", "title": "", "message": message, "linkText": "", "linkUrl": ""}

    def test_clear_all(self):
        old, live = self.banner("aaaaaaaaaaaa", "Old"), self.banner("bbbbbbbbbbbb", "Live")
        self.store.record(old["id"], self.user, True, old)
        self.store.record(live["id"], self.user, False, live)
        self.assertEqual(len(self.store.report(live, [])), 2)
        self.assertEqual(self.store.clear_events(), 2)
        events = self.store.report(live, [])
        self.assertEqual([e["id"] for e in events], [live["id"]])   # the live one, back with nobody
        self.assertEqual(events[0]["viewed"], 0)
        self.assertEqual(self.store.receipt_times(live["id"], "u1"), (None, None))


class PeerChatTest(unittest.TestCase):
    """Peer-to-peer chat: who may write to whom, invisibility, retention, purge, and who talks to whom."""

    ADMIN = {"id": "00000000-0000-0000-0000-00000000000a", "username": "boss", "name": "Boss", "admin": True}
    ANN = {"id": "00000000-0000-0000-0000-000000000001", "username": "ann", "name": "Ann", "admin": False}
    BOB = {"id": "00000000-0000-0000-0000-000000000002", "username": "bob", "name": "Bob", "admin": False}

    def setUp(self):
        import tempfile
        self.path = os.path.join(tempfile.mkdtemp(), "ui.sqlite")
        self.chat = ui.ChatStore(self.path)
        self.admins = {self.ADMIN["id"]}

    def may(self, user, peer):
        return self.chat.may_write(user, False, peer["id"], self.admins, self.admins)

    def test_peers_off_then_on(self):
        self.assertIsNone(self.may(self.ANN, self.ADMIN))  # their line to support, always
        self.assertEqual(self.may(self.ANN, self.BOB), "You can start a conversation with an admin only.")
        self.chat.put_settings({"peers": True}, "boss")
        self.assertIsNone(self.may(self.ANN, self.BOB))
        self.chat.send(self.ANN, False, self.BOB, "hi")
        # Turned off again: the developers' conversation can't go on, but an admin's can.
        self.chat.put_settings({"peers": False}, "boss")
        self.assertEqual(self.may(self.BOB, self.ANN), "Chat between developers is turned off by your admins.")
        self.chat.send(self.ADMIN, True, self.BOB, "need anything?")
        self.assertIsNone(self.may(self.BOB, self.ADMIN))
        # Settings survive a restart.
        self.assertFalse(ui.ChatStore(self.path).settings["peers"])
        self.assertEqual(ui.ChatStore(self.path).settings["updatedBy"], "boss")

    def test_invisible(self):
        self.chat.put_settings({"peers": True}, "boss")
        self.chat.set_invisible(self.BOB["id"], True)
        self.assertEqual(self.may(self.ANN, self.BOB), "That person isn't available for chat.")
        self.assertIn(self.BOB["id"], ui.ChatStore(self.path).invisible)
        # Someone they already talk with may still write.
        self.chat.set_invisible(self.BOB["id"], False)
        self.chat.send(self.ANN, False, self.BOB, "hi")
        self.chat.set_invisible(self.BOB["id"], True)
        self.assertIsNone(self.may(self.ANN, self.BOB))

    def test_people_list_hides_the_invisible_from_all_but_admins(self):
        directory = {p["id"]: {**{k: p[k] for k in ("id", "username", "name", "admin")}, "lastSeen": None}
                     for p in (self.ADMIN, self.ANN, self.BOB)}
        self.chat.touch(self.ANN["id"])
        self.chat.touch(self.BOB["id"])
        self.chat.set_invisible(self.BOB["id"], True)
        with mock.patch.object(ui, "CHAT", self.chat), mock.patch.object(ui, "chat_people", lambda: directory):
            seen_by_ann = [p["username"] for p in ui.chat_people_for(self.ANN, False)]
            seen_by_admin = {p["username"]: p.get("invisible") for p in ui.chat_people_for(self.ADMIN, True)}
        self.assertEqual(seen_by_ann, [])  # Bob is invisible; the admin was never seen
        self.assertEqual(seen_by_admin, {"ann": False, "bob": True})

    def test_pairs_a_page_at_a_time_newest_first(self):
        self.chat.put_settings({"peers": True}, "boss")
        devs = [{"id": "dev-%02d" % i, "username": "dev%02d" % i, "name": ""} for i in range(30)]
        for dev in devs:  # each writes to Bob once: 30 pairs, dev29's the newest
            self.chat.send(dev, False, self.BOB, "hi")
        first = self.chat.pairs()
        self.assertEqual((len(first["pairs"]), first["total"]), (25, 30))
        self.assertEqual({first["pairs"][0][s]["username"] for s in ("a", "b")}, {"bob", "dev29"})
        rest = self.chat.pairs(25, 25)
        self.assertEqual((len(rest["pairs"]), rest["total"]), (5, 30))
        names = {p[s]["username"] for p in rest["pairs"] for s in ("a", "b")} - {"bob"}
        self.assertEqual(names, {"dev00", "dev01", "dev02", "dev03", "dev04"})  # the oldest last

    def test_retention_purge_and_pairs(self):
        self.chat.put_settings({"peers": True}, "boss")
        self.chat.send(self.ANN, False, self.BOB, "a")
        self.chat.send(self.BOB, False, self.ANN, "b")
        self.chat.send(self.ADMIN, True, self.ANN, "c")
        page = self.chat.pairs()
        pairs = page["pairs"]
        self.assertEqual((len(pairs), page["total"]), (1, 1))  # developers only: the admin's conversation is not a pair
        self.assertEqual({pairs[0]["a"]["username"], pairs[0]["b"]["username"]}, {"ann", "bob"})
        self.assertEqual(pairs[0]["messages"], 2)
        self.assertNotIn("text", str(pairs))
        stats = self.chat.stats()
        self.assertEqual((stats["messages"], stats["peerMessages"]), (3, 2))
        # A message older than the retention goes when it is shortened.
        with self.chat.connect() as db:
            db.execute("UPDATE chat_messages SET sent_at = '2020-01-01T00:00:00Z' WHERE text = 'a'")
        self.chat.put_settings({"keepDays": 7}, "boss")
        self.assertEqual(self.chat.stats()["messages"], 2)
        version = self.chat.settings["purgeVersion"]
        self.assertEqual(self.chat.purge(), 2)
        self.assertEqual(self.chat.stats()["messages"], 0)
        self.assertEqual(self.chat.settings["purgeVersion"], version + 1)


class GroupChatTest(unittest.TestCase):
    """An admin's group chat (Admin > Accounts, Chat): nobody sees it before its first message; that message, the
    admin's, takes each member's focus until they click it, once; members only."""

    ADMIN = {"id": "00000000-0000-0000-0000-00000000000a", "username": "boss", "name": "Boss", "admin": True}
    ANN = {"id": "00000000-0000-0000-0000-000000000001", "username": "ann", "name": "Ann", "admin": False}
    BOB = {"id": "00000000-0000-0000-0000-000000000002", "username": "bob", "name": "Bob", "admin": False}
    EVE = {"id": "00000000-0000-0000-0000-000000000003", "username": "eve", "name": "Eve", "admin": False}

    def setUp(self):
        import tempfile
        self.path = os.path.join(tempfile.mkdtemp(), "ui.sqlite")
        self.chat = ui.ChatStore(self.path)

    def test_first_message_takes_focus_once(self):
        peer = self.chat.create_group(self.ADMIN, [self.ANN, self.BOB])
        gid = peer["id"]
        self.assertTrue(ui.CHAT_GROUP_RE.match(gid))
        self.assertEqual(self.chat.conversations(self.ANN["id"]), [])  # nothing before its first message
        self.assertEqual(self.chat.focus_pending(self.ANN["id"]), [])
        first = self.chat.send(self.ADMIN, True, peer, "All hands: maintenance at 5")
        for member in (self.ANN, self.BOB):
            focus = self.chat.focus_pending(member["id"])
            self.assertEqual([(f["group"]["id"], f["message"]["id"]) for f in focus], [(gid, first["id"])])
        self.assertEqual(self.chat.focus_pending(self.ADMIN["id"]), [])  # never the admin's own
        self.chat.ack_focus(self.ANN["id"], gid)
        self.assertEqual(self.chat.focus_pending(self.ANN["id"]), [])
        self.assertEqual(len(ui.ChatStore(self.path).focus_pending(self.BOB["id"])), 1)  # kept across a restart
        # the answers and later messages take nobody's focus
        self.chat.send(self.BOB, False, peer, "ok")
        self.chat.send(self.ADMIN, True, peer, "thanks")
        self.chat.ack_focus(self.BOB["id"], gid)
        self.assertEqual(self.chat.focus_pending(self.BOB["id"]), [])
        convo = self.chat.conversations(self.ANN["id"])
        self.assertEqual((convo[0]["peer"]["id"], convo[0]["unread"]), (gid, 3))
        self.assertEqual([m["text"] for m in self.chat.since(self.ANN["id"], 0)],
                         ["All hands: maintenance at 5", "ok", "thanks"])

    def test_forced_focus_and_force_response(self):
        """An admin's Forced Focus: the message in front of everything for the user until they acknowledge it (kept
        across a restart, waking the polls); Force Response forces the newest admin message again; in a group chat
        every member but the admin."""
        before = self.chat.focus_ver
        m = self.chat.send(self.ADMIN, True, self.ANN, "Please save your work: reboot at 5")
        self.chat.force(self.ADMIN, m["id"], [self.ANN["id"]])
        self.assertGreater(self.chat.focus_ver, before)
        focus = self.chat.focus_pending(self.ANN["id"])
        self.assertEqual([(f["group"]["id"], f["message"]["id"], f.get("forced")) for f in focus],
                         [(self.ADMIN["id"], m["id"], True)])
        self.assertEqual(self.chat.focus_pending(self.BOB["id"]), [])
        self.assertEqual(len(ui.ChatStore(self.path).focus_pending(self.ANN["id"])), 1)  # kept across a restart
        self.assertFalse(self.chat.ack_forced(self.BOB["id"], m["id"]))  # not hers to acknowledge
        self.assertTrue(self.chat.ack_forced(self.ANN["id"], m["id"]))
        self.assertFalse(self.chat.ack_forced(self.ANN["id"], m["id"]))  # once
        self.assertEqual(ui.ChatStore(self.path).focus_pending(self.ANN["id"]), [])
        # Force Response: the newest message an admin wrote to her, forced again (her answer is not it)
        self.chat.send(self.ANN, False, self.ADMIN, "which reboot?")
        self.assertEqual(self.chat.newest_admin_message(self.ANN["id"])["id"], m["id"])
        self.assertIsNone(self.chat.newest_admin_message(self.EVE["id"]))
        self.chat.force(self.ADMIN, m["id"], [self.ANN["id"]])
        self.assertEqual(len(self.chat.focus_pending(self.ANN["id"])), 1)
        # a group chat: its members, the conversation being the group
        peer = self.chat.create_group(self.ADMIN, [self.ANN, self.BOB], focus=False)
        g = self.chat.send(self.ADMIN, True, peer, "all of you: answer")
        self.chat.force(self.ADMIN, g["id"], [self.ANN["id"], self.BOB["id"]])
        self.assertEqual([f["group"]["id"] for f in self.chat.focus_pending(self.BOB["id"])], [peer["id"]])
        self.assertEqual(self.chat.newest_admin_message(peer["id"])["id"], g["id"])
        # a deleted conversation asks nothing any more
        self.chat.delete(self.ADMIN["id"], self.ANN["id"])
        self.assertEqual([f["group"]["id"] for f in self.chat.focus_pending(self.ANN["id"])], [peer["id"]])

    def test_members_only(self):
        peer = self.chat.create_group(self.ADMIN, [self.ANN])
        self.chat.send(self.ADMIN, True, peer, "hello")
        self.assertEqual(self.chat.may_write(self.EVE, False, peer["id"]), "You are not in that chat.")
        self.assertIsNone(self.chat.may_write(self.ANN, False, peer["id"]))
        self.assertEqual(self.chat.history(self.EVE["id"], peer["id"]), ([], 0))
        self.assertEqual(self.chat.since(self.EVE["id"], 0), [])
        self.assertEqual(self.chat.focus_pending(self.EVE["id"]), [])
        with self.assertRaises(PermissionError):
            self.chat.delete(self.ANN["id"], peer["id"])  # only its admin
        self.chat.delete(self.ADMIN["id"], peer["id"])
        self.assertEqual(self.chat.conversations(self.ANN["id"]), [])


class DisasterRecoveryTest(unittest.TestCase):
    """Persistence > Disaster recovery (dr.py): targets checked, secrets never shown, dumps streamed into S3 with the
    newest kept, a failed dump never left as a backup."""

    def setUp(self):
        import tempfile
        self.dir = tempfile.TemporaryDirectory()
        self.dr = load("dr", "dr.py")
        self.dr.LOG = lambda **kw: None
        self.dr.SETTINGS_FILE = os.path.join(self.dir.name, "dr.json")

    def tearDown(self):
        self.dir.cleanup()

    def test_targets_are_checked_and_secrets_kept_out_of_sight(self):
        for bad in ({"kind": "ftp", "name": "x"},
                    {"kind": "s3", "name": "x", "endpoint": "s3.example", "bucket": "b-1"},
                    {"kind": "s3", "name": "x", "endpoint": "https://s3.example", "bucket": "B"},
                    {"kind": "postgres", "name": "x", "url": "mysql://h/db"},
                    {"kind": "postgres", "name": "x", "url": "postgresql://h/db", "everyHours": 1000}):
            with self.assertRaises(ValueError, msg=bad):
                self.dr.clean_target(bad)
        s3 = self.dr.clean_target({"kind": "s3", "name": "Offsite", "endpoint": "https://s3.example/",
                                   "bucket": "dr-bucket", "prefix": "coder", "accessKey": "AK", "secretKey": "SK"})
        self.assertEqual((s3["prefix"], s3["keep"], s3["everyHours"]), ("coder/", 14, 24))
        again = self.dr.clean_target({**s3, "secretKey": ""}, old=s3)  # left empty: kept
        self.assertEqual((again["id"], again["secretKey"]), (s3["id"], "SK"))
        self.assertNotIn("secretKey", self.dr.public(s3))
        pg = self.dr.clean_target({"kind": "postgres", "name": "Standby",
                                   "url": "postgresql://dr:pw@db.example:5432/coder?sslmode=require"})
        shown = self.dr.public(pg)
        self.assertNotIn("url", shown)
        self.assertEqual((shown["where"], shown["hasSecret"]), ("db.example:5432/coder", True))

    def test_a_dump_into_s3_keeps_the_newest(self):
        target = self.dr.clean_target({"kind": "s3", "name": "Offsite", "endpoint": "https://s3.example",
                                       "bucket": "dr-bucket", "prefix": "c/", "accessKey": "AK", "secretKey": "SK",
                                       "keep": 2})
        s3 = mock.Mock()
        got = {}

        def put_stream(bucket, key, read):
            data = b""
            while True:
                chunk = read(7)
                if not chunk:
                    break
                data += chunk
            got[key] = data
            return len(data)

        s3.put_stream.side_effect = put_stream
        s3.list.return_value = ([{"key": "c/coder-db-20260101T000000Z.dump"}, {"key": "c/coder-db-20260102T000000Z.dump"},
                                 {"key": "c/coder-db-20260103T000000Z.dump"}], [])
        with mock.patch.object(self.dr, "_s3", return_value=s3), \
                mock.patch.object(self.dr, "dump", side_effect=lambda write: [write(b"PGDMP"), write(b"-rest")]):
            out = self.dr.to_s3(target)
        self.assertEqual(list(got.values()), [b"PGDMP-rest"])
        self.assertEqual(out["bytes"], 10)
        s3.delete_many.assert_called_with("dr-bucket", ["c/coder-db-20260101T000000Z.dump"])

        def broken(write):
            write(b"PGDMP")
            raise RuntimeError("pg_dump failed")

        with mock.patch.object(self.dr, "_s3", return_value=s3), mock.patch.object(self.dr, "dump", side_effect=broken):
            with self.assertRaises(RuntimeError):
                self.dr.to_s3(target)
        s3.delete.assert_called()  # the partial dump removed


class AgentNetHistoryTest(unittest.TestCase):
    """The workspace page's network pill: the last 5 minutes of each agent's traffic, kept whether or not anyone looks."""

    def test_a_reading_after_another_is_a_rate(self):
        h = ui.AgentNetHistory()
        t0 = 1_000_000_000_000
        h.add([("a1", "1000 500", t0)], now_ms=t0)
        h.add([("a1", "1000 500", t0)], now_ms=t0 + 1000)       # the same reading again: nothing new
        h.add([("a1", "5000 2500", t0 + 2000)], now_ms=t0 + 2000)
        h.add([("a1", "100 50", t0 + 4000)], now_ms=t0 + 4000)  # counters restarted: no traffic, not negative
        r = h.recent(["a1", "a2"])
        self.assertEqual(r["a1"]["samples"], [
            {"from": t0, "t": t0 + 2000, "rx": 2000.0, "tx": 1000.0},
            {"from": t0 + 2000, "t": t0 + 4000, "rx": 0, "tx": 0},
        ])
        self.assertEqual(r["a1"]["last"], {"at": t0 + 4000, "rx": 100, "tx": 50})
        self.assertEqual(r["a2"], {"samples": [], "last": None})

    def test_keeps_five_minutes_and_no_rate_across_a_long_gap(self):
        h = ui.AgentNetHistory()
        t0 = 1_000_000_000_000
        for i in range(200):                                     # every 2 s for 400 s
            h.add([("a1", "%d 0" % (i * 1000), t0 + i * 2000)], now_ms=t0 + i * 2000)
        samples = h.recent(["a1"])["a1"]["samples"]
        self.assertTrue(all(s["t"] >= t0 + 398_000 - 300_000 for s in samples))
        self.assertEqual(len(samples), 151)                  # 98 s to 398 s, both ends kept
        h.add([("a1", "999999 0", t0 + 398_000 + 600_000)], now_ms=t0 + 398_000 + 600_000)  # 10 minutes later
        self.assertEqual(h.recent(["a1"])["a1"]["samples"], [])
        h.add([("a1", "bad", t0)], now_ms=t0)                    # not a reading: ignored


class ChatPresenceAndFilesTest(unittest.TestCase):
    """Who is online, idle or gone (each tab's own report, pushed to every poll), and files attached to messages."""

    ANN = PeerChatTest.ANN
    BOB = PeerChatTest.BOB
    ADMIN = PeerChatTest.ADMIN

    def setUp(self):
        import tempfile
        self.dir = tempfile.mkdtemp()
        self.chat = ui.ChatStore(os.path.join(self.dir, "ui.sqlite"))

    def test_online_idle_and_gone_with_its_tabs(self):
        ann = self.ANN["id"]
        self.assertEqual(self.chat.presence(ann)["status"], "offline")
        v0 = self.chat.presence_ver
        self.chat.tab(ann, "tab-one-1234")                       # a tab's poll: online, active
        self.assertEqual(self.chat.presence(ann)["status"], "active")
        self.assertGreater(self.chat.presence_ver, v0)
        v1 = self.chat.presence_ver
        self.chat.tab(ann, "tab-one-1234")                       # polling again changes nothing
        self.assertEqual(self.chat.presence_ver, v1)
        self.chat.tab(ann, "tab-two-1234")                       # a second tab
        self.chat.tab(ann, "tab-one-1234", idle=True)            # one tab idle: still active
        self.assertEqual(self.chat.presence(ann)["status"], "active")
        self.chat.tab(ann, "tab-two-1234", idle=True)            # every tab idle: idle, everyone hears of it
        self.assertEqual(self.chat.presence(ann)["status"], "idle")
        self.assertGreater(self.chat.presence_ver, v1)
        self.assertEqual(self.chat.presence(ann)["tabs"], 2)
        self.chat.tab(ann, "tab-two-1234", idle=False)           # back
        self.assertEqual(self.chat.presence(ann)["status"], "active")
        self.chat.tab(ann, "tab-one-1234", leave=True)
        self.chat.tab(ann, "tab-two-1234", leave=True)           # the last tab closed: gone at once
        self.assertEqual(self.chat.presence(ann)["status"], "offline")
        self.assertFalse(self.chat.is_online(ann))
        # a tab that stopped polling goes with the sweep
        self.chat.tab(ann, "tab-three-1234")
        self.chat.tabs[ann]["tab-three-1234"]["seen"] -= ui.CHAT_ONLINE_SECONDS + 1
        self.assertEqual(self.chat.presence(ann)["status"], "offline")

    def test_a_late_poll_from_a_closed_tab_does_not_bring_it_back(self):
        ann = self.ANN["id"]
        self.chat.tab(ann, "old-page-1234")                      # the page before a reload
        self.chat.tab(ann, "new-page-1234")                      # the reloaded page
        self.chat.tab(ann, "old-page-1234", leave=True)          # the old page's goodbye
        self.chat.tab(ann, "old-page-1234")                      # its last poll, sent as it closed, arrives after
        self.assertEqual(self.chat.presence(ann)["tabs"], 1)
        self.chat.tab(ann, "new-page-1234", idle=True)           # so the open page alone says idle
        self.assertEqual(self.chat.presence(ann)["status"], "idle")

    def test_a_poll_wakes_when_someone_comes_or_goes(self):
        import threading as th
        import time
        known = self.chat.presence_ver
        woke = []
        t = th.Thread(target=lambda: woke.append(self.chat.wait(self.BOB["id"], 10 ** 9, [], 10 ** 15,
                                                                10 ** 15, known)))
        t.start()
        time.sleep(0.2)
        self.chat.tab(self.ANN["id"], "ann-tab-12345")
        t.join(3)
        self.assertFalse(t.is_alive())

    def test_people_say_who_is_idle(self):
        directory = {p["id"]: {**{k: p[k] for k in ("id", "username", "name", "admin")}, "lastSeen": None}
                     for p in (self.ADMIN, self.ANN, self.BOB)}
        self.chat.put_settings({"peers": True}, "boss")
        self.chat.tab(self.BOB["id"], "bob-tab-12345", idle=True)
        with mock.patch.object(ui, "CHAT", self.chat), mock.patch.object(ui, "chat_people", lambda: directory):
            people = {p["username"]: p for p in ui.chat_people_for(self.ANN, False)}
        self.assertTrue(people["bob"]["online"])
        self.assertTrue(people["bob"]["idle"])

    def test_a_file_goes_with_its_message_to_those_two_only(self):
        import io
        self.chat.put_settings({"peers": True}, "boss")
        up = self.chat.put_upload(self.ANN["id"], self.BOB["id"], "notes.txt", "text/plain", 5, io.BytesIO(b"hello"))
        self.assertIsNone(self.chat.take_upload(self.BOB["id"], up["upload"], self.ANN["id"]))  # not theirs
        taken = self.chat.take_upload(self.ANN["id"], up["upload"], self.BOB["id"])
        message = self.chat.send(self.ANN, False, self.BOB, "", attachment=taken)
        self.assertEqual(message["file"]["name"], "notes.txt")
        self.assertEqual(message["file"]["size"], 5)
        self.assertIsNone(self.chat.take_upload(self.ANN["id"], up["upload"], self.BOB["id"]))  # sent once
        path, name, ctype, size = self.chat.attachment(self.BOB["id"], message["id"])
        self.assertEqual(open(path, "rb").read(), b"hello")
        self.assertIsNone(self.chat.attachment(self.ADMIN["id"], message["id"]))  # not in the conversation
        self.assertEqual(self.chat.stats()["attachments"], 1)
        # deleting the conversation removes the file
        self.chat.delete(self.ANN["id"], self.BOB["id"])
        self.assertFalse(os.path.exists(path))

    def test_retention_and_purge_remove_files(self):
        import io
        sent = []
        for text in ("old", "new"):
            up = self.chat.put_upload(self.ADMIN["id"], self.ANN["id"], text + ".bin", "application/octet-stream",
                                      3, io.BytesIO(b"abc"))
            m = self.chat.send(self.ADMIN, True, self.ANN, text,
                               attachment=self.chat.take_upload(self.ADMIN["id"], up["upload"], self.ANN["id"]))
            sent.append(self.chat._file_path(m["id"]))
        with self.chat.connect() as db:
            db.execute("UPDATE chat_messages SET sent_at = '2020-01-01T00:00:00Z' WHERE text = 'old'")
        self.chat.prune()
        self.assertEqual([os.path.exists(p) for p in sent], [False, True])
        self.chat.purge()
        self.assertFalse(os.path.exists(sent[1]))

    def test_an_upload_cut_short_leaves_nothing(self):
        import io
        with self.assertRaises(ValueError):
            self.chat.put_upload(self.ANN["id"], self.BOB["id"], "x", "text/plain", 10, io.BytesIO(b"short"))
        self.assertEqual([n for n in os.listdir(self.chat.files_dir) if n.startswith("pending-")], [])

    def test_file_names_kept_plain(self):
        self.assertEqual(ui.chat_file_name("../../etc/passwd"), "passwd")
        self.assertEqual(ui.chat_file_name('C:\\Users\\a\\report "final".pdf'), "report final.pdf")
        self.assertEqual(ui.chat_file_name(""), "file")

    def test_online_users_report(self):
        directory = {p["id"]: {**{k: p[k] for k in ("id", "username", "name", "admin")},
                               "lastSeen": ui.now()} for p in (self.ADMIN, self.ANN, self.BOB)}
        self.chat.tab(self.ANN["id"], "ann-tab-12345")
        self.chat.tab(self.BOB["id"], "bob-tab-12345", idle=True)
        running = {"workspaces": [{"id": "w1", "name": "dev", "owner_id": self.ANN["id"], "owner_name": "ann"}]}
        with mock.patch.object(ui, "CHAT", self.chat), mock.patch.object(ui, "chat_people", lambda: directory), \
                mock.patch.object(ui, "_coder_get", lambda token, path, timeout=10: (200, running)):
            report = ui.presence_report("token")
        self.assertEqual([(p["username"], p["status"]) for p in report["online"]], [("ann", "active"), ("bob", "idle")])
        self.assertEqual(report["online"][0]["workspaces"][0]["name"], "dev")
        self.assertEqual([p["username"] for p in report["recent"]], ["boss"])  # seen by Coder, no tab open


class LoginPageSettingsTest(unittest.TestCase):
    def test_defaults_are_the_traced_outline(self):
        self.assertEqual(ui.LOGIN_PAGE_DEFAULT["effect"], "wireframe")
        self.assertEqual(ui.LOGIN_PAGE_DEFAULT["title"], "")

    def test_clean(self):
        v = ui.clean_login_page({"title": " Mission Control ", "effect": "neon", "color": "#FF00AA", "speed": 2,
                                 "message": "Authorised users only.", "signedOutMessage": "Bye."})
        self.assertEqual(v, {"title": "Mission Control", "effect": "neon", "color": "#ff00aa", "speed": 2.0,
                             "message": "Authorised users only.", "signedOutMessage": "Bye."})
        for bad in ({"effect": "blink"}, {"color": "red"}, {"speed": 0}, {"speed": True}, {"title": "a\nb"},
                    {"title": "x" * 41}, {"message": "m" * 301}):
            with self.assertRaises(ValueError):
                ui.clean_login_page(dict({"effect": "plain", "color": "#000000", "speed": 3}, **bad))


class NotApplicableFindingsTest(unittest.TestCase):
    """The scan sets apart what cannot apply to a container (and only that): the kernel's header package, and Ubuntu's
    snap-package advisories, two years old or more, matched against Mozilla's own Firefox."""

    OS = {"Class": "os-pkgs", "Target": "img (ubuntu 26.04)"}

    def v(self, pkg, installed="1", fixed="", published="2022-02-19T18:15:08Z", sev="HIGH"):
        return {"PkgName": pkg, "InstalledVersion": installed, "FixedVersion": fixed, "PublishedDate": published,
                "Severity": sev, "VulnerabilityID": "CVE-2022-25235"}

    def test_kernel_headers(self):
        self.assertEqual(ib.not_applicable(self.v("linux-libc-dev", "7.0.0-38.38"), self.OS), "kernel-headers")
        # not the kernel package's other names, and not outside the OS packages
        self.assertIsNone(ib.not_applicable(self.v("linux-image-generic"), self.OS))
        self.assertIsNone(ib.not_applicable(self.v("linux-libc-dev"), {"Class": "lang-pkgs", "Target": "x"}))

    def test_mozilla_firefox_old_snap_advisories_only(self):
        mozilla = self.v("firefox", "158.0~build2", "1:1snap1-0ubuntu1")
        self.assertEqual(ib.not_applicable(mozilla, self.OS), "mozilla-firefox")
        recent = self.v("firefox", "158.0~build2", "1:1snap1-0ubuntu1",
                        published=ib.datetime.datetime.now(ib.datetime.timezone.utc).isoformat())
        self.assertIsNone(ib.not_applicable(recent, self.OS))                      # a new one counts
        ubuntu_deb = self.v("firefox", "1:1snap1-0ubuntu1", "1:1snap1-0ubuntu2")
        self.assertIsNone(ib.not_applicable(ubuntu_deb, self.OS))                  # Ubuntu's own package counts
        real_fix = self.v("firefox", "158.0~build2", "159.0")
        self.assertIsNone(ib.not_applicable(real_fix, self.OS))                    # a real fixed version counts

    def test_grade_counts_only_what_applies(self):
        self.assertEqual(ib.grade({"vulnerabilities": {"counts": {"HIGH": 0, "MEDIUM": 258}}, "checks": []}), "B")
        self.assertEqual(ib.grade({"vulnerabilities": {"counts": {"HIGH": 1, "MEDIUM": 0}}, "checks": []}), "C")

    def test_projected_grade_and_needs_fixing(self):
        """The Images list's "31 fixable -> B" and Needs fixing: the grade with every fixable finding fixed."""
        def report(counts, fixable, **extra):
            return dict({"grade": None, "checks": [], "vulnerabilities": {"counts": counts, "fixableHereCounts": fixable}},
                        **extra)
        r = report({"CRITICAL": 3, "HIGH": 34, "MEDIUM": 5}, {"CRITICAL": 3, "HIGH": 33})
        r["grade"] = ib.grade(r)
        self.assertEqual((r["grade"], ib.projected_grade(r), ib.needs_fixing(r)), ("D", "C", True))
        # nothing fixable: the grade stays, so it does not need fixing (there is nothing to fix)
        r = report({"CRITICAL": 3}, {"CRITICAL": 0})
        r["grade"] = ib.grade(r)
        self.assertEqual((r["grade"], ib.projected_grade(r), ib.needs_fixing(r)), ("D", "D", False))
        # a C with one fixable high: better after, but not "needs fixing" (the list shows View fix)
        r = report({"HIGH": 1}, {"HIGH": 1})
        r["grade"] = ib.grade(r)
        self.assertEqual((r["grade"], ib.projected_grade(r), ib.needs_fixing(r)), ("C", "A", False))
        # a secret keeps it F whatever is fixed: no projected improvement
        r = report({"CRITICAL": 1}, {"CRITICAL": 1}, secrets=[{"rule": "aws"}])
        r["grade"] = ib.grade(r)
        self.assertEqual((r["grade"], ib.projected_grade(r), ib.needs_fixing(r)), ("F", "F", False))
        self.assertIsNone(ib.projected_grade({"error": "trivy failed"}))
        # a report from before fixableHereCounts: counted from its top findings, only what Fix image can fix
        old = {"grade": "D", "checks": [], "vulnerabilities": {"counts": {"CRITICAL": 2}, "top": [
            {"severity": "CRITICAL", "fixed": "1.2", "target": "img (ubuntu 26.04)"},
            {"severity": "CRITICAL", "fixed": ""}]}}
        self.assertEqual(ib.fixable_counts(old)["CRITICAL"], 1)
        summary = ib.report_summary(old)
        self.assertEqual((summary["projectedGrade"], summary["needsFixing"], summary["fixableHere"]), ("D", False, 1))

    def test_what_fix_image_can_fix(self):
        """The same rules as the page's fixPlan.ts: OS and Python packages with a fixed version; not the packages
        inside a program the image ships (they need a newer release of it), the targets as trivy writes them."""
        here = ib.fix_here
        self.assertTrue(here({"fixed": "1.2", "target": "registry:5000/x:1 (ubuntu 26.04)"}))
        self.assertTrue(here({"fixed": "1.2", "class": "os-pkgs", "target": "x"}))
        self.assertTrue(here({"fixed": "4.14.2", "target": "Python"}))
        self.assertTrue(here({"fixed": "1", "class": "lang-pkgs", "type": "python-pkg", "target": "x"}))
        self.assertTrue(here({"fixed": "1", "target": "opt/a/lib/python3.12/site-packages/x/METADATA"}))
        self.assertFalse(here({"fixed": "0.60.0", "target": "usr/local/bin/filebrowser"}))   # a Go program's
        self.assertFalse(here({"fixed": "6.2.1", "target": "Node.js"}))
        self.assertFalse(here({"fixed": "1", "class": "lang-pkgs", "type": "jar", "target": "x"}))
        self.assertFalse(here({"fixed": "", "target": "img (ubuntu 26.04)"}))


class TrackedWorkspaceImageTest(unittest.TestCase):
    """The workspace template's image as an entry of the Images view: made, kept to the deployed image, scanned when
    it is new or changed, and read-only."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()
        self.rescans = []
        self.digest = "sha256:" + "1" * 64
        patches = [
            mock.patch.object(ib, "STORE", ib.DB(os.path.join(self.tmp, "images.sqlite"))),
            mock.patch.object(ib, "ENABLED", True),
            mock.patch.object(ib, "WORKSPACE_TEMPLATE", "DEFAULT"),
            mock.patch.object(ib, "LOG", None),
            mock.patch.object(ib, "start_rescan", side_effect=lambda image_id, user: self.rescans.append(image_id)),
            mock.patch.object(ib, "manifest", side_effect=lambda repo, ref: ({}, self.digest)),
            mock.patch.object(ib, "image_size", return_value=1234),
            mock.patch.object(ib, "FILES", self.tmp),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.deploy("1.8.2")

    def deploy(self, tag):
        for name, value in (("WORKSPACE_IMAGE", "ghcr.io/user187x/coder-workspace:" + tag),
                            ("BASE_IMAGE", ib.REGISTRY + "/coder-images/base/coder-workspace:" + tag)):
            p = mock.patch.object(ib, name, value)
            p.start()
            self.addCleanup(p.stop)

    def test_made_scanned_and_kept_to_the_deployed_image(self):
        ib.sync_tracked()
        img = ib.STORE.image(ib.TRACKED_ID)
        self.assertEqual(img["status"], "ready")
        self.assertEqual(img["pullRef"], "ghcr.io/user187x/coder-workspace:1.8.2")  # what templates name
        self.assertEqual(img["ref"], ib.REGISTRY + "/coder-images/base/coder-workspace:1.8.2")  # what is scanned
        self.assertEqual(img["tracked"]["template"], "DEFAULT")
        self.assertEqual(self.rescans, [ib.TRACKED_ID])
        ib.STORE.set(ib.TRACKED_ID, report={"grade": "C"})
        ib.sync_tracked()
        self.assertEqual(self.rescans, [ib.TRACKED_ID])  # scanned, unchanged: not again
        # a chart upgrade deploys another image: the entry follows it, and it is scanned again
        self.deploy("1.8.3")
        self.digest = "sha256:" + "2" * 64
        ib.sync_tracked()
        img = ib.STORE.image(ib.TRACKED_ID)
        self.assertEqual(img["pullRef"], "ghcr.io/user187x/coder-workspace:1.8.3")
        self.assertIsNone(img["report"])
        self.assertEqual(self.rescans, [ib.TRACKED_ID, ib.TRACKED_ID])

    def test_waits_as_a_draft_until_the_builder_has_its_copy(self):
        with mock.patch.object(ib, "manifest", side_effect=OSError("404")):
            ib.sync_tracked()
        img = ib.STORE.image(ib.TRACKED_ID)
        self.assertEqual(img["status"], "draft")
        self.assertIsNone(img["pullRef"])
        self.assertEqual(self.rescans, [])

    def test_read_only_and_its_name_reserved(self):
        ib.sync_tracked()
        h = mock.MagicMock()
        user = {"username": "admin"}
        for method, sub in (("POST", ""), ("POST", "/build"), ("POST", "/s3"), ("PUT", "/files/deb/x.deb")):
            with self.assertRaisesRegex(ValueError, "image the workspace template runs"):
                ib.route(h, method, "/" + ib.TRACKED_ID + sub, "", user, True, True)
        self.assertIn(ib.TRACKED_ID, ib.RESERVED_NAMES)
        # as a base it is the workspace image itself
        self.assertEqual(ib.clean_spec({"base": {"kind": "image", "id": ib.TRACKED_ID}}, "mine")["base"],
                         {"kind": "workspace"})

    def test_deleted_it_stays_away_until_another_workspace_image_is_deployed(self):
        ib.sync_tracked()
        h = mock.MagicMock()
        with mock.patch.object(ib, "LOG", lambda **kw: None):
            ib.route(h, "POST", "/" + ib.TRACKED_ID + "/delete", "", {"username": "dev"}, False, False)
            self.assertEqual(h.send_json.call_args[0][0], 403)  # editors only
            self.assertIsNotNone(ib.STORE.image(ib.TRACKED_ID))
            ib.route(h, "POST", "/" + ib.TRACKED_ID + "/delete", "", {"username": "admin"}, True, True)
        self.assertIsNone(ib.STORE.image(ib.TRACKED_ID))
        ib.sync_tracked()
        self.assertIsNone(ib.STORE.image(ib.TRACKED_ID))  # the same image: not listed again
        self.deploy("1.8.3")
        ib.sync_tracked()
        self.assertEqual(ib.STORE.image(ib.TRACKED_ID)["pullRef"], "ghcr.io/user187x/coder-workspace:1.8.3")

    def test_contents_of_our_images(self):
        h = mock.MagicMock()
        catalog = {"images": {"ghcr.io/user187x/coder-workspace:1.8.2": {"repository": "coder-workspace"}}}
        with mock.patch.dict(ib._contents, {"data": catalog}):
            ib.route(h, "GET", "/contents", "", {"username": "dev"}, False, False)
        h.send_json.assert_called_with(200, catalog)
        self.assertIn("contents", ib.RESERVED_NAMES)
        # the file shipped with the service: our images, each with its packages and build steps
        ib._contents.clear()
        shipped = ib.image_contents()["images"]
        self.assertTrue(shipped and all(ref.startswith("ghcr.io/user187x/") for ref in shipped))
        for i in shipped.values():
            self.assertTrue(i["steps"] and i["digest"].startswith("sha256:"), i["ref"])
            # add-ons as the reports give them: every part there (addons.json has no "python" part)
            if i["addons"] is not None:
                self.assertEqual(set(i["addons"]), {"base", "builtAt", "software", "packages", "python"}, i["ref"])
                self.assertIsInstance(i["addons"]["python"]["packages"], list)
        self.assertTrue(any(i["addons"] for i in shipped.values()))  # the workspace images have them

    def test_addons_read_from_the_image(self):
        import json
        a = ib.addons_of(json.dumps({"schema": 1, "base": "codercom/enterprise-base:ubuntu", "software": [
            {"name": "code-server", "version": "4.141.0", "group": "Apps", "what": "VS Code in the browser"}],
            "packages": {"added": [{"name": "firefox", "version": "157.0"}], "dependencies": 800, "upgraded": 3,
                         "installed": 1300}, "python": {"packages": [{"name": "numpy", "version": "2.4.0"}]}}))
        self.assertEqual(a["software"][0]["version"], "4.141.0")
        self.assertEqual(a["packages"]["added"][0]["name"], "firefox")
        self.assertEqual(a["packages"]["dependencies"], 800)
        self.assertEqual(a["python"]["packages"][0]["name"], "numpy")
        self.assertIsNone(ib.addons_of("not json"))
        self.assertIsNone(ib.addons_of(None))



class SafeTest(unittest.TestCase):
    """The safe (safe.py): only ever the caller's own prefix, every account's volume made, the nodes' endpoint."""

    UID = "3f0c2a52-7d7e-4c5b-9a3e-2f4b8c1d0e9a"

    def setUp(self):
        self.safe = load("safe", "safe.py")
        self.safe.LOG = lambda **kw: None

    def test_a_kept_volume_from_an_earlier_install_is_adopted(self):
        # a reinstall (the namespace deleted, the Retain volumes kept): Released with the old claim's uid - pre-bound to
        # this install's claim; a volume bound to another namespace's live claim, or to this one's, is left alone
        ns = self.safe.NAMESPACE
        calls = []

        class Kube:
            def __init__(self, claims):
                self.claims = claims

            def call(self, method, path, body=None, patch_type=None):
                calls.append((method, path, body))
                if method == "GET":
                    uid = self.claims.get(path.rsplit("/", 1)[1])
                    return (200, {"metadata": {"uid": uid}}) if uid else (404, None)
                return 200, {}

        def pv(phase, ref):
            return {"spec": {"claimRef": ref}, "status": {"phase": phase}}

        self.safe.KUBE = Kube({})
        self.assertTrue(self.safe.adopt_volume("coder-isos", pv("Released", {"namespace": ns, "name": "coder-isos", "uid": "old"})))
        self.assertEqual(calls[-1][2]["spec"]["claimRef"], {"namespace": ns, "name": "coder-isos", "uid": None, "resourceVersion": None})
        self.assertTrue(self.safe.adopt_volume("coder-isos", pv("Released", {"namespace": "coder", "name": "coder-isos", "uid": "x"}))
                        or ns == "coder")
        n = len(calls)
        self.assertFalse(self.safe.adopt_volume("coder-isos", pv("Bound", {"namespace": "elsewhere", "name": "coder-isos", "uid": "y"})))
        self.safe.KUBE = Kube({"coder-isos": "mine"})
        self.assertFalse(self.safe.adopt_volume("coder-isos", pv("Bound", {"namespace": ns, "name": "coder-isos", "uid": "mine"})))
        self.assertFalse(self.safe.adopt_volume("coder-isos", pv("Available", {"namespace": ns, "name": "coder-isos"})))
        self.assertEqual([c for c in calls[n:] if c[0] == "PATCH"], [], "nothing patched for a volume in use or already ours")

    def test_paths_stay_inside_the_safe(self):
        c = self.safe.clean_path
        self.assertEqual(c("/docs/a b.txt/"), "docs/a b.txt")
        self.assertEqual(c(""), "")
        for bad in ("../x", "a/../../b", "a//b", "./a", "a/\x00b", "a/\nb", "x" * 256):
            with self.assertRaises(ValueError, msg=bad):
                c(bad)
        self.assertEqual(self.safe.root_of(self.UID), "users/%s/" % self.UID)
        with self.assertRaises(PermissionError):
            self.safe.root_of("../other")

    def test_volume_is_the_accounts_prefix_retained(self):
        pv, pvc = self.safe.volume_objects(self.UID, "alice")
        self.assertEqual(pv["metadata"]["name"], "coder-safe-" + self.UID)
        self.assertEqual(pv["spec"]["csi"]["volumeHandle"], "coder-user-storage/users/" + self.UID)
        self.assertEqual(pv["spec"]["persistentVolumeReclaimPolicy"], "Retain")
        self.assertEqual(pv["spec"]["claimRef"], {"namespace": "coder", "name": "coder-safe-" + self.UID})
        self.assertEqual(pv["spec"]["csi"]["nodePublishSecretRef"]["name"], "coder-user-storage-mount")
        self.assertEqual((pvc["spec"]["volumeName"], pvc["spec"]["accessModes"], pvc["spec"]["storageClassName"]),
                         (pv["metadata"]["name"], ["ReadWriteMany"], ""))

    def test_reconcile_makes_what_is_missing_and_the_nodes_endpoint(self):
        import base64
        calls = []
        have = {"/api/v1/persistentvolumes/coder-safe-" + self.UID: {}}                # the PV is there already

        class Kube:
            ok = True

            def call(self, method, path, body=None, patch_type=None):
                calls.append((method, path))
                if method == "GET" and path.endswith("/services/coder-user-storage-minio"):
                    return 200, {"spec": {"clusterIP": "10.96.4.5"}}
                if method == "GET" and path.endswith("/pods"):
                    return 200, {"items": []}
                if method == "GET":
                    return (200, have[path]) if path in have else (404, {})
                have[path + "/" + body["metadata"]["name"]] = body                      # POST: made
                return 201, body

        self.safe.KUBE = Kube()
        self.safe.SETTINGS_FILE = "/nonexistent/cloud-drive.json"                      # the chart's backend
        self.safe.NODE_SERVICE = "coder-user-storage-minio"
        self.safe.USERS = lambda: [(self.UID, "alice")]
        s3 = mock.Mock()
        s3.bucket_exists.return_value = False
        with mock.patch.object(self.safe, "client", return_value=s3), \
                mock.patch.dict(os.environ, {"S3_ACCESS_KEY": "k", "S3_SECRET_KEY": "s"}):
            self.safe.reconcile()
            self.safe.reconcile()                                                       # nothing more to make
        s3.create_bucket.assert_called_with("coder-user-storage")
        self.assertEqual([p for m, p in calls if m in ("POST", "PUT")],
                         ["/api/v1/namespaces/coder/secrets",                            # the mount Secret
                          "/api/v1/persistentvolumes",                                   # the ISO library's
                          "/api/v1/namespaces/coder/persistentvolumeclaims",
                          "/api/v1/namespaces/coder/persistentvolumeclaims"])            # the account's (its PV was there)
        secret = have["/api/v1/namespaces/coder/secrets/coder-user-storage-mount"]["data"]
        self.assertEqual({k: base64.b64decode(v).decode() for k, v in secret.items()},
                         {"accessKeyID": "k", "secretAccessKey": "s", "endpoint": "http://10.96.4.5:9000"})
        # the account's volume is asked about once in ten minutes, not every round
        self.assertEqual(sum(1 for m, p in calls if p.endswith("persistentvolumeclaims/coder-safe-" + self.UID)), 1)

    def test_listing_shows_folders_and_files_not_the_folder_marker(self):
        prefix = "users/%s/docs/" % self.UID
        s3 = mock.Mock()
        s3.list.return_value = ([{"key": prefix, "size": 0, "modified": "t"},
                                 {"key": prefix + "b.txt", "size": 5, "modified": "t"},
                                 {"key": prefix + "A.txt", "size": 3, "modified": "t"}],
                                [prefix + "zeta/", prefix + "Alpha/"])
        with mock.patch.object(self.safe, "client", return_value=s3):
            out = self.safe.listing({"id": self.UID}, "docs")
        s3.list.assert_called_with("coder-user-storage", prefix=prefix, delimiter="/")
        self.assertEqual(out["folders"], [{"name": "Alpha"}, {"name": "zeta"}])
        self.assertEqual([f["name"] for f in out["files"]], ["A.txt", "b.txt"])

    def test_writes_need_the_dashboards_header(self):
        self.safe.ENABLED = True
        sent = []
        h = mock.Mock()
        h.headers = {"Origin": "https://coder.example", "Host": "coder.example"}
        h.send_json = lambda status, body: sent.append(status)
        self.assertTrue(self.safe.handle(h, "POST", "/__coder-ui/api/safe/delete", ""))
        self.assertEqual(sent, [403])
        h.user.assert_not_called()
        h.headers = {"X-Requested-With": "coder-ui", "Origin": "https://evil.example", "Host": "coder.example"}
        self.safe.handle(h, "PUT", "/__coder-ui/api/safe/upload", "path=a")
        self.assertEqual(sent, [403, 403])
        self.assertFalse(self.safe.handle(h, "GET", "/__coder-ui/api/other", ""))


class NodeImagesTest(unittest.TestCase):
    """The images on the nodes (Templates > Images): a node reports only its largest images (nodeStatusMaxImages), so
    a small image a running pod runs is still listed on that pod's node, and can be scanned there."""

    def test_running_pods_images_count_as_on_their_node(self):
        nodes = {"items": [{"metadata": {"name": "n1"}, "status": {"images": [
            {"names": ["ghcr.io/o/big:1", "ghcr.io/o/big@sha256:aa"], "sizeBytes": 5000}]}}]}
        pods = {"items": [
            {"spec": {"nodeName": "n2"}, "status": {"containerStatuses": [
                {"image": "ghcr.io/o/small:1", "imageID": "sha256:0123"}],  # loaded locally: only its ID
                "initContainerStatuses": [{"image": "ghcr.io/o/small:1", "imageID": "sha256:0123"}]}},
            {"spec": {"nodeName": "n1"}, "status": {"containerStatuses": [
                {"image": "ghcr.io/o/big:1", "imageID": "ghcr.io/o/big@sha256:aa"},
                {"image": "ghcr.io/o/small:1", "imageID": "ghcr.io/o/small@sha256:bb"}]}},
            {"spec": {}, "status": {"containerStatuses": [{"image": "ghcr.io/o/unscheduled:1"}]}},
        ]}
        calls = lambda method, path, *a, **k: (200, nodes if path == "/api/v1/nodes" else pods)
        with mock.patch.object(ib, "kube_call", calls):
            found = ib.node_images()
            groups = ib.node_image_groups()
        self.assertEqual(found["ghcr.io/o/big:1"], {"sizeBytes": 5000, "nodes": ["n1"]})
        self.assertEqual(sorted(found["ghcr.io/o/small:1"]["nodes"]), ["n1", "n2"])
        self.assertEqual(found["ghcr.io/o/small@sha256:bb"], found["ghcr.io/o/small:1"])
        self.assertNotIn("sha256:0123", found)
        self.assertNotIn("ghcr.io/o/unscheduled:1", found)
        self.assertEqual(len(groups), 2)  # each image once, all its names together
        with mock.patch.object(ib, "kube_call", calls):
            self.assertEqual(ib.scan_target("ghcr.io/o/small:1"), ("ghcr.io/o/small:1", "n1"))


class FakeRegistry:
    """An in-memory registry (the v2 API's manifests, blobs, uploads and deletes), on a port of its own."""

    def __init__(self):
        import hashlib, http.server, re, socketserver
        self.blobs, self.manifests = {}, {}
        reg, sha = self, lambda b: "sha256:" + hashlib.sha256(b).hexdigest()
        self.sha = sha

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def reply(self, code, body=b"", headers=None):
                self.send_response(code)
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)

            def do_GET(self):
                repo, kind, ref = re.match(r"^/v2/(.+)/(manifests|blobs)/([^/?]+)$", self.path).groups()
                if kind == "blobs":
                    data = reg.blobs.get((repo, ref))
                    return self.reply(200, data) if data is not None else self.reply(404)
                found = reg.manifests.get((repo, ref))
                if not found:
                    return self.reply(404)
                self.reply(200, found[0], {"Content-Type": found[1], "Docker-Content-Digest": sha(found[0])})

            do_HEAD = do_GET

            def do_POST(self):
                repo = re.match(r"^/v2/(.+)/blobs/uploads/$", self.path).group(1)
                self.reply(202, headers={"Location": "/v2/%s/blobs/uploads/u1?_state=x" % repo})

            def do_PUT(self):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                u = urllib.parse.urlparse(self.path)
                up = re.match(r"^/v2/(.+)/blobs/uploads/\w+$", u.path)
                if up:
                    digest = urllib.parse.parse_qs(u.query)["digest"][0]
                    assert digest == sha(body)
                    reg.blobs[(up.group(1), digest)] = body
                    return self.reply(201)
                repo, ref = re.match(r"^/v2/(.+)/manifests/(.+)$", u.path).groups()
                assert not ref.startswith("sha256:") or ref == sha(body)
                for r in {ref, sha(body)}:
                    reg.manifests[(repo, r)] = (body, self.headers["Content-Type"])
                self.reply(201, headers={"Docker-Content-Digest": sha(body)})

            def do_DELETE(self):
                repo, digest = re.match(r"^/v2/(.+)/manifests/(.+)$", self.path).groups()
                body = reg.manifests[(repo, digest)][0]
                for k in [k for k, v in reg.manifests.items() if k[0] == repo and v[0] == body]:
                    del reg.manifests[k]
                self.reply(202)

        self.server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addr = "127.0.0.1:%d" % self.server.server_address[1]

    def put(self, repo, ref, obj, ctype):
        body = json.dumps(obj).encode()
        for r in {ref, self.sha(body)}:
            self.manifests[(repo, r)] = (body, ctype)
        return self.sha(body)

    def blob(self, repo, obj):
        data = json.dumps(obj).encode() if not isinstance(obj, bytes) else obj
        self.blobs[(repo, self.sha(data))] = data
        return self.sha(data)

    def get(self, repo, ref):
        return json.loads(self.manifests[(repo, ref)][0])

    def env(self, repo, ref):
        m = self.get(repo, ref)
        return json.loads(self.blobs[(repo, m["config"]["digest"])])["config"]["Env"]


class PitRegistryTest(unittest.TestCase):
    """The pit stop's own registry work: Copa's source with pip let into environments the distribution manages (the
    image's configuration only, the same layers), and that taken out of the patched image again."""

    V2 = "application/vnd.docker.distribution.manifest.v2+json"
    INDEX = "application/vnd.oci.image.index.v1+json"
    OCI = "application/vnd.oci.image.manifest.v1+json"

    def setUp(self):
        self.reg = FakeRegistry()
        self.patch = mock.patch.object(ib, "REGISTRY", self.reg.addr)
        self.patch.start()
        self.repo = "coder-images/base/dataflow"
        cfg = self.reg.blob(self.repo, {"architecture": "amd64", "config": {"Env": ["PATH=/usr/bin", "LANG=C"]}})
        self.layer = self.reg.blob(self.repo, b"layer")
        self.manifest = {"schemaVersion": 2, "mediaType": self.V2,
                         "config": {"mediaType": "application/vnd.docker.container.image.v1+json", "digest": cfg,
                                    "size": 1},
                         "layers": [{"mediaType": "application/vnd.docker.image.rootfs.diff.tar.gzip",
                                     "digest": self.layer, "size": 5}]}

    def tearDown(self):
        self.patch.stop()
        self.reg.server.shutdown()
        self.reg.server.server_close()

    def test_an_environment_variable_set_and_taken_out_again_with_the_same_layers(self):
        self.reg.put(self.repo, "2.0.0", self.manifest, self.V2)
        ib.image_env(self.repo, "2.0.0", "2.0.0-pit7", add={ib.PIT_PIP_ENV: "1"})
        self.assertEqual(self.reg.env(self.repo, "2.0.0-pit7"), ["PATH=/usr/bin", "LANG=C", "PIP_BREAK_SYSTEM_PACKAGES=1"])
        self.assertEqual(self.reg.get(self.repo, "2.0.0-pit7")["layers"], self.manifest["layers"])
        self.assertEqual(self.reg.env(self.repo, "2.0.0"), ["PATH=/usr/bin", "LANG=C"])  # the source as it was
        ib.image_env(self.repo, "2.0.0-pit7", "3", drop=[ib.PIT_PIP_ENV])
        self.assertEqual(self.reg.env(self.repo, "3"), ["PATH=/usr/bin", "LANG=C"])
        ib.delete_manifest(self.repo, "2.0.0-pit7")
        self.assertNotIn((self.repo, "2.0.0-pit7"), self.reg.manifests)
        self.assertIn((self.repo, "2.0.0"), self.reg.manifests)

    def test_an_index_each_image_and_no_attestations(self):
        image = self.reg.put(self.repo, "m", dict(self.manifest, mediaType=self.OCI), self.OCI)
        att = self.reg.put(self.repo, "a", dict(self.manifest, mediaType=self.OCI), self.OCI)
        self.reg.put(self.repo, "1", {"schemaVersion": 2, "mediaType": self.INDEX, "manifests": [
            {"mediaType": self.OCI, "digest": image, "size": 1, "platform": {"architecture": "amd64", "os": "linux"}},
            {"mediaType": self.OCI, "digest": att, "size": 1, "platform": {"architecture": "unknown", "os": "unknown"},
             "annotations": {"vnd.docker.reference.type": "attestation-manifest"}}]}, self.INDEX)
        ib.image_env(self.repo, "1", "1-pit3", add={ib.PIT_PIP_ENV: "1"})
        index = self.reg.get(self.repo, "1-pit3")
        self.assertEqual([x["platform"]["architecture"] for x in index["manifests"]], ["amd64"])
        self.assertIn("PIP_BREAK_SYSTEM_PACKAGES=1", self.reg.env(self.repo, index["manifests"][0]["digest"]))
        # through the service's own reading of an image
        m, _ = ib.manifest(self.repo, "1-pit3")
        self.assertEqual(m["layers"][0]["digest"], self.layer)


class PitStopTest(unittest.TestCase):
    """Images > Fix image > Pit stop (imagebuilder.py: PitStop): Copa patches an image in place - where the patched
    image goes, the findings worked on, the laps' library levels, the Job, and the Job's own endpoints."""

    REPORT = {"grade": "D", "secrets": [], "checks": [],
              "vulnerabilities": {"counts": {"CRITICAL": 1, "HIGH": 1, "MEDIUM": 0, "LOW": 0, "UNKNOWN": 0}, "total": 3,
                                  "fixable": 2, "top": [
                                      {"id": "CVE-1", "pkg": "openssl", "fixed": "3.0.2", "severity": "CRITICAL",
                                       "class": "os-pkgs", "type": "ubuntu", "target": "x (ubuntu 24.04)"},
                                      {"id": "CVE-2", "pkg": "requests", "fixed": "2.32.0", "severity": "HIGH",
                                       "class": "lang-pkgs", "type": "python-pkg", "target": "Python"},
                                      {"id": "CVE-3", "pkg": "zlib", "fixed": "", "severity": "LOW",
                                       "class": "os-pkgs", "type": "ubuntu", "target": "x (ubuntu 24.04)"}]}}

    def setUp(self):
        import tempfile
        self.dir = tempfile.TemporaryDirectory()
        self.patches = [mock.patch.object(ib, "FILES", self.dir.name),
                        mock.patch.object(ib, "STORE", ib.DB(os.path.join(self.dir.name, "images.sqlite"))),
                        mock.patch.object(ib.PitStop, "start"),  # nothing runs
                        mock.patch.object(ib, "registry_copy", lambda ref: None)]
        for p in self.patches:
            p.start()
        ib.LOG = lambda **kw: None

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.dir.cleanup()

    def scanned(self, ref="ghcr.io/user187x/coder-workspace-dataflow:1.1.3"):
        image_id = ib.scanned_id(ref)
        ib.STORE.create(image_id, "", dict(ib.empty_spec(), scanned={"image": ref}), "alice")
        ib.STORE.set(image_id, status="ready", ref=ref, report=self.REPORT)
        return image_id

    def test_a_scanned_image_goes_out_as_its_patched_copy(self):
        image_id = self.scanned()
        target, build_id = ib.start_pit_stop(image_id, {"libraries": True, "level": "patch", "maxLevel": "major",
                                                        "laps": 3}, "alice")
        self.assertEqual(target, "coder-workspace-dataflow-patched")
        copy = ib.STORE.image(target)
        self.assertEqual((copy["imported"]["source"], copy["imported"]["from"], copy["imported"]["entry"]),
                         ("copa", "ghcr.io/user187x/coder-workspace-dataflow:1.1.3", image_id))
        st = ib.pit_state(build_id)
        # every finding with a fixed version (none chosen), the grade it came in with
        self.assertEqual(st["pairs"], [["CVE-1", "openssl"], ["CVE-2", "requests"]])
        self.assertEqual((st["source"], st["version"], st["before"]["grade"]),
                         ("ghcr.io/user187x/coder-workspace-dataflow:1.1.3", 1, "D"))
        self.assertEqual([s_["key"] for s_ in json.loads(ib.STORE.build(build_id)["steps"])],
                         ["pit-in", "report", "patch", "rescan", "pit-out"])
        # both the scanned entry and the copy know their pit stop
        self.assertEqual(ib.STORE.setting("pit-latest-%s" % image_id), build_id)
        self.assertEqual(ib.STORE.setting("pit-latest-%s" % target), build_id)
        with self.assertRaisesRegex(ValueError, "in the pit already"):
            ib.start_pit_stop(image_id, {}, "alice")

    def test_an_image_built_here_gets_its_next_version_and_only_the_findings_chosen(self):
        ib.STORE.create("tool", "", ib.empty_spec(), "alice")
        ib.STORE.set("tool", status="ready", version=4, ref="%s/coder-images/tool:4" % ib.REGISTRY, report=self.REPORT)
        with self.assertRaisesRegex(ValueError, "Python ones with library patching"):
            ib.start_pit_stop("tool", {"keys": [{"id": "CVE-2", "pkg": "requests"}]}, "alice")
        target, build_id = ib.start_pit_stop("tool", {"keys": [{"id": "CVE-2", "pkg": "requests"}], "libraries": True},
                                             "alice")
        st = ib.pit_state(build_id)
        self.assertEqual((target, st["version"], st["pairs"], st["source"]),
                         ("tool", 5, [["CVE-2", "requests"]], "%s/coder-images/tool:4" % ib.REGISTRY))

    def test_what_a_pit_stop_needs(self):
        ib.STORE.create("draft", "", ib.empty_spec(), "alice")
        with self.assertRaisesRegex(ValueError, "Scan the image first"):
            ib.start_pit_stop("draft", {}, "alice")
        ib.STORE.set("draft", report=self.REPORT)
        with self.assertRaisesRegex(ValueError, "Build the image first"):
            ib.start_pit_stop("draft", {}, "alice")
        image_id = self.scanned()
        for bad, why in (({"laps": 9}, "Laps"), ({"level": "huge"}, "Library updates"),
                         # only a finding with no fixed version chosen
                         ({"keys": [{"id": "CVE-3", "pkg": "zlib"}]}, "Nothing to fix")):
            with self.assertRaisesRegex(ValueError, why):
                ib.start_pit_stop(image_id, bad, "alice")
        # a name taken by an image of another kind
        ib.STORE.create("coder-workspace-dataflow-patched", "", ib.empty_spec(), "alice")
        with self.assertRaisesRegex(ValueError, "not a patched copy"):
            ib.start_pit_stop(image_id, {}, "alice")

    def test_each_lap_lets_library_updates_go_a_level_further(self):
        run = ib.PitStop("x", 1, 1, "t", ib.empty_spec(), "alice", {"level": "patch", "maxLevel": "major"})
        self.assertEqual([run.level(n) for n in (1, 2, 3)], ["patch", "minor", "major"])
        run = ib.PitStop("x", 1, 1, "t", ib.empty_spec(), "alice", {"level": "minor", "maxLevel": "minor"})
        self.assertEqual([run.level(n) for n in (1, 2, 3)], ["minor", "minor", "minor"])

    def test_the_report_copa_gets_and_what_is_left(self):
        trivy = {"Results": [
            {"Target": "x (ubuntu 24.04)", "Class": "os-pkgs", "Vulnerabilities": [
                {"VulnerabilityID": "CVE-1", "PkgName": "openssl"}, {"VulnerabilityID": "CVE-9", "PkgName": "curl"}]},
            {"Target": "Python", "Class": "lang-pkgs", "Type": "python-pkg", "Vulnerabilities": [
                {"VulnerabilityID": "CVE-8", "PkgName": "idna"}]}]}
        out = ib.pit_filter(trivy, [["CVE-1", "openssl"]])
        self.assertEqual([(r["Target"], [v["VulnerabilityID"] for v in r["Vulnerabilities"]]) for r in out["Results"]],
                         [("x (ubuntu 24.04)", ["CVE-1"])])
        self.assertEqual(ib.pit_remaining(self.REPORT, {("CVE-1", "openssl"), ("CVE-7", "gone")}),
                         [("CVE-1", "openssl")])

    def test_copa_works_on_os_and_python_packages_only(self):
        top = [dict(v) for v in self.REPORT["vulnerabilities"]["top"]] + [
            {"id": "CVE-4", "pkg": "lodash", "fixed": "4.17.21", "class": "lang-pkgs", "type": "node-pkg",
             "target": "Node.js"},
            {"id": "CVE-5", "pkg": "stdlib", "fixed": "1.24.1", "class": "lang-pkgs", "type": "gobinary",
             "target": "usr/bin/x"},
            # a scan from before findings had their class and type: told by the target and the path
            {"id": "CVE-6", "pkg": "glibc", "fixed": "2.39-0ubuntu8.5", "target": "x (ubuntu 24.04)"},
            {"id": "CVE-7", "pkg": "idna", "fixed": "3.7", "target": "opt/venv/lib/python3.12/site-packages/idna-3.6.dist-info/METADATA"}]
        report = {"vulnerabilities": {"top": top}}
        self.assertEqual(ib.pit_keys(report, []), {("CVE-1", "openssl"), ("CVE-2", "requests"), ("CVE-6", "glibc"),
                                                   ("CVE-7", "idna")})
        self.assertEqual(ib.pit_keys(report, [], libraries=False), {("CVE-1", "openssl"), ("CVE-6", "glibc")})
        self.assertEqual(ib.pit_keys(report, [{"id": "CVE-4", "pkg": "lodash"}, {"id": "CVE-2", "pkg": "requests"}]),
                         {("CVE-2", "requests")})
        trivy = {"Results": [
            {"Target": "Python", "Class": "lang-pkgs", "Type": "python-pkg", "Vulnerabilities": [
                {"VulnerabilityID": "CVE-2", "PkgName": "requests"}]},
            {"Target": "Node.js", "Class": "lang-pkgs", "Type": "node-pkg", "Vulnerabilities": [
                {"VulnerabilityID": "CVE-2", "PkgName": "requests"}]}]}
        self.assertEqual([r["Target"] for r in ib.pit_filter(trivy, [["CVE-2", "requests"]])["Results"]], ["Python"])

    def test_the_lap_job_runs_copa_beside_a_rootless_buildkit(self):
        with mock.patch.object(ib, "PULL_SECRETS", [{"name": "harbor"}]), \
                mock.patch.object(ib.securitydata, "active_bundle", lambda: None):
            name, body = ib.copa_job("tool", 7, 2, "tok")
        self.assertEqual(name, "coder-image-copa2-tool-7")
        spec = body["spec"]["template"]["spec"]
        sidecar = next(c for c in spec["initContainers"] if c["name"] == "buildkitd")
        self.assertEqual(sidecar["restartPolicy"], "Always")  # a native sidecar: the Job ends with Copa
        self.assertEqual(sidecar["command"][:4], ["rootlesskit", "buildkitd", "--addr", "unix:///run/copa/buildkitd.sock"])
        copa = spec["containers"][0]
        self.assertEqual(copa["command"][-2:], ["copa", "%s%s/internal/builds/tok/copa/2" % (ib.SERVICE_URL, ib.BASE)])
        env = {e["name"]: e["value"] for e in copa["env"]}
        self.assertEqual((env["COPA_ADDR"], env["DOCKER_CONFIG"]), ("unix:///run/copa/buildkitd.sock", "/docker"))
        self.assertEqual(spec["securityContext"]["seccompProfile"]["type"], "Unconfined")  # rootlesskit

    def test_the_lap_reads_its_report_without_java_archives(self):
        # Copa does not patch them, and trivy would need its Java database for them (none: it stops on the first run)
        import io
        plan = {"lap": 1, "source": "%s/coder-images/base/dataflow:2" % ib.REGISTRY,
                "target": "%s/coder-images/dataflow-patched:1" % ib.REGISTRY, "libraries": True, "pairs": [],
                "report": None}
        runs = []

        def run(cmd, **kw):
            runs.append(cmd)
            return mock.Mock(returncode=1, stdout=b"", stderr=b"FATAL")

        with mock.patch.object(ib, "_forward"), mock.patch.object(ib.subprocess, "run", run), \
                mock.patch.object(ib.securitydata, "job_stamp", lambda d: {"cacheDir": "/data"}), \
                mock.patch.object(ib.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(json.dumps(plan).encode())), \
                mock.patch.dict(os.environ, {"BUILDER_REGISTRY": ib.REGISTRY}), \
                self.assertRaises(SystemExit), mock.patch("builtins.print"):
            ib.copa("http://svc/copa/1")
        cmd = runs[0]
        self.assertEqual(cmd[:2], ["trivy", "image"])
        self.assertEqual([cmd[i + 1] for i, a in enumerate(cmd) if a == "--skip-files"], ["**/*.jar", "**/*.war", "**/*.ear"])
        self.assertEqual((cmd[cmd.index("--pkg-types") + 1], cmd[-1]), ("os,library", "localhost:5000/coder-images/base/dataflow:2"))

    def test_the_jobs_own_endpoints(self):
        import io
        image_id = self.scanned()
        target, build_id = ib.start_pit_stop(image_id, {"libraries": True, "level": "patch", "maxLevel": "minor"},
                                             "alice")
        token = ib.STORE.build(build_id)["token"]

        class H:
            def __init__(self, body=b""):
                self.headers = {"Content-Length": str(len(body))}
                self.rfile = io.BytesIO(body)
                self.out = None

            def send_json(self, code, obj):
                self.out = (code, obj)

            def send(self, code, text):
                self.out = (code, text)

        h = H()
        path = "%s/internal/builds/%s/copa/2" % (ib.BASE, token)
        self.assertTrue(ib.handle_internal(h, "GET", path))
        code, plan = h.out
        self.assertEqual((code, plan["lap"], plan["level"], plan["target"], plan["report"]),
                         (200, 2, "minor", "%s/coder-images/%s:1" % (ib.REGISTRY, target), None))
        h = H(json.dumps({"Results": []}).encode())
        ib.handle_internal(h, "POST", "%s/internal/builds/%s/copa/1/report" % (ib.BASE, token))
        self.assertEqual(ib.STORE.setting("pit-report-%d" % build_id), {"Results": []})
        h = H(json.dumps({"exit": 0, "pushed": True}).encode())
        ib.handle_internal(h, "POST", "%s/internal/builds/%s/copa/1/result" % (ib.BASE, token))
        self.assertEqual(ib.STORE.setting("pit-result-%d-1" % build_id)["pushed"], True)
        # the image's view carries it
        view = ib.pit_view(build_id)
        self.assertEqual((view["target"], view["status"], view["outcome"]), (target, "queued", "running"))


class ImageImportTest(unittest.TestCase):
    """Images brought in (imagebuilder.py: Import): references, names, archives, and what workspaces run."""

    def setUp(self):
        import tempfile
        self.dir = tempfile.TemporaryDirectory()
        self.files = mock.patch.object(ib, "FILES", self.dir.name)
        self.files.start()
        self.store = mock.patch.object(ib, "STORE", ib.DB(os.path.join(self.dir.name, "images.sqlite")))
        self.store.start()
        self.started = mock.patch.object(ib.Import, "start")  # nothing runs
        self.started.start()
        ib.LOG = lambda **kw: None

    def tearDown(self):
        self.started.stop()
        self.store.stop()
        self.files.stop()
        self.dir.cleanup()

    def test_references_and_their_registry(self):
        for ok in ("registry.example.org/team/tool:1.2", "harbor.local:8443/a/b@sha256:" + "0" * 64,
                   "ghcr.io/user187x/coder-workspace:1.8.5"):
            self.assertTrue(ib.REF_RE.match(ok), ok)
        for bad in ("tool:1.2", "registry.example.org/team/tool", "registry.example.org/team/tool:1.2 extra",
                    "https://registry.example.org/a:1"):
            self.assertFalse(ib.REF_RE.match(bad), bad)
        self.assertEqual(ib.registry_host("harbor.local:8443/a/b:1"), "harbor.local:8443")
        self.assertEqual(ib.registry_host("library/python:3"), "docker.io")

    def test_an_import_is_an_image_of_its_own_and_its_versions(self):
        src = {"kind": "registry", "ref": "registry.example.org/team/tool:1.2", "user": "", "password": ""}
        image_id, build_id = ib.start_import("Team-Tool", "the team's tool", src, "alice")
        img = ib.STORE.image("team-tool")
        self.assertEqual((image_id, img["imported"]["source"], img["imported"]["from"]),
                         ("team-tool", "registry", "registry.example.org/team/tool:1.2"))
        self.assertEqual(ib.STORE.build(build_id)["version"], 1)
        steps = [s["key"] for s in json.loads(ib.STORE.build(build_id)["steps"])]
        self.assertEqual(steps, ["copy", "push", "scan", "checks"])
        with self.assertRaisesRegex(ValueError, "being imported"):
            ib.start_import("team-tool", "", src, "alice")
        ib.STORE.update_build(build_id, status="ready")
        ib.STORE.set("team-tool", version=1, ref="x", status="ready")
        _, second = ib.start_import("team-tool", "", src, "bob")
        self.assertEqual(ib.STORE.build(second)["version"], 2)
        ib.STORE.create("built-here", "", ib.empty_spec(), "alice")
        with self.assertRaisesRegex(ValueError, "built here"):
            ib.start_import("built-here", "", src, "alice")
        with self.assertRaisesRegex(ValueError, "uses itself"):
            ib.start_import("in-use", "", src, "alice")

    def test_an_uploaded_archive_gzip_or_not(self):
        import gzip as gz, io, tarfile
        raw = io.BytesIO()
        with tarfile.open(fileobj=raw, mode="w") as tf:
            info = tarfile.TarInfo("manifest.json")
            info.size = 2
            tf.addfile(info, io.BytesIO(b"[]"))
        for body, label in ((raw.getvalue(), "tar"), (gz.compress(raw.getvalue()), "tar.gz"), (b"not a tar" * 100, "junk")):
            sent = []
            h = mock.Mock()
            h.headers = {"Content-Length": str(len(body))}
            h.rfile = io.BytesIO(body)
            h.send_json = lambda status, payload: sent.append((status, payload))
            q = {"name": ["up-" + label.replace(".", "-")], "file": ["x." + label]}
            if label == "junk":
                before = sorted(os.listdir(os.path.join(self.dir.name, "imports")))
                with self.assertRaisesRegex(ValueError, "not an image archive"):
                    ib.import_upload(h, q, {"username": "alice"})
                self.assertEqual(sorted(os.listdir(os.path.join(self.dir.name, "imports"))), before)  # nothing left
                continue
            ib.import_upload(h, q, {"username": "alice"})
            self.assertEqual(sent[0][0], 200, sent)
            build = ib.STORE.build(sent[0][1]["build"])
            self.assertEqual([s["key"] for s in json.loads(build["steps"])][0], "load")
        files = os.listdir(os.path.join(self.dir.name, "imports"))
        self.assertEqual(len(files), 2)                                         # one tar each, unpacked
        for f in files:
            self.assertTrue(tarfile.is_tarfile(os.path.join(self.dir.name, "imports", f)))

    def test_images_in_use(self):
        pods = {"items": [
            {"metadata": {"labels": {"com.coder.user.username": "alice", "com.coder.workspace.name": "w1"}},
             "spec": {"containers": [{"image": "ghcr.io/user187x/coder-workspace:1.8.5"}]}, "status": {"phase": "Running"}},
            {"metadata": {"labels": {"com.coder.user.username": "bob", "com.coder.workspace.name": "w2"}},
             "spec": {"containers": [{"image": "localhost:5000/coder-images/team-tool:2"}]}, "status": {"phase": "Running"}},
            {"metadata": {"labels": {}}, "spec": {"containers": [{"image": "old:1"}]}, "status": {"phase": "Succeeded"}}]}
        ib.STORE.create("team-tool", "", dict(ib.empty_spec(), imported={"source": "registry"}), "alice")
        ib.STORE.set("team-tool", version=2, ref="r", status="ready")
        with mock.patch.object(ib, "kube_call", return_value=(200, pods)), \
                mock.patch.object(ib, "TEMPLATES", [
                    {"name": "default", "displayName": "DEFAULT", "image": "ghcr.io/user187x/coder-workspace:1.8.5"},
                    {"name": "ubuntu-dataflow", "displayName": "DATAFLOW",
                     "image": "ghcr.io/user187x/coder-workspace-dataflow:1.1.0"}]):
            out = {x["image"]: x for x in ib.images_in_use()}
        self.assertEqual(out["ghcr.io/user187x/coder-workspace:1.8.5"]["workspaces"], ["alice/w1"])
        self.assertEqual(out["ghcr.io/user187x/coder-workspace:1.8.5"]["templates"], ["DEFAULT"])
        self.assertEqual(out["ghcr.io/user187x/coder-workspace-dataflow:1.1.0"]["workspaces"], [])
        self.assertEqual(out["localhost:5000/coder-images/team-tool:2"]["entry"], "team-tool")
        self.assertNotIn("old:1", out)


class IsoLibraryTest(unittest.TestCase):
    def setUp(self):
        self.safe = load("safe", "safe.py")
        self.safe.LOG = lambda **kw: None

    def test_names_and_the_shared_read_only_volume(self):
        for ok in ("ubuntu-26.04-desktop-amd64.iso", "disk.IMG"):
            self.assertEqual(self.safe.library_name("isos", ok), ok)
        for bad in ("../x.iso", "notes.txt", "a b.iso", ".iso", "model.gguf"):
            with self.assertRaises(ValueError, msg=bad):
                self.safe.library_name("isos", bad)
        pv, pvc = self.safe.library_volume_objects("isos")
        self.assertEqual((pv["spec"]["csi"]["volumeHandle"], pv["spec"]["accessModes"], pv["spec"]["csi"]["readOnly"]),
                         ("coder-user-storage/library/isos", ["ReadOnlyMany"], True))
        self.assertEqual(pvc["spec"]["volumeName"], "coder-isos")

    def test_the_iso_library_is_the_only_one(self):
        """The model library went with the AI template: no models, no hold, nothing answers on /api/models."""
        self.assertEqual(list(self.safe.LIBRARIES), ["isos"])
        self.assertEqual(self.safe.LIBRARIES["isos"]["mountPath"], "/home/coder/isos")
        pv, _ = self.safe.library_volume_objects("isos")
        self.assertEqual(pv["spec"]["capacity"]["storage"], "20Gi")
        self.assertFalse(hasattr(self.safe, "LIBRARY_HOOKS"))

    def test_it_answers_on_its_own_path(self):
        self.safe.ENABLED = True
        self.safe.CAN_EDIT = lambda user: True
        self.safe.client = lambda: mock.Mock(list=mock.Mock(return_value=([
            {"key": "library/isos/a.iso", "size": 5, "modified": "2026-10-17T00:00:00Z"}], None)))
        sent = []
        h = mock.Mock()
        h.headers = {}
        h.user.return_value = ({"id": "u", "username": "dev", "roles": ["owner"]}, None)
        h.send_json = lambda status, body: sent.append((status, body))
        self.assertTrue(self.safe.handle_libraries(h, "GET", "/__coder-ui/api/isos", ""))
        self.assertEqual(sent[0][0], 200)
        self.assertEqual([f["name"] for f in sent[0][1]["files"]], ["a.iso"])
        self.assertEqual(sent[0][1]["mountPath"], "/home/coder/isos")
        for gone in ("/__coder-ui/api/models", "/__coder-ui/api/models/upload", "/__coder-ui/api/isosx",
                     "/__coder-ui/api/safe"):
            self.assertFalse(self.safe.handle_libraries(h, "GET", gone, ""), gone)

    def test_without_the_cloud_drives_storage_it_says_so(self):
        self.safe.ENABLED = False
        sent = []
        h = mock.Mock()
        h.send_json = lambda status, body: sent.append((status, body))
        self.assertTrue(self.safe.handle_libraries(h, "GET", "/__coder-ui/api/isos", ""))
        self.assertEqual(sent, [(404, {"error": "The ISO library needs the Cloud Drive's storage (global.userStorage)."})])

    def test_an_upload_and_a_delete_go_straight_to_the_library(self):
        import io
        self.safe.ENABLED = True
        self.safe.CAN_EDIT = lambda user: True
        self.safe.bucket = lambda: "b"
        s3 = mock.Mock()
        self.safe.client = lambda: s3
        sent = []
        h = mock.Mock()
        h.headers = {"X-Requested-With": "coder-ui", "Content-Length": "4"}
        h.rfile = io.BytesIO(b"disk")
        h.user.return_value = ({"id": "u", "username": "dev", "roles": ["owner"]}, None)
        h.send_json = lambda status, body: sent.append((status, body))
        self.assertTrue(self.safe.handle_libraries(h, "PUT", "/__coder-ui/api/isos/upload", "name=disk.iso"))
        self.assertEqual(sent, [(200, {"ok": True, "name": "disk.iso", "size": 4})])
        self.assertEqual(s3.put_large.call_args[0][:2], ("b", "library/isos/disk.iso"))
        body = json.dumps({"name": "disk.iso"}).encode()
        h.headers = {"X-Requested-With": "coder-ui", "Content-Length": str(len(body))}
        h.rfile = io.BytesIO(body)
        self.assertTrue(self.safe.handle_libraries(h, "POST", "/__coder-ui/api/isos/delete", ""))
        self.assertEqual(sent[1], (200, {"ok": True}))
        s3.delete.assert_called_once_with("b", "library/isos/disk.iso")

    def test_only_editors_change_it(self):
        self.safe.ENABLED = True
        self.safe.CAN_EDIT = lambda user: "owner" in user["roles"]
        sent = []
        h = mock.Mock()
        h.headers = {"X-Requested-With": "coder-ui"}
        h.user.return_value = ({"id": "u", "username": "dev", "roles": []}, None)
        h.send_json = lambda status, body: sent.append(status)
        self.safe.handle_libraries(h, "POST", "/__coder-ui/api/isos/delete", "")
        self.assertEqual(sent, [403])


class ArchiveLayoutTest(unittest.TestCase):
    def test_an_index_of_platforms_not_all_held_points_at_the_one_held(self):
        import io as io_, tarfile, tempfile
        amd = b'{"schemaVersion":2,"config":{},"layers":[]}'
        amd_d = "sha256:" + __import__("hashlib").sha256(amd).hexdigest()
        lst = json.dumps({"schemaVersion": 2, "manifests": [
            {"digest": "sha256:" + "a" * 64, "platform": {"architecture": "arm64", "os": "linux"}},
            {"digest": amd_d, "platform": {"architecture": "amd64", "os": "linux"}, "size": len(amd)}]}).encode()
        lst_d = "sha256:" + __import__("hashlib").sha256(lst).hexdigest()
        index = json.dumps({"schemaVersion": 2, "manifests": [{
            "mediaType": "application/vnd.docker.distribution.manifest.list.v2+json", "digest": lst_d,
            "annotations": {"org.opencontainers.image.ref.name": "3.10.1"}}]}).encode()
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "a.tar")
            with tarfile.open(path, "w") as tf:
                for name, data in (("index.json", index), ("oci-layout", b'{"imageLayoutVersion":"1.0.0"}'),
                                   ("blobs/sha256/" + lst_d[7:], lst), ("blobs/sha256/" + amd_d[7:], amd)):
                    info = tarfile.TarInfo(name)
                    info.size = len(data)
                    tf.addfile(info, io_.BytesIO(data))
            self.assertTrue(ib.complete_layout(path))
            with tarfile.open(path) as tf:
                fixed = json.load(tf.extractfile("index.json"))
                self.assertEqual(len(tf.getnames()), 4)                    # every other file kept
            self.assertEqual(fixed["manifests"][0]["digest"], amd_d)
            self.assertEqual(fixed["manifests"][0]["annotations"]["org.opencontainers.image.ref.name"], "3.10.1")
            self.assertFalse(ib.complete_layout(path))                     # complete now: left alone


class CloudDriveQuotaTest(unittest.TestCase):
    """Cloud Drive > Settings: every account's capacity, as an admin sets it, over the chart's."""

    def setUp(self):
        import tempfile
        self.dir = tempfile.TemporaryDirectory()
        self.safe = load("safe", "safe.py")
        self.safe.LOG = lambda **kw: None
        self.safe.QUOTA_FILE = os.path.join(self.dir.name, "cloud-drive-quota.json")
        self.safe.QUOTA_BYTES = 10 * 1024 ** 3
        self.safe.IS_ADMIN = lambda user: "owner" in user["roles"]
        self.admin = {"id": "a", "username": "admin", "roles": ["owner"]}

    def tearDown(self):
        self.dir.cleanup()

    def test_an_admin_sets_it_and_goes_back_to_the_chart(self):
        self.assertEqual(self.safe.quota_bytes(), 10 * 1024 ** 3)
        view = self.safe.save_quota(self.admin, {"quotaGiB": 250})
        self.assertEqual((view["gib"], view["chartGib"], view["custom"]), (250, 10, True))
        self.assertEqual(self.safe.quota_bytes(), 250 * 1024 ** 3)
        self.assertEqual(self.safe.capacity(), "250Gi")
        view = self.safe.save_quota(self.admin, {"reset": True})
        self.assertEqual((view["gib"], view["custom"]), (10, False))

    def test_only_admins_and_only_sensible_values(self):
        with self.assertRaises(PermissionError):
            self.safe.save_quota({"id": "u", "username": "dev", "roles": []}, {"quotaGiB": 5})
        for bad in (0, -1, 10241, 2.5, "50", True, None):
            with self.assertRaises(ValueError, msg=repr(bad)):
                self.safe.save_quota(self.admin, {"quotaGiB": bad})
        self.assertEqual(self.safe.save_quota(self.admin, {"quotaGiB": 40.0})["gib"], 40)


class CloudDriveConnectionsTest(unittest.TestCase):
    """The owner connects and disconnects their workspaces from their Cloud Drive (safe.py): kept in LINKS_FILE, set
    at once on the running pod's annotation (the workspace links ~/cloud-drive by it), and kept so by the watcher."""

    OWNER = {"id": "3f0c2a52-7d7e-4c5b-9a3e-2f4b8c1d0e9a", "username": "ann", "roles": []}
    WS = "11111111-2222-4333-8444-555555555555"
    OLD = "66666666-7777-4888-9999-aaaaaaaaaaaa"

    class Kube:
        ok = True

        def __init__(self, pods):
            self.pods = pods
            self.patches = []
            self.state = None  # the ConfigMap the templates read

        def call(self, method, path, body=None, patch_type=None):
            if "/configmaps" in path:
                if method == "GET":
                    return (404, {}) if self.state is None else (200, {"metadata": {"resourceVersion": "1"},
                                                                       "data": {"state": self.state}})
                self.state = body["data"]["state"]
                return 200, body
            if method == "GET":
                sel = urllib.parse.unquote(path.split("labelSelector=", 1)[1]) if "labelSelector=" in path else ""
                want = sel.split("=", 1)[1] if "=" in sel else None
                items = [p for p in self.pods
                         if want is None or p["metadata"]["labels"]["com.coder.workspace.id"] == want]
                return 200, {"items": items}
            if method == "PATCH":
                name = path.rsplit("/", 1)[1]
                self.patches.append((name, body["metadata"]["annotations"]))
                for p in self.pods:
                    if p["metadata"]["name"] == name:
                        p["metadata"].setdefault("annotations", {}).update(body["metadata"]["annotations"])
                return 200, {}
            raise AssertionError(method)

    @staticmethod
    def pod(name, wid, owner, annotation="on"):
        return {"metadata": {"name": name, "labels": {"com.coder.workspace.id": wid, "com.coder.user.id": owner},
                             "annotations": {} if annotation is None else {"platform.coder/cloud-drive": annotation}},
                "status": {"phase": "Running"}}

    def setUp(self):
        import tempfile
        self.dir = tempfile.TemporaryDirectory()
        self.safe = load("safe", "safe.py")
        self.safe.LOG = lambda **kw: None
        self.safe.LINKS_FILE = os.path.join(self.dir.name, "cloud-drive-links.json")
        self.kube = self.Kube([self.pod("coder-ws-1", self.WS, self.OWNER["id"]),
                               self.pod("coder-old-1", self.OLD, self.OWNER["id"], annotation=None),
                               self.pod("coder-bob-1", "bbbbbbbb-2222-4333-8444-555555555555", "bob")])
        self.safe.KUBE = self.kube
        owners = {self.WS: self.OWNER["id"], self.OLD: self.OWNER["id"]}
        self.safe.WORKSPACE_INFO = lambda token, wid: (
            {"id": wid, "name": "dev", "owner_id": owners[wid]} if wid in owners else None)
        self.h = mock.Mock()
        self.h.token.return_value = "t"
        # the workspace's Cloud Drive script, by exec: (pod, command) of each call; its answer
        self.execs, self.answer = [], (0, "attached\n", "")
        self.safe.kexec = mock.Mock()
        self.safe.kexec.exec_in.side_effect = lambda kube, ns, pod, container, cmd, **kw: (
            self.execs.append((pod, cmd)) or self.answer)

    def tearDown(self):
        self.dir.cleanup()

    def test_disconnect_and_connect_again_at_once_in_the_running_pod(self):
        self.assertEqual(self.safe.connections(self.OWNER),
                         {"disconnected": [], "older": [self.OLD], "shared": [], "sharedWithVolume": [],
                          "encrypted": False, "restart": [], "source": None, "attachment": {}})
        out = self.safe.set_connected(self.h, self.OWNER, self.WS, {"connected": False})
        self.assertEqual((out["disconnected"], out["applied"]), ([self.WS], True))
        self.assertEqual(self.kube.patches, [("coder-ws-1", {"platform.coder/cloud-drive": "off"})])
        out = self.safe.set_connected(self.h, self.OWNER, self.WS, {"connected": True})
        self.assertEqual(out["disconnected"], [])
        self.assertEqual(self.kube.patches[-1], ("coder-ws-1", {"platform.coder/cloud-drive": "on"}))

    def test_the_workspace_is_told_at_once_and_says_how_its_drive_is(self):
        # the kubelet may take a minute to update the annotation's file in the pod: the workspace is told by exec
        self.answer = (0, "detached\n", "")
        self.safe.set_connected(self.h, self.OWNER, self.WS, {"connected": False})
        self.assertEqual(self.execs, [("coder-ws-1", [self.safe.PUSH_HELPER, "push", "off"])])
        # its answer stands over an older reading of its agent's metadata, a newer reading over it
        t = self.safe.time.time()
        self.safe.ATTACHMENT = lambda owner: [(self.WS, "attached", t - 5, "succeeded")]
        self.assertEqual(self.safe.connections(self.OWNER)["attachment"], {self.WS: "detached"})
        self.safe.ATTACHMENT = lambda owner: [(self.WS, "waiting", t + 5, "succeeded")]
        self.assertEqual(self.safe.attachment(self.OWNER["id"]), {self.WS: "waiting"})

    def test_attachment_words(self):
        t = self.safe.time.time()
        rows = [("a", "attached", t - 1, "succeeded"),  # its agent's word
                ("b", "", -62135596800.0, "succeeded"),  # never collected yet (0001-01-01)
                ("c", "attached", t - 120, "succeeded"),  # its agent went quiet
                ("d", "attached", t - 1, "running"),  # its build still runs: a reading of the previous start
                ("e", "unanswered extra", t - 1, "succeeded")]
        self.safe.ATTACHMENT = lambda owner: rows
        self.assertEqual(self.safe.attachment(self.OWNER["id"]),
                         {"a": "attached", "b": "starting", "c": "stale", "d": "starting", "e": "unanswered"})
        # Coder's database not readable: nothing, no error
        self.safe.ATTACHMENT = lambda owner: (_ for _ in ()).throw(OSError("down"))
        self.assertEqual(self.safe.attachment(self.OWNER["id"]), {})

    def test_a_workspace_without_its_script_yet_is_told_again(self):
        self.safe.PUSH_RETRY = 0.01
        self.answer = (126, "", "no such file")
        out = self.safe.set_connected(self.h, self.OWNER, self.WS, {"connected": False})
        self.assertTrue(out["applied"])
        for _ in range(200):
            if len(self.execs) >= 3:
                break
            self.safe.time.sleep(0.01)
        self.answer = (0, "detached\n", "")
        for _ in range(200):
            if self.WS in self.safe._attach_seen:
                break
            self.safe.time.sleep(0.01)
        self.assertEqual(self.safe._attach_seen[self.WS][0], "detached")
        self.assertTrue(all(cmd == [self.safe.PUSH_HELPER, "push", "off"] for _, cmd in self.execs))
        # a newer state: the retries of the older one give way
        self.safe._wanted["coder-ws-1"] = "on"
        n = len(self.execs)
        self.safe.push_soon("coder-ws-1", self.WS, "off")
        self.safe.time.sleep(0.1)
        self.assertEqual(len(self.execs), n)

    def test_a_restarted_workspace_gets_its_disconnection_back(self):
        self.safe.set_connected(self.h, self.OWNER, self.WS, {"connected": False})
        # a new pod starts "on", from its template
        self.kube.pods[0] = self.pod("coder-ws-2", self.WS, self.OWNER["id"])
        self.kube.patches.clear()
        self.safe.links_once()
        self.assertEqual(self.kube.patches, [("coder-ws-2", {"platform.coder/cloud-drive": "off"})])
        self.safe.links_once()  # nothing more to do
        self.assertEqual(len(self.kube.patches), 1)

    def test_an_older_pod_is_recorded_not_patched(self):
        out = self.safe.set_connected(self.h, self.OWNER, self.OLD, {"connected": False})
        self.assertEqual((out["disconnected"], out["applied"], out["older"]), ([self.OLD], False, [self.OLD]))
        self.assertEqual(self.kube.patches, [])

    def test_only_the_owner_and_only_a_workspace(self):
        with self.assertRaises(PermissionError):
            self.safe.set_connected(self.h, {"id": "bob", "username": "bob", "roles": []}, self.WS,
                                    {"connected": False})
        for wid, body in (("../etc", {"connected": False}), (self.WS, {"connected": "no"}),
                          ("99999999-2222-4333-8444-555555555555", {"connected": False})):
            with self.assertRaises(ValueError, msg=wid):
                self.safe.set_connected(self.h, self.OWNER, wid, body)
        self.assertEqual(self.kube.patches, [])
        # another user's disconnections are not listed
        self.assertEqual(self.safe.connections({"id": "bob", "username": "bob"})["disconnected"], [])

    def test_a_shared_workspace_never_has_the_owners_drive(self):
        self.safe.SHARED = lambda: {self.WS: self.OWNER["id"]}
        self.safe.links_once()
        self.assertEqual(self.kube.patches, [("coder-ws-1", {"platform.coder/cloud-drive": "off"})])
        # the templates read it: from its next start it mounts none
        self.assertEqual(json.loads(self.kube.state)["shared"], [self.WS])
        with self.assertRaises(ValueError):
            self.safe.set_connected(self.h, self.OWNER, self.WS, {"connected": True})
        self.assertEqual(self.safe.connections(self.OWNER)["shared"], [self.WS])
        # no longer shared: connected again (it was never disconnected by its owner)
        self.safe.SHARED = lambda: {}
        self.safe._shared["at"] = 0
        self.safe.links_once()
        self.assertEqual(self.kube.patches[-1], ("coder-ws-1", {"platform.coder/cloud-drive": "on"}))
        self.assertEqual(json.loads(self.kube.state)["shared"], [])


class KitCryptoTest(unittest.TestCase):
    """The recovery kit's wrapping (kitcrypto.py): AES-256 as FIPS-197 has it, and what openssl opens."""

    def setUp(self):
        self.kc = load("kitcrypto", "kitcrypto.py")

    def test_aes256_known_answer(self):
        rounds = self.kc._expand(bytes(range(32)))
        self.assertEqual(self.kc._encrypt_block(rounds, bytes.fromhex("00112233445566778899aabbccddeeff")).hex(),
                         "8ea2b7ca516745bfeafc49904b496089")

    def test_openssl_opens_the_password_and_certificate_wraps(self):
        import shutil, subprocess, tempfile
        if not shutil.which("openssl"):
            self.skipTest("no openssl")
        key = os.urandom(32)
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "k.enc"), "w") as f:
                f.write(self.kc.openssl_enc(key, "correct horse battery", iterations=1000))
            out = subprocess.run(["openssl", "enc", "-d", "-aes-256-cbc", "-pbkdf2", "-md", "sha256", "-iter", "1000",
                                  "-a", "-in", os.path.join(d, "k.enc"), "-pass", "pass:correct horse battery"],
                                 capture_output=True, check=True).stdout
            self.assertEqual(out, key)
            subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout",
                            os.path.join(d, "key.pem"), "-out", os.path.join(d, "cert.pem"), "-days", "1", "-subj",
                            "/CN=jdoe"], capture_output=True, check=True)
            with open(os.path.join(d, "cert.pem"), "rb") as f:
                cert = self.kc.certificate(f.read())
            self.assertEqual((cert["subject"], cert["key"]["bits"]), ("CN=jdoe", 2048))
            with open(os.path.join(d, "w.bin"), "wb") as f:
                f.write(self.kc.rsa_oaep_encrypt(cert["key"], key))
            out = subprocess.run(["openssl", "pkeyutl", "-decrypt", "-inkey", os.path.join(d, "key.pem"), "-pkeyopt",
                                  "rsa_padding_mode:oaep", "-pkeyopt", "rsa_oaep_md:sha256", "-pkeyopt",
                                  "rsa_mgf1_md:sha256", "-in", os.path.join(d, "w.bin")],
                                 capture_output=True, check=True).stdout
            self.assertEqual(out, key)
            with open(os.path.join(d, "key.pem"), "rb") as f:
                with self.assertRaises(ValueError):  # never a private key
                    self.kc.certificate(f.read())
        with self.assertRaises(ValueError):
            self.kc.certificate("not a certificate")


class CloudDriveEncryptionTest(unittest.TestCase):
    """An encrypted Cloud Drive (safe.py): its volume mounts with the key over TLS; the key is turned on only after the
    recovery kit is downloaded, and only while none of the owner's workspaces has the drive."""

    UID = "3f0c2a52-7d7e-4c5b-9a3e-2f4b8c1d0e9a"
    USER = {"id": UID, "username": "ann", "roles": []}

    def setUp(self):
        import tempfile
        self.dir = tempfile.TemporaryDirectory()
        self.safe = load("safe", "safe.py")
        self.safe.LOG = lambda **kw: None
        self.safe.ENCRYPTION_FILE = os.path.join(self.dir.name, "enc.json")
        self.safe.SOURCES_FILE = os.path.join(self.dir.name, "src.json")
        self.safe.SETTINGS_FILE = os.path.join(self.dir.name, "none.json")
        self.safe.NODE_SERVICE = "coder-user-storage-minio"
        self.safe.NODE_TLS_PORT = 9443
        self.safe.S3_ENDPOINT = "https://coder-user-storage-minio.coder.svc:9443"
        self.secrets = {}
        self.pods = []
        test = self

        class Kube:
            ok = True

            def call(self, method, path, body=None, patch_type=None):
                name = path.split("?")[0].rsplit("/", 1)[-1]
                if "/secrets" in path:
                    if method == "GET":
                        return (200, {"metadata": {"resourceVersion": "1"}, "data": test.secrets[name]}) \
                            if name in test.secrets else (404, {})
                    test.secrets[body["metadata"]["name"]] = body["data"]
                    return 201, body
                if "/pods" in path:
                    return 200, {"items": test.pods}
                return 404, {}

        self.safe.KUBE = Kube()
        s3 = mock.Mock()
        s3.list.return_value = ([{"key": "users/%s/a.txt" % self.UID, "size": 5, "modified": "t"}], [])
        self.client = mock.patch.object(self.safe, "client", return_value=s3)
        self.client.start()

    def tearDown(self):
        self.client.stop()
        self.dir.cleanup()

    def test_the_kit_comes_first_and_running_workspaces_stop_it(self):
        with self.assertRaises(ValueError):
            self.safe.prepare_encryption(self.USER, {"method": "password", "password": "short"})
        out = self.safe.prepare_encryption(self.USER, {"method": "password", "password": "a long enough password"})
        kit = out["kit"]
        self.assertEqual((kit["account"]["username"], kit["storage"]["bucket"], kit["storage"]["prefix"],
                          kit["storage"]["objects"], kit["storage"]["bytes"], kit["key"]["method"]),
                         ("ann", "coder-user-storage", "users/%s/" % self.UID, 1, 5, "password"))
        self.assertTrue(kit["key"]["ciphertext"].startswith("U2FsdGVkX1"))  # openssl's "Salted__"
        self.assertNotIn("a long enough password", json.dumps(kit))
        self.assertFalse(self.safe.encryption_on(self.UID))  # nothing changed yet
        with self.assertRaises(ValueError):  # the kit must be saved
            self.safe.enable_encryption(self.USER, {"keyId": out["keyId"]})
        self.pods = [{"metadata": {"labels": {"com.coder.workspace.id": "w1"}}, "status": {"phase": "Running"},
                      "spec": {"volumes": [{"persistentVolumeClaim": {"claimName": "coder-safe-" + self.UID}}]}}]
        with self.assertRaises(ValueError):  # a workspace has the drive
            self.safe.enable_encryption(self.USER, {"keyId": out["keyId"], "kitSaved": True})
        self.assertNotIn("coder-cloud-drive-keys", self.secrets)

    def test_an_encrypted_volume_mounts_with_its_key_over_tls(self):
        import base64
        key = os.urandom(32)
        self.safe.store_key(self.UID, key)
        self.safe._write_json(self.safe.ENCRYPTION_FILE, {self.UID: {"state": "on", "method": "password"}})
        pv, _ = self.safe.volume_objects(self.UID, "ann")
        self.assertEqual(pv["spec"]["csi"]["nodePublishSecretRef"]["name"], "coder-user-storage-mount-tls")
        opts = pv["spec"]["csi"]["volumeAttributes"]["options"]
        self.assertIn("--sse-c " + base64.b64encode(key).decode(), opts)
        self.assertIn("--no-verify-ssl", opts)  # the chart's MinIO by its ClusterIP
        # a plain one keeps the plain Secret and options
        self.safe._write_json(self.safe.ENCRYPTION_FILE, {})
        pv, _ = self.safe.volume_objects(self.UID, "ann")
        self.assertEqual((pv["spec"]["csi"]["nodePublishSecretRef"]["name"],
                          pv["spec"]["csi"]["volumeAttributes"]["options"]),
                         ("coder-user-storage-mount", self.safe.MOUNT_OPTIONS))

    def test_no_encryption_over_plain_http(self):
        self.safe.S3_ENDPOINT = "http://coder-user-storage-minio.coder.svc:9000"
        self.assertFalse(self.safe.tls_available()[0])
        with self.assertRaises(ValueError):
            self.safe.prepare_encryption(self.USER, {"method": "password", "password": "a long enough password"})

    def test_turning_it_off_needs_the_password(self):
        self.safe._write_json(self.safe.ENCRYPTION_FILE, {self.UID: {
            "state": "on", "method": "password", "verifier": self.safe._verifier("the right password", iterations=1000)}})
        with self.assertRaises(PermissionError):
            self.safe.disable_encryption(self.USER, {"password": "a wrong password!"})
        self.assertTrue(self.safe._password_ok(self.safe.encryption_records()[self.UID], "the right password"))


class DriveSourceTest(unittest.TestCase):
    """A workspace folder served as the owner's Cloud Drive (drivesource.py): signed requests only, the owner's prefix
    only, never outside the folder; what the platform's client does works."""

    def setUp(self):
        import socket, tempfile
        self.dir = tempfile.TemporaryDirectory()
        root = os.path.join(self.dir.name, "root")
        os.makedirs(os.path.join(root, "sub"))
        with open(os.path.join(root, "a.txt"), "w") as f:
            f.write("hello")
        os.symlink("/etc", os.path.join(root, "escape"))
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        cfg = os.path.join(self.dir.name, "config.json")
        with open(cfg, "w") as f:
            json.dump({"root": root, "bucket": "drive", "prefix": "u1/", "accessKey": "AK", "secretKey": "SK",
                       "port": port, "id": "c1"}, f)
        self.ds = load("drivesource", "drivesource.py")
        self.ds.load(cfg)
        self.server = self.ds.Server(("127.0.0.1", port), self.ds.Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.s3c = load("s3client", "s3client.py")
        self.s3 = self.s3c.S3("http://127.0.0.1:%d" % port, "", "AK", "SK", timeout=10)
        self.root = root

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.dir.cleanup()

    def test_the_folder_as_s3(self):
        import io
        objects, prefixes = self.s3.list("drive", prefix="u1/", delimiter="/")
        self.assertEqual(([o["key"] for o in objects], prefixes), (["u1/a.txt"], ["u1/sub/"]))  # no link
        self.s3.put("drive", "u1/sub/new file.txt", b"data")
        with open(os.path.join(self.root, "sub", "new file.txt"), "rb") as f:
            self.assertEqual(f.read(), b"data")
        data = os.urandom(3 * 1024 * 1024 + 7)
        self.s3.put_large("drive", "u1/big.bin", io.BytesIO(data), len(data), part_size=1024 * 1024)
        self.s3.copy("drive", "u1/big.bin", "u1/copy.bin", len(data))
        r = self.s3.get("drive", "u1/copy.bin")
        self.assertEqual(r.read(), data)
        r.close()
        r.conn.close()
        self.assertEqual(self.s3.delete_many("drive", ["u1/copy.bin", "u1/sub/new file.txt", "u1/sub/"]), [])
        self.assertFalse(os.path.exists(os.path.join(self.root, "sub")))

    def test_nothing_outside(self):
        for key in ("u1/escape/passwd", "u1/../a.txt", "u2/a.txt"):
            with self.assertRaises(self.s3c.S3Error, msg=key) as e:
                self.s3.get("drive", key)
            self.assertIn(e.exception.status, (400, 403, 404))
        bad = self.s3c.S3(self.s3.scheme + "://" + self.s3.netloc, "", "AK", "wrong", timeout=10)
        with self.assertRaises(self.s3c.S3Error) as e:
            bad.list("drive", prefix="u1/")
        self.assertEqual(e.exception.code, "SignatureDoesNotMatch")


class CloudDriveSourceTest(unittest.TestCase):
    """A workspace folder as the Cloud Drive (safe.py): only a folder of the home, and deleting the source workspace
    needs each connected workspace confirmed and "delete" typed."""

    UID = "3f0c2a52-7d7e-4c5b-9a3e-2f4b8c1d0e9a"
    USER = {"id": UID, "username": "ann", "roles": []}
    SRC = "11111111-2222-4333-8444-555555555555"
    OTHER = "22222222-2222-4333-8444-555555555555"

    def setUp(self):
        import tempfile
        self.dir = tempfile.TemporaryDirectory()
        self.safe = load("safe", "safe.py")
        self.safe.LOG = lambda **kw: None
        self.safe.SOURCES_FILE = os.path.join(self.dir.name, "src.json")
        self.safe.LINKS_FILE = os.path.join(self.dir.name, "links.json")
        self.safe._write_json(self.safe.SOURCES_FILE, {self.UID: {
            "owner": self.UID, "workspace": self.SRC, "name": "src", "path": "/home/coder/data",
            "accessKey": "a", "secretKey": "s"}})
        self.safe.WORKSPACES = lambda owner: [{"id": self.SRC, "name": "src"}, {"id": self.OTHER, "name": "other"}]

        class Kube:
            ok = True

            def call(self, method, path, body=None, patch_type=None):
                if method == "GET" and "/pods" in path:
                    return 200, {"items": []}
                return 404, {}

        self.safe.KUBE = Kube()

    def tearDown(self):
        self.dir.cleanup()

    def test_workspaces_whose_volume_is_not_the_current_one(self):
        def pod(wid, claim):
            return {"metadata": {"name": "p-" + wid, "labels": {"com.coder.workspace.id": wid},
                                 "annotations": {"platform.coder/cloud-drive": "on"}},
                    "status": {"phase": "Running"},
                    "spec": {"volumes": [{"persistentVolumeClaim": {"claimName": claim}}] if claim else []}}
        pods = [pod(self.SRC, None), pod(self.OTHER, "coder-safe-" + self.UID),
                pod("33333333-2222-4333-8444-555555555555", "coder-drive-src-" + self.UID)]
        self.safe.KUBE.call = lambda method, path, body=None, patch_type=None: (200, {"items": pods})
        src = self.safe.sources()[self.UID]
        self.safe._sources_seen[self.UID] = {"running": True}
        # the source runs: the one still on S3 needs a restart; the source itself and the one on the source do not
        self.assertEqual(self.safe.stale_mounts(self.UID, src, set()), [self.OTHER])
        # no source any more: the one on the source's volume needs a restart
        self.assertEqual(self.safe.stale_mounts(self.UID, None, set()),
                         sorted([self.SRC, "33333333-2222-4333-8444-555555555555"]))

    def test_only_a_folder_of_the_home(self):
        c = self.safe.clean_source_path
        self.assertEqual(c("data/x"), "/home/coder/data/x")
        self.assertEqual(c("~/data"), "/home/coder/data")
        for bad in ("/home/coder", "/etc", "/home/coder/../etc", "~/.ssh", "/home/coder/cloud-drive",
                    "/home/coder/cloud-drive/x"):
            with self.assertRaises(ValueError, msg=bad):
                c(bad)

    def test_deleting_the_source_is_guarded(self):
        guard = self.safe.source_guard(self.USER, self.SRC)
        self.assertEqual((guard["source"], [w["name"] for w in guard["connected"]]), (True, ["other"]))
        self.assertFalse(self.safe.source_guard(self.USER, self.OTHER)["source"])
        with self.assertRaises(ValueError):  # each connected workspace confirmed
            self.safe.release_source(self.USER, {"workspace": self.SRC, "disconnect": [], "confirm": "delete"})
        with self.assertRaises(ValueError):  # and "delete" typed
            self.safe.release_source(self.USER, {"workspace": self.SRC, "disconnect": [self.OTHER], "confirm": "del"})
        self.safe.release_source(self.USER, {"workspace": self.SRC, "disconnect": [self.OTHER], "confirm": "delete"})
        self.assertEqual(self.safe.sources(), {})
        self.assertIn(self.OTHER, self.safe.disconnected())


class CloudDriveBackendTest(unittest.TestCase):
    """Cloud Drive > Settings (safe.py): the backend admins set over the chart's, its check, and volumes following a
    new bucket once nothing uses them."""

    UID = "3f0c2a52-7d7e-4c5b-9a3e-2f4b8c1d0e9a"

    def setUp(self):
        import tempfile
        self.dir = tempfile.TemporaryDirectory()
        self.safe = load("safe", "safe.py")
        self.safe.LOG = lambda **kw: None
        self.safe.SETTINGS_FILE = os.path.join(self.dir.name, "cloud-drive.json")
        self.safe.IS_ADMIN = lambda user: "owner" in user["roles"]
        self.env = mock.patch.dict(os.environ, {"S3_ACCESS_KEY": "chartkey", "S3_SECRET_KEY": "chartsecret",
                                                "SAFE_BUCKET": "coder-user-storage"})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.dir.cleanup()

    def test_validation(self):
        good = {"endpoint": "https://s3.us-east-1.amazonaws.com", "region": "us-east-1", "bucket": "team-drive",
                "accessKey": "AK", "secretKey": "SK"}
        self.assertEqual(self.safe.clean_backend(good)["bucket"], "team-drive")
        for change in ({"endpoint": "s3.amazonaws.com"}, {"bucket": "Bad_Bucket"}, {"region": "US East"},
                       {"secretKey": ""}):
            with self.assertRaises(ValueError, msg=change):
                self.safe.clean_backend(dict(good, **change))
        self.assertEqual(self.safe.clean_backend(dict(good, secretKey=""), keep_secret="old")["secretKey"], "old")

    def test_only_admins_and_never_an_unreachable_one(self):
        admin, dev = {"username": "a", "roles": ["owner"]}, {"username": "d", "roles": []}
        body = {"endpoint": "http://minio.example:9000", "bucket": "team-drive", "accessKey": "AK", "secretKey": "SK"}
        with self.assertRaises(PermissionError):
            self.safe.save_settings(dev, body)
        with mock.patch.object(self.safe, "check", return_value={"connected": False, "error": "refused"}):
            with self.assertRaisesRegex(ValueError, "could not be reached"):
                self.safe.save_settings(admin, body)
        self.assertEqual(self.safe.backend()["source"], "chart")
        s3 = mock.Mock()
        with mock.patch.object(self.safe, "check", return_value={"connected": True, "error": None, "bucketExists": False}), \
                mock.patch.object(self.safe, "client", return_value=s3):
            view = self.safe.save_settings(admin, body)
        s3.create_bucket.assert_called_with("team-drive")
        self.assertEqual((view["source"], view["bucket"], self.safe.backend()["secretKey"]), ("custom", "team-drive", "SK"))
        self.assertEqual(oct(os.stat(self.safe.SETTINGS_FILE).st_mode & 0o777), "0o600")
        self.assertEqual(self.safe.settings_view(dev)["accessKey"], "")                # keys only for admins
        with mock.patch.object(self.safe, "check", return_value={"connected": True}):
            self.safe.save_settings(admin, {"reset": True})
        self.assertEqual(self.safe.backend()["bucket"], "coder-user-storage")

    def test_a_new_bucket_takes_volumes_over_once_unused(self):
        calls = []
        old = {"spec": {"csi": {"volumeHandle": "coder-user-storage/users/" + self.UID}}}

        class Kube:
            ok = True

            def call(self, method, path, body=None, patch_type=None):
                calls.append((method, path))
                if method == "GET" and path.startswith("/api/v1/persistentvolumes/"):
                    return (200, old) if not any(m == "DELETE" for m, _ in calls) else (404, {})
                if method == "GET":
                    return 404, {}
                return 201, body or {}

        self.safe.KUBE = Kube()
        with open(self.safe.SETTINGS_FILE, "w") as f:
            json.dump({"endpoint": "http://x:9000", "bucket": "team-drive", "accessKey": "a", "secretKey": "b"}, f)
        name = "coder-safe-" + self.UID
        self.assertFalse(self.safe.ensure_volume(self.UID, "alice", in_use={name}))   # its workspace runs: later
        self.assertFalse(any(m == "DELETE" for m, _ in calls))
        with mock.patch.object(self.safe.time, "sleep"):
            self.assertTrue(self.safe.ensure_volume(self.UID, "alice", in_use=set()))
        self.assertIn(("DELETE", "/api/v1/persistentvolumes/" + name), calls)
        posted = [p for m, p in calls if m == "POST"]
        self.assertEqual(posted, ["/api/v1/persistentvolumes", "/api/v1/namespaces/coder/persistentvolumeclaims"])


class CloudDriveMountTest(unittest.TestCase):
    """The Cloud Drive mounted into a folder of a workspace (clouddrive_cli.py, the workspace's `cloud-drive`): links
    beside the folder's files, folders merged, collisions halted and resolved as the owner chooses."""

    def setUp(self):
        import tempfile
        self.home = tempfile.mkdtemp()
        self.cli = load("clouddrive_cli", "clouddrive_cli.py")
        c = self.cli
        c.HOME = self.home
        c.CLOUD = os.path.join(self.home, "cloud-drive")
        c.STATE = os.path.join(self.home, ".config", "cloud-drive", "mounts.json")
        c.OVERWRITTEN = os.path.join(self.home, ".local", "share", "cloud-drive", "overwritten")
        drive = os.path.join(self.home, "drive")
        for rel, text in (("data.csv", "cloud"), ("notes.txt", "cloud"), ("new.txt", "c"), ("src/main.py", "c"),
                          ("src/util.py", "c"), ("docs/a.md", "d")):
            self.write(os.path.join(drive, rel), text)
        os.symlink(drive, c.CLOUD)
        for rel in ("data.csv", "notes.txt", "src/main.py", "keep.me"):
            self.write(os.path.join(self.home, "data", rel), "mine")
        self.target = os.path.join(self.home, "data")

    def write(self, path, text):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)

    def read(self, rel):
        with open(os.path.join(self.target, rel)) as f:
            return f.read()

    def test_names_kept_both(self):
        r = self.cli.renamed
        self.assertEqual([r("data.csv"), r("archive.tar.gz"), r(".bashrc"), r("Makefile"), r("src"),
                          r("data.csv", 2)],
                         ["data-(cloud_drive).csv", "archive-(cloud_drive).tar.gz", ".bashrc-(cloud_drive)",
                          "Makefile-(cloud_drive)", "src-(cloud_drive)", "data-(cloud_drive)-2.csv"])

    def test_paths(self):
        self.assertEqual(self.cli.resolve("data"), self.target)
        self.assertEqual(self.cli.resolve("~/data"), self.target)
        self.assertEqual(self.cli.resolve(self.target + "/"), self.target)
        for bad in ("/etc", "/", "cloud-drive", "cloud-drive/x", "", "data/keep.me", "/proc/1"):
            with self.assertRaises(ValueError, msg=bad):
                self.cli.resolve(bad)

    def test_the_plan_halts_on_files_of_the_same_name_and_merges_folders(self):
        p = self.cli.plan(self.target)
        self.assertEqual(sorted(x["path"] for x in p["links"]), ["docs", "new.txt", "src/util.py"])
        self.assertEqual([c["path"] for c in p["collisions"]], ["data.csv", "notes.txt", "src/main.py"])
        self.assertEqual(p["merged"], ["src"])
        self.assertEqual(p["collisions"][2]["keepBothAs"], "src/main-(cloud_drive).py")
        with self.assertRaises(self.cli.Halt):  # "ask" with an answer missing changes nothing
            self.cli.apply(self.target, "ask", {"data.csv": "overwrite"})
        self.assertFalse(os.path.islink(os.path.join(self.target, "docs")))

    def test_keep_both(self):
        r = self.cli.apply(self.target, "keep-both")
        self.assertEqual((r["linked"], len(r["keptBoth"]), r["overwritten"]), (3, 3, []))
        self.assertEqual(self.read("data.csv"), "mine")
        self.assertEqual(self.read("data-(cloud_drive).csv"), "cloud")
        self.assertEqual(self.read("docs/a.md"), "d")  # through the link
        self.assertEqual(self.read("keep.me"), "mine")

    def test_overwrite_all_and_unmount_puts_them_back(self):
        r = self.cli.apply(self.target, "overwrite-all")
        self.assertEqual(sorted(r["overwritten"]), ["data.csv", "notes.txt", "src/main.py"])
        self.assertEqual(self.read("data.csv"), "cloud")
        self.assertTrue(os.path.exists(os.path.join(r["movedTo"], "data", "data.csv")))
        u = self.cli.unmount(self.target)
        self.assertEqual((u["removed"], u["restored"]), (6, 3))
        self.assertEqual(self.read("data.csv"), "mine")
        self.assertEqual(sorted(os.listdir(self.target)), ["data.csv", "keep.me", "notes.txt", "src"])
        self.assertEqual(os.listdir(os.path.join(self.target, "src")), ["main.py"])

    def test_each_occurrence(self):
        self.cli.apply(self.target, "ask", {"data.csv": "overwrite", "notes.txt": "keep", "src/main.py": "keep"})
        self.assertEqual(self.read("data.csv"), "cloud")
        self.assertEqual((self.read("notes.txt"), self.read("notes-(cloud_drive).txt")), ("mine", "cloud"))
        self.assertEqual(self.read("src/main-(cloud_drive).py"), "c")

    def test_sync_follows_the_cloud_drive_as_the_mount_was_made(self):
        self.cli.apply(self.target, "ask", {"data.csv": "keep", "notes.txt": "keep", "src/main.py": "keep"})
        drive = os.path.join(self.home, "drive")
        self.write(os.path.join(drive, "later.txt"), "c")
        self.write(os.path.join(self.target, "later.txt"), "mine")
        os.remove(os.path.join(drive, "new.txt"))
        self.cli.sync()
        self.assertFalse(os.path.lexists(os.path.join(self.target, "new.txt")))  # gone from the Cloud Drive
        self.assertEqual(self.read("later-(cloud_drive).txt"), "c")  # kept both: nobody to ask
        self.assertEqual(self.cli.listing()["mounts"][0]["mode"], "ask")  # how it was made stays
        # the Cloud Drive away (disconnected): nothing is touched
        os.remove(self.cli.CLOUD)
        self.assertEqual(self.cli.sync(), [])
        self.assertTrue(os.path.islink(os.path.join(self.target, "docs")))

    def test_the_service_runs_it_in_the_owners_running_workspace(self):
        safe = load("safe", "safe.py")
        safe.LOG = lambda **kw: None
        safe.KUBE = mock.Mock()
        wid = "11111111-2222-3333-4444-555555555555"
        safe.WORKSPACE_INFO = lambda token, w: {"id": w, "owner_id": "u1", "name": "dev"}
        safe.shared_now = lambda: {}
        safe._source_pod = lambda w: ("ws-pod", "10.0.0.1")
        calls = []

        def exec_in(kube, ns, pod, container, command, timeout=0, stdin=b""):
            calls.append((pod, container, command, stdin))
            return 0, json.dumps({"halted": True, "unresolved": ["a.txt"]}), ""
        safe.kexec = mock.Mock(exec_in=exec_in)
        h = mock.Mock()
        user = {"id": "u1", "username": "dev"}
        r = safe.mount_apply(h, user, {"workspace": wid, "path": "data", "mode": "ask",
                                        "decisions": {"b.txt": "overwrite"}})
        self.assertTrue(r["halted"])
        pod, container, command, stdin = calls[0]
        self.assertEqual((pod, container), ("ws-pod", "dev"))
        self.assertEqual(command[:2], ["python3", "-c"])
        self.assertEqual(command[4:], ["apply", "data", "--mode", "ask",
                                       "--decisions=-:%d" % len(json.dumps({"b.txt": "overwrite"})), "--json"])
        n = int(command[3])
        self.assertTrue(stdin[:n].startswith(b"#!/usr/bin/env python3"))  # the command, then the answers
        self.assertEqual(json.loads(stdin[n:]), {"b.txt": "overwrite"})
        for body in ({"workspace": wid, "path": "data", "mode": "nope"},
                     {"workspace": wid, "path": "data", "mode": "ask", "decisions": {"x": "delete"}},
                     {"workspace": "x", "path": "data", "mode": "ask"},
                     {"workspace": wid, "path": "a\x00b", "mode": "ask"}):
            with self.assertRaises(ValueError, msg=body):
                safe.mount_apply(h, user, body)
        with self.assertRaises(PermissionError):
            safe.mount_apply(h, {"id": "u2", "username": "other"}, {"workspace": wid, "path": "d", "mode": "ask"})
        safe.shared_now = lambda: {wid: "u1"}
        with self.assertRaises(ValueError):
            safe.mount_plan(h, user, {"workspace": wid, "path": "data"})

    def test_the_boot_runs_the_command_with_its_arguments(self):
        """CLI_BOOT, as the service sends it: the command's code on stdin, its exit code kept."""
        import subprocess
        safe = load("safe", "safe.py")
        code = safe._cli_text()
        env = dict(os.environ, HOME=self.home)
        p = subprocess.run(["python3", "-c", safe.CLI_BOOT, str(len(code)), "plan", "data", "--json"],
                           input=code, capture_output=True, env=env)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(len(json.loads(p.stdout)["collisions"]), 3)
        p = subprocess.run(["python3", "-c", safe.CLI_BOOT, str(len(code)), "apply", "data", "--mode", "ask",
                            "--decisions", "-", "--json"], input=code + b"{}", capture_output=True, env=env)
        self.assertEqual((p.returncode, json.loads(p.stdout)["halted"]), (3, True))
        # as through exec: stdin never ends - the command reads exactly the answers' bytes
        answers = json.dumps({"data.csv": "keep", "notes.txt": "keep", "src/main.py": "overwrite"}).encode()
        proc = subprocess.Popen(["python3", "-c", safe.CLI_BOOT, str(len(code)), "apply", "data", "--mode", "ask",
                                 "--decisions=-:%d" % len(answers), "--json"], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        proc.stdin.write(code + answers)
        proc.stdin.flush()  # and left open
        out, err = proc.stdout.read(), proc.stderr.read()
        proc.wait(timeout=30)
        self.assertEqual(json.loads(out)["overwritten"], ["src/main.py"], err)


class UninstallHookTest(unittest.TestCase):
    """helm uninstall's pre-delete hook (files/hooks/stop_cloud_drive_mounts.py): the Deployments whose pods mount a
    Cloud Drive volume are scaled to 0, any other pod that mounts one is deleted, and it waits until none is left -
    against a fake API; it never fails the uninstall."""

    def load(self):
        env = {"NAMESPACE": "coder", "KUBERNETES_SERVICE_HOST": "k", "KUBERNETES_SERVICE_PORT": "443", "TIMEOUT": "30"}
        path = os.path.normpath(os.path.join(SRC, "../../files/hooks/stop_cloud_drive_mounts.py"))
        with mock.patch.dict(os.environ, env), mock.patch("ssl.create_default_context"):
            spec = importlib.util.spec_from_file_location("stop_mounts", path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
        return mod

    def test_it_stops_the_mounts_and_waits(self):
        h = self.load()
        pv = lambda name, driver: {"spec": {"csi": {"driver": driver}, "claimRef": {"namespace": "coder", "name": name}}}
        vol = lambda claim: {"volumes": [{"name": "v", "persistentVolumeClaim": {"claimName": claim}}]}
        pods = {"ws-a-1": vol("coder-safe-a"), "job-x": vol("coder-isos"), "db-1": vol("coder-db-1")}
        calls = []

        def call(method, path, body=None, content_type="application/json"):
            calls.append((method, path, body))
            if path == "/api/v1/persistentvolumes":
                return 200, {"items": [pv("coder-safe-a", "ru.yandex.s3.csi"), pv("coder-isos", "ru.yandex.s3.csi"),
                                       pv("coder-db-1", "rancher.io/local-path")]}
            if path.endswith("/deployments"):
                return 200, {"items": [{"metadata": {"name": "ws-a"}, "spec": {"replicas": 1, "template": {"spec": vol("coder-safe-a")}}},
                                       {"metadata": {"name": "coder"}, "spec": {"replicas": 2, "template": {"spec": {}}}}]}
            if path.endswith("/pods"):
                out = {"items": [{"metadata": {"name": n, "deletionTimestamp": "t" if n == "ws-a-1" else None}, "spec": sp}
                                 for n, sp in pods.items()]}
                pods.pop("ws-a-1", None)  # the scaled Deployment's pod goes at the next look
                return 200, out
            if method == "DELETE":
                pods.pop(path.rsplit("/", 1)[1], None)
            return 200, {}

        with mock.patch.object(h, "call", side_effect=call), mock.patch.object(h.time, "sleep"), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            h.main()
        self.assertIn(("PATCH", "/apis/apps/v1/namespaces/coder/deployments/ws-a", {"spec": {"replicas": 0}}), calls)
        self.assertNotIn("/deployments/coder", " ".join(c[1] for c in calls), "a Deployment that mounts none stays")
        self.assertIn(("DELETE", "/api/v1/namespaces/coder/pods/job-x", None), calls)
        self.assertNotIn(("DELETE", "/api/v1/namespaces/coder/pods/ws-a-1", None), calls, "terminating already")
        self.assertNotIn(("DELETE", "/api/v1/namespaces/coder/pods/db-1", None), calls, "not a Cloud Drive volume")
        self.assertIn("the CSI driver may go", out.getvalue())

    def test_it_never_fails_the_uninstall(self):
        h = self.load()
        with mock.patch.object(h, "main", side_effect=RuntimeError("API down")), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(h.run(), 0)
        self.assertIn("uninstall goes on", out.getvalue())


def load_hook(name, env):
    path = os.path.normpath(os.path.join(SRC, "../../files/hooks/%s" % name))
    base = {"NAMESPACE": "cf", "KUBERNETES_SERVICE_HOST": "k", "KUBERNETES_SERVICE_PORT": "443"}
    with mock.patch.dict(os.environ, dict(base, **env)), mock.patch("ssl.create_default_context"):
        spec = importlib.util.spec_from_file_location(name[:-3], path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    return mod


class InClusterDNSTest(unittest.TestCase):
    """global.inClusterDNS (files/hooks/coredns_rewrite.py): this release's rules at the top of CoreDNS's ".:53"
    block, replacing its earlier ones and never touching another's; removed on uninstall."""

    COREFILE = ".:53 {\n    errors\n    rewrite name regex ^x\\.other\\.?$ y answer auto # coder-airgapped other/rel\n    kubernetes cluster.local\n}\n"

    def test_rules_in_and_out(self):
        h = load_hook("coredns_rewrite.py", {"RELEASE": "coder-fork", "RULES": "rewrite name regex ^coder\\.vm\\.?$ t answer auto\nrewrite name regex ^(.+)\\.coder\\.vm\\.?$ t answer auto"})
        once = h.edit(self.COREFILE, h.RULES)
        lines = once.split("\n")
        self.assertEqual(lines[0], ".:53 {")
        self.assertTrue(lines[1].endswith("# coder-airgapped cf/coder-fork") and "^coder\\.vm" in lines[1])
        self.assertTrue(lines[2].endswith("# coder-airgapped cf/coder-fork"))
        self.assertIn("# coder-airgapped other/rel", once, "another release's rule stays")
        self.assertEqual(h.edit(once, h.RULES), once, "applying twice changes nothing")
        self.assertEqual(h.edit(once, []), self.COREFILE, "remove: back as it was")
        with self.assertRaises(RuntimeError):
            h.edit("example.org {\n}\n", h.RULES)


class DatabaseJobTest(unittest.TestCase):
    """The database when the chart brings CloudNativePG (files/hooks/apply_database.py): applied once the operator's
    webhook answers (retried until then), the Cluster before the pooler; deleted on uninstall, the pooler first."""

    def test_apply_retries_until_the_webhook_answers(self):
        import tempfile
        d = tempfile.mkdtemp()
        for n, kind in (("cluster.json", "Cluster"), ("pooler.json", "Pooler")):
            open(os.path.join(d, n), "w").write(json.dumps({"kind": kind, "metadata": {"name": "coder-db" + ("" if kind == "Cluster" else "-pooler-rw")}}))
        h = load_hook("apply_database.py", {"MANIFESTS": d, "TIMEOUT": "600"})
        answers = iter([(500, "failed calling webhook"), (0, "connection refused"), (200, "{}"), (201, "{}")])
        calls = []

        def call(method, path, body=None, content_type="application/json"):
            calls.append((method, path.split("?")[0].rsplit("/", 2)[-2:]))
            return next(answers)

        slept = []
        with mock.patch.object(h, "call", side_effect=call), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(h.apply(sleep=slept.append), 0)
        self.assertEqual([c[1] for c in calls], [["clusters", "coder-db"]] * 3 + [["poolers", "coder-db-pooler-rw"]])
        self.assertEqual(slept, [2, 4])

        gets = iter([200, 404, 404, 404])  # round 1: the pooler still there; round 2: both gone
        dcalls = []

        def dcall(method, path, body=None, content_type="application/json"):
            dcalls.append((method, path.rsplit("/", 1)[-1]))
            return (next(gets), "") if method == "GET" else (200, "")

        with mock.patch.object(h, "call", side_effect=dcall), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(h.delete(sleep=lambda s: None), 0)
        self.assertEqual(dcalls[:2], [("DELETE", "coder-db-pooler-rw"), ("DELETE", "coder-db")])


class CodeServerHATest(unittest.TestCase):
    """code-server HA (csha.py) without a database: its schema split into statements, an instance's pod (code-server
    on loopback only, its home volume on its node, or a folder of its own elsewhere), the endpoints. The database
    and the relays: csha_db_test.py (a throwaway PostgreSQL)."""

    @classmethod
    def setUpClass(cls):
        cls.c = load("csha", "csha.py")

    def test_the_schema_goes_one_statement_at_a_time(self):
        st = self.c.statements(self.c.SCHEMA) + self.c.statements(self.c.GRANTS)
        self.assertTrue(all(x.rstrip().endswith(";") for x in st))
        self.assertEqual(sum(1 for x in st if x.startswith("CREATE OR REPLACE FUNCTION")), 4)
        self.assertTrue(all("SECURITY DEFINER SET search_path" in x for x in st if "FUNCTION code_server_ha." in x
                            and x.startswith("CREATE")))
        self.assertIn("REVOKE ALL ON SCHEMA code_server_ha FROM PUBLIC;", st)

    def test_it_waits_for_a_database_that_is_not_up_yet(self):
        # a fresh install: refused, then "starting up", then the schema goes in - no crash loop
        pg = self.c.pgwire
        class DB:
            def __init__(self, fails):
                self.fails, self.statements = list(fails), 0
            def query(self, stmt):
                if self.fails:
                    raise self.fails.pop(0)
                self.statements += 1
        slept = []
        db = DB([ConnectionRefusedError(111, "refused"), pg.PgError({"C": "57P03", "M": "the database system is starting up"}),
                 # both replicas create the schema at once: CREATE SCHEMA IF NOT EXISTS races
                 pg.PgError({"C": "23505", "M": 'duplicate key value violates unique constraint "pg_namespace_nspname_index"'})])
        with contextlib.redirect_stdout(io.StringIO()):
            self.c.wait_for_schema(db, sleep=slept.append)
        self.assertEqual(slept, [2, 4, 8])
        self.assertEqual(db.statements, len(self.c.statements(self.c.SCHEMA) + self.c.statements(self.c.GRANTS)))
        # a real error is not waited out
        with self.assertRaises(pg.PgError):
            self.c.wait_for_schema(DB([pg.PgError({"C": "42501", "M": "permission denied"})]), sleep=slept.append)
        self.assertEqual(slept, [2, 4, 8])
        # nor a database that never comes up
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(OSError):
            self.c.wait_for_schema(DB([ConnectionRefusedError(111, "refused")] * 99), timeout=10, sleep=lambda s: None)

    def test_an_instance_pod(self):
        inst = {"workspace_id": "11111111-2222-3333-4444-555555555555", "image": "ws:1", "home_pvc": "coder-x-home",
                "home_node": "node-a", "folder": "/home/coder/src", "owner": "alice"}
        home = self.c.instance_pod(inst, 3, "tok", True, "primary", "coder")
        self.assertEqual(home["metadata"]["name"], "coder-cs-111111112222-g3")
        cs, station = home["spec"]["containers"]
        self.assertIn("127.0.0.1:13337", cs["command"])
        self.assertEqual(cs["command"][-1], "/home/coder/src")
        self.assertEqual(station["ports"][0]["containerPort"], 8080)
        self.assertFalse(home["spec"]["automountServiceAccountToken"])
        self.assertTrue(home["spec"]["securityContext"]["runAsNonRoot"])
        self.assertEqual(home["spec"]["volumes"][0]["persistentVolumeClaim"]["claimName"], "coder-x-home")
        self.assertIn("node-a", json.dumps(home["spec"]["affinity"]))
        away = self.c.instance_pod(inst, 4, "tok", False, "dr-site", "coder-dr")
        self.assertIn("emptyDir", away["spec"]["volumes"][0])
        self.assertNotIn("affinity", away["spec"])
        self.assertEqual(away["spec"]["containers"][0]["command"][-1], "/home/coder")
        env = {e["name"]: e for e in away["spec"]["containers"][1]["env"]}
        self.assertEqual(env["CSHA_DB"]["valueFrom"]["secretKeyRef"]["name"], "coder-code-server-ha-relay")
        self.assertEqual((env["CSHA_CLUSTER"]["value"], env["CSHA_NAMESPACE"]["value"]), ("dr-site", "coder-dr"))

    def test_endpoints(self):
        ws = "11111111-2222-3333-4444-555555555555"
        self.assertEqual(self.c.local_endpoint(ws, "coder"), "coder-cs-111111112222.coder.svc:8080")
        self.assertIsNone(self.c.external_endpoint(ws, "coder", ""))
        self.assertEqual(self.c.external_endpoint(ws, "coder", "{service}.{namespace}.svc.clusterset.local:{port}"),
                         "coder-cs-111111112222.coder.svc.clusterset.local:8080")




class CloudDrivePeersTest(unittest.TestCase):
    """Cloud Drives shared between users (drivepeers.py, through safe.py): asked for in chat, accepted by the owner
    (read only or read and write), approved by an admin when admins approve, mounted in the requester's workspaces
    from their next start (the templates' state), ended by either side or an admin; admins turn it all off."""

    ANN = {"id": "3f0c2a52-7d7e-4c5b-9a3e-2f4b8c1d0e9a", "username": "ann", "roles": []}
    BOB = {"id": "4a1d3b63-8e8f-4d6c-8b4f-3a5c9d2e1f0b", "username": "bob", "roles": []}
    EVE = {"id": "5b2e4c74-9f90-4e7d-9c5a-4b6dae3f2a1c", "username": "eve", "roles": []}
    ADMIN = {"id": "6c3f5d85-a0a1-4f8e-ad6b-5c7ebf4a3b2d", "username": "admin", "roles": ["owner"]}
    WS = "11111111-2222-4333-8444-555555555555"

    class Kube(CloudDriveConnectionsTest.Kube):
        def call(self, method, path, body=None, patch_type=None):
            if method == "GET" and "/pods?labelSelector=" in path:  # by whichever label the selector names
                key, want = urllib.parse.unquote(path.split("labelSelector=", 1)[1]).split("=", 1)
                return 200, {"items": [p for p in self.pods if p["metadata"]["labels"].get(key) == want]}
            return super().call(method, path, body, patch_type)

    def setUp(self):
        import tempfile
        self.dir = tempfile.TemporaryDirectory()
        self.safe = load("safe", "safe.py")
        self.peers = self.safe.drivepeers
        self.safe.LOG = lambda **kw: None
        self.safe.LINKS_FILE = os.path.join(self.dir.name, "links.json")
        self.peers.PEERS_FILE = os.path.join(self.dir.name, "peers.json")
        self.chat, self.changes = [], []
        users = {u["id"]: {**u, "name": "", "admin": "owner" in u["roles"]}
                 for u in (self.ANN, self.BOB, self.EVE, self.ADMIN)}
        self.peers.setup(lambda **kw: None, lambda u: "owner" in (u.get("roles") or []), users.get,
                         lambda user, to, text, sid: self.chat.append((user["username"], to, text, sid)),
                         lambda: self.changes.append(1))
        pod = CloudDriveConnectionsTest.pod("coder-ann-1", self.WS, self.ANN["id"])
        pod["spec"] = {"volumes": [{"name": "safe", "persistentVolumeClaim": {"claimName": "coder-safe-" + self.ANN["id"]}}]}
        self.kube = self.Kube([pod])
        self.safe.KUBE = self.kube

    def tearDown(self):
        self.dir.cleanup()

    def test_ask_accept_approve_mount_end(self):
        p = self.peers
        with self.assertRaises(PermissionError):  # off by default
            p.request(self.ANN, {"owner": self.BOB["id"], "access": "write"})
        with self.assertRaises(PermissionError):  # admins only
            p.save_settings(self.ANN, {"enabled": True})
        p.save_settings(self.ADMIN, {"enabled": True})
        out = p.request(self.ANN, {"owner": self.BOB["id"], "access": "write"})
        sid = out["share"]["id"]
        self.assertEqual(out["share"]["state"], "asked")
        self.assertEqual(self.chat[-1][:2], ("ann", self.BOB["id"]))
        self.assertEqual(self.chat[-1][3], sid)
        with self.assertRaises(ValueError):  # once
            p.request(self.ANN, {"owner": self.BOB["id"], "access": "read"})
        with self.assertRaises(PermissionError):  # only the owner answers
            p.answer(self.EVE, sid, {"accept": True})
        out = p.answer(self.BOB, sid, {"accept": True, "access": "read"})
        self.assertEqual((out["share"]["state"], out["share"]["access"]), ("approval", "read"))
        self.assertEqual(p.active(), {})
        self.assertEqual([s["id"] for s in p.view(self.ADMIN)["waiting"]], [sid])
        with self.assertRaises(PermissionError):
            p.approve(self.BOB, sid, {"approve": True})
        p.approve(self.ADMIN, sid, {"approve": True})
        self.assertEqual(self.safe.peer_mounts(), {self.ANN["id"]: [
            {"id": sid, "owner": self.BOB["id"], "name": "bob", "claim": "coder-safe-" + self.BOB["id"],
             "readOnly": True, "kind": "drive", "path": "", "item": ""}]})
        # the templates hear of it, and Ann's running workspace needs a restart to get it
        self.safe.publish_state({})
        self.assertEqual(json.loads(self.kube.state)["peers"][self.ANN["id"]][0]["claim"],
                         "coder-safe-" + self.BOB["id"])
        self.assertEqual(self.safe.stale_mounts(self.ANN["id"], None, set()), [self.WS])
        self.assertTrue(self.changes)
        with self.assertRaises(PermissionError):  # a third party does not end it
            p.end(self.EVE, sid)
        p.end(self.BOB, sid)
        self.assertEqual(p.active(), {})
        self.assertEqual(self.safe.stale_mounts(self.ANN["id"], None, set()), [])
        self.assertIn("Ended", self.chat[-1][2])

    def test_without_admin_approval_and_turned_off(self):
        p = self.peers
        p.save_settings(self.ADMIN, {"enabled": True})
        waiting = p.request(self.EVE, {"owner": self.BOB["id"], "access": "read"})["share"]["id"]
        p.answer(self.BOB, waiting, {"accept": True})
        # approving turned off: what waited for an admin goes active, and new ones need only the owner
        p.save_settings(self.ADMIN, {"adminApproval": False})
        sid = p.request(self.ANN, {"owner": self.BOB["id"], "access": "write"})["share"]["id"]
        self.assertEqual(p.answer(self.BOB, sid, {"accept": True})["share"]["state"], "active")
        self.assertEqual({k: [m["access"] for m in v] for k, v in p.active().items()},
                         {self.ANN["id"]: ["write"], self.EVE["id"]: ["read"]})
        # read only cannot be widened to write by the owner's answer
        other = p.request(self.ANN, {"owner": self.EVE["id"], "access": "read"})["share"]["id"]
        with self.assertRaises(ValueError):
            p.answer(self.EVE, other, {"accept": True, "access": "write"})
        p.answer(self.EVE, other, {"accept": False})
        self.assertEqual(next(x for x in p.view(self.ANN)["asked"] if x["id"] == other)["state"], "declined")
        # the feature off: nothing mounted (kept for when it is back on)
        p.save_settings(self.ADMIN, {"enabled": False})
        self.assertEqual(p.active(), {})
        p.save_settings(self.ADMIN, {"enabled": True})
        self.assertEqual(len(p.active()), 2)

    def test_admins_asked_in_chat_to_approve(self):
        """A share waiting for an admin asks every admin in a chat message carrying its card - not the admin who made
        it wait - whether its owner accepted a request or shared it themselves; nobody when admins do not approve."""
        p = self.peers
        p.ADMINS = lambda: [{"id": self.ADMIN["id"], "username": "admin", "name": ""},
                            {"id": self.EVE["id"], "username": "eve", "name": ""}]
        p.ITEM = lambda owner, path: {"docs": "folder"}.get(path)
        p.save_settings(self.ADMIN, {"enabled": True})
        sid = p.request(self.ANN, {"owner": self.BOB["id"], "access": "read"})["share"]["id"]
        self.assertFalse([c for c in self.chat if "Approve" in c[2]])  # asked: the owner answers first
        p.answer(self.BOB, sid, {"accept": True})
        asked = [c for c in self.chat if c[2].startswith("Approve Cloud Drive share")]
        self.assertEqual(sorted(c[1] for c in asked), sorted([self.ADMIN["id"], self.EVE["id"]]))
        self.assertEqual({(c[0], c[3]) for c in asked}, {("bob", sid)})
        self.assertIn("bob's Cloud Drive with ann (read only)", asked[0][2])
        self.chat.clear()
        folder = p.share(self.EVE, {"to": self.ANN["id"], "path": "docs", "access": "write"})["share"]
        asked = [c for c in self.chat if c[2].startswith("Approve")]
        self.assertEqual([(c[1], c[3]) for c in asked], [(self.ADMIN["id"], folder["id"])])  # not eve herself
        self.assertIn("eve's folder docs with ann (read and write)", asked[0][2])
        self.chat.clear()
        p.save_settings(self.ADMIN, {"adminApproval": False})
        p.share(self.BOB, {"to": self.EVE["id"], "path": "", "access": "read"})
        self.assertFalse([c for c in self.chat if c[2].startswith("Approve")])

    def test_admins_read_any_drive_but_an_encrypted_one(self):
        """Admins open any account's Cloud Drive read only (?drive=<owner id>: list, glance, download) - never write
        there, never one its owner encrypted; anyone else only through a share."""
        safe = self.safe
        safe.ENABLED = True
        safe.IS_ADMIN = lambda u: "owner" in (u.get("roles") or [])

        def call(user, method, path, query):
            sent, seen = [], []
            h = mock.Mock()
            h.headers = {"X-Requested-With": "coder-ui", "Origin": "https://c.example", "Host": "c.example"}
            h.user = lambda: (user, None)
            h.send_json = lambda status, body: sent.append((status, body))
            with mock.patch.object(safe, "listing", lambda u, path: seen.append(u["id"]) or {"path": path}), \
                    mock.patch.object(safe, "make_folder", lambda u, path: seen.append(u["id"]) or {"ok": True}), \
                    mock.patch.object(safe, "read_json", lambda h: {"path": "x"}):
                safe.handle(h, method, "/__coder-ui/api/safe/" + path, query)
            return sent[0][0], seen, sent[0][1]

        drive = "drive=" + self.BOB["id"]
        with mock.patch.object(safe, "encryption_on", lambda uid: False):
            status, seen, body = call(self.ADMIN, "GET", "list", "path=&" + drive)
            self.assertEqual((status, seen, body["access"], body["adminView"]), (200, [self.BOB["id"]], "read", True))
            self.assertEqual(call(self.ADMIN, "POST", "folder", drive)[:2], (403, []))  # read only
            self.assertEqual(call(self.ANN, "GET", "list", drive)[:2], (403, []))  # not an admin, not shared
            self.assertEqual(call(self.ADMIN, "GET", "list", "drive=..%2Fother")[:2], (400, []))
        with mock.patch.object(safe, "encryption_on", lambda uid: uid == self.BOB["id"]):
            status, seen, body = call(self.ADMIN, "GET", "list", drive)
            self.assertEqual((status, seen), (403, []))
            self.assertIn("encrypted", body["error"])

    def test_shared_drive_opens_at_once_through_the_file_routes(self):
        """A share is usable at once in the Cloud Drive window (?drive=<owner id>), before any workspace restart: read
        with any active share, written only with a read and write one; nothing without an active share."""
        p, safe = self.peers, self.safe
        safe.ENABLED = True
        p.save_settings(self.ADMIN, {"enabled": True, "adminApproval": False})
        read = p.request(self.ANN, {"owner": self.BOB["id"], "access": "read"})["share"]["id"]
        p.answer(self.BOB, read, {"accept": True})
        write = p.request(self.EVE, {"owner": self.BOB["id"], "access": "write"})["share"]["id"]
        p.answer(self.BOB, write, {"accept": True, "access": "write"})
        self.assertEqual(p.access_at(self.ANN["id"], self.BOB["id"], ""), "read")
        self.assertEqual(p.access_at(self.EVE["id"], self.BOB["id"], "a/b"), "write")
        self.assertIsNone(p.access_at(self.BOB["id"], self.ANN["id"], ""))

        def call(user, method, path, query):
            sent, seen = [], []
            h = mock.Mock()
            h.headers = {"X-Requested-With": "coder-ui", "Origin": "https://c.example", "Host": "c.example"}
            h.user = lambda: (user, None)
            h.send_json = lambda status, body: sent.append((status, body))
            with mock.patch.object(safe, "listing", lambda u, path: seen.append(u["id"]) or {"path": path}), \
                    mock.patch.object(safe, "make_folder", lambda u, path: seen.append(u["id"]) or {"ok": True}), \
                    mock.patch.object(safe, "read_json", lambda h: {"path": "x"}):
                safe.handle(h, method, "/__coder-ui/api/safe/" + path, query)
            return sent[0][0], seen

        drive = "drive=" + self.BOB["id"]
        self.assertEqual(call(self.ANN, "GET", "list", "path=&" + drive), (200, [self.BOB["id"]]))
        self.assertEqual(call(self.ANN, "POST", "folder", drive), (403, []))  # read only
        self.assertEqual(call(self.EVE, "POST", "folder", drive), (200, [self.BOB["id"]]))
        self.assertEqual(call(self.BOB, "GET", "list", "drive=" + self.EVE["id"]), (403, []))  # not shared
        self.assertEqual(call(self.ANN, "GET", "list", "path="), (200, [self.ANN["id"]]))  # their own
        # ended, or the feature off: no more
        p.end(self.BOB, read)
        self.assertEqual(call(self.ANN, "GET", "list", drive), (403, []))
        p.save_settings(self.ADMIN, {"enabled": False})
        self.assertEqual(call(self.EVE, "GET", "list", drive), (403, []))

    def test_owner_shares_a_folder_or_a_file_and_only_that_is_reached(self):
        """The owner shares a folder (read only) and a file (read and write) of their drive: active at once (no admin
        approval), mounted by subPath (only while there), and in the Cloud Drive window only those are reached - the
        top lists just them, outside them nothing, a read-only folder takes no writes, and neither the shared folder
        nor the shared file itself can be removed by the other user."""
        p, safe = self.peers, self.safe
        safe.ENABLED = True
        p.save_settings(self.ADMIN, {"enabled": True, "adminApproval": False})
        there = {"docs": "folder", "docs/plan.md": "file", "notes.txt": "file", "old.txt": "file"}
        p.ITEM = lambda owner, path: there.get(path)
        p.PEOPLE = lambda: [{"id": u["id"], "username": u["username"], "name": ""} for u in (self.ANN, self.BOB, self.EVE)]
        self.assertEqual([x["username"] for x in p.people(self.BOB)["people"]], ["ann", "eve"])
        folder = p.share(self.BOB, {"to": self.ANN["id"], "path": "docs/", "access": "read"})["share"]
        self.assertEqual((folder["kind"], folder["path"], folder["state"], folder["by"]), ("folder", "docs", "active", "owner"))
        self.assertIn("folder docs", self.chat[-1][2])
        file = p.share(self.BOB, {"to": self.ANN["id"], "path": "notes.txt", "access": "write"})["share"]
        gone = p.share(self.BOB, {"to": self.ANN["id"], "path": "old.txt", "access": "read"})["share"]
        with self.assertRaises(ValueError):  # once
            p.share(self.BOB, {"to": self.ANN["id"], "path": "docs", "access": "write"})
        with self.assertRaises(ValueError):  # not there
            p.share(self.BOB, {"to": self.ANN["id"], "path": "nothing", "access": "read"})
        with self.assertRaises(ValueError):  # their own
            p.share(self.BOB, {"to": self.BOB["id"], "path": "", "access": "read"})
        whole = p.share(self.BOB, {"to": self.EVE["id"], "path": "", "access": "write"})["share"]
        self.assertEqual(whole["kind"], "drive")
        # widest access that reaches a path
        self.assertEqual(p.access_at(self.ANN["id"], self.BOB["id"], "docs/a/b.txt"), "read")
        self.assertEqual(p.access_at(self.ANN["id"], self.BOB["id"], "notes.txt"), "write")
        self.assertIsNone(p.access_at(self.ANN["id"], self.BOB["id"], "notes.txt.bak"))
        self.assertIsNone(p.access_at(self.ANN["id"], self.BOB["id"], ""))
        # mounted by subPath, a file no longer there left out
        del there["old.txt"]
        with mock.patch.object(safe, "item_kind", lambda owner, path: there.get(path)):
            mounts = safe.peer_mounts()
        self.assertEqual(sorted((m["kind"], m["path"], m["item"], m["readOnly"]) for m in mounts[self.ANN["id"]]),
                         [("file", "notes.txt", "notes.txt", False), ("folder", "docs", "docs", True)])
        self.assertEqual([m["kind"] for m in mounts[self.EVE["id"]]], ["drive"])
        self.assertEqual(safe.peer_mount_path(mounts[self.ANN["id"]][0]), "/mnt/cloud-drive-items/" + mounts[self.ANN["id"]][0]["id"])
        self.assertEqual(safe.peer_mount_path(mounts[self.EVE["id"]][0]), "/mnt/cloud-drive-peers/bob")

        real = {"": {"path": "", "folders": [{"name": "docs"}, {"name": "private"}],
                     "files": [{"name": "notes.txt", "size": 3}, {"name": "secret.txt", "size": 9}]},
                "docs": {"path": "docs", "folders": [], "files": [{"name": "plan.md", "size": 5}]}}

        def call(user, method, path, query, body=None):
            sent, done = [], []
            h = mock.Mock()
            h.headers = {"X-Requested-With": "coder-ui", "Origin": "https://c.example", "Host": "c.example"}
            h.user = lambda: (user, None)
            h.send_json = lambda status, out: sent.append((status, out))
            with mock.patch.object(safe, "listing", lambda u, path: real[path]), \
                    mock.patch.object(safe, "make_folder", lambda u, path: done.append(("folder", path)) or {"ok": True}), \
                    mock.patch.object(safe, "delete", lambda u, path: done.append(("delete", path)) or {"ok": True}), \
                    mock.patch.object(safe, "upload", lambda h, u, path: done.append(("upload", path)) or h.send_json(200, {})), \
                    mock.patch.object(safe, "download", lambda h, u, path: done.append(("download", path)) or h.send_json(200, {})), \
                    mock.patch.object(safe, "read_json", lambda h, limit=8192: body or {}):
                safe.handle(h, method, "/__coder-ui/api/safe/" + path, query)
            return sent[0][0], sent[0][1], done

        drive = "drive=" + self.BOB["id"]
        status, top, _ = call(self.ANN, "GET", "list", "path=&" + drive)
        self.assertEqual(status, 200)
        self.assertEqual((top["access"], top["folders"], top["files"]),
                         (None, [{"name": "docs", "access": "read"}], [{"name": "notes.txt", "size": 3, "access": "write"}]))
        status, docs, _ = call(self.ANN, "GET", "list", "path=docs&" + drive)
        self.assertEqual((status, docs["access"], docs["files"]), (200, "read", [{"name": "plan.md", "size": 5}]))
        self.assertEqual(call(self.ANN, "GET", "list", "path=private&" + drive)[0], 403)
        self.assertEqual(call(self.ANN, "GET", "download", "path=secret.txt&" + drive)[0], 403)
        self.assertEqual(call(self.ANN, "GET", "download", "path=docs/plan.md&" + drive)[2], [("download", "docs/plan.md")])
        self.assertEqual(call(self.ANN, "POST", "folder", drive, {"path": "docs/new"})[0], 403)  # read only
        self.assertEqual(call(self.ANN, "PUT", "upload", "path=notes.txt&" + drive)[2], [("upload", "notes.txt")])
        self.assertEqual(call(self.ANN, "PUT", "upload", "path=new.txt&" + drive)[0], 403)  # beside the file: no
        self.assertEqual(call(self.ANN, "POST", "delete", drive, {"path": "notes.txt"})[0], 403)  # the shared file
        self.assertEqual(call(self.ANN, "GET", "glance", drive)[0], 403)  # not the entire drive
        self.assertEqual(call(self.EVE, "POST", "delete", drive, {"path": "docs/"})[2], [("delete", "docs/")])  # all of it, write
        # a running workspace that mounts exactly these needs no restart; one that mounts the drive does
        pod = self.kube.pods[0]
        pod["spec"]["containers"] = [{"name": "dev", "volumeMounts": [
            {"name": "x", "mountPath": "/mnt/cloud-drive-items/" + folder["id"], "readOnly": True, "subPath": "docs"},
            {"name": "x", "mountPath": "/mnt/cloud-drive-items/" + file["id"], "subPath": "notes.txt"}]}]
        with mock.patch.object(safe, "item_kind", lambda owner, path: there.get(path)):
            self.assertEqual(safe.stale_mounts(self.ANN["id"], None, set()), [])
            p.end(self.ANN, file["id"])
            self.assertEqual(safe.stale_mounts(self.ANN["id"], None, set()), [self.WS])
        self.assertIn("notes.txt", self.chat[-1][2])
        p.end(self.BOB, gone["id"])

    def test_shared_drive_glance_counts_its_files_for_whom_it_is_shared_with(self):
        """The Workspaces page's cloud of a drive shared with the caller: its HUD reads how many files, how much and the
        newest few (?drive=<owner id>, read access enough), its owner named; nothing once the share ends."""
        p, safe = self.peers, self.safe
        safe.ENABLED = True
        p.save_settings(self.ADMIN, {"enabled": True, "adminApproval": False})
        sid = p.request(self.ANN, {"owner": self.BOB["id"], "access": "read"})["share"]["id"]
        p.answer(self.BOB, sid, {"accept": True})
        root = "users/%s/" % self.BOB["id"]
        objects = [{"key": root + "docs/", "size": 0, "modified": "2026-10-01T00:00:00.000Z"}] + [
            {"key": root + "docs/f%d.txt" % i, "size": 100 * (i + 1), "modified": "2026-10-%02dT00:00:00.000Z" % (i + 1)}
            for i in range(10)]
        s3 = mock.Mock()
        s3.list = lambda bkt, prefix="", delimiter=None, limit=None: (
            [o for o in objects if o["key"].startswith(prefix)], [])

        def call(user, query):
            sent = []
            h = mock.Mock()
            h.headers = {}
            h.user = lambda: (user, None)
            h.send_json = lambda status, body: sent.append((status, body))
            with mock.patch.object(safe, "drive_of", lambda uid: (s3, "b", "users/%s/" % uid, None)):
                safe.handle(h, "GET", "/__coder-ui/api/safe/glance", query)
            return sent[0]

        status, body = call(self.ANN, "drive=" + self.BOB["id"])
        self.assertEqual(status, 200)
        self.assertEqual((body["files"], body["usedBytes"]), (10, sum(100 * (i + 1) for i in range(10))))
        self.assertEqual([f["path"] for f in body["newest"]], ["docs/f%d.txt" % i for i in range(9, 1, -1)])
        self.assertEqual(body["owner"], {"id": self.BOB["id"], "username": self.BOB["username"], "name": ""})
        self.assertEqual(call(self.EVE, "drive=" + self.BOB["id"])[0], 403)  # not shared with eve
        p.end(self.BOB, sid)
        self.assertEqual(call(self.ANN, "drive=" + self.BOB["id"])[0], 403)


if __name__ == "__main__":
    unittest.main()

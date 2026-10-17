"""Unit tests of the platform service (helm-chart/coder-airgapped/files/ui-updates): the parts that decide access,
keep it fast under load, and keep the image builder honest offline. Imported as modules, nothing started.

  python3 -m unittest platform_unit_test.py"""
import importlib.util, json, os, sys, threading, unittest
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

    def test_retention_purge_and_pairs(self):
        self.chat.put_settings({"peers": True}, "boss")
        self.chat.send(self.ANN, False, self.BOB, "a")
        self.chat.send(self.BOB, False, self.ANN, "b")
        self.chat.send(self.ADMIN, True, self.ANN, "c")
        pairs = self.chat.pairs()
        self.assertEqual(len(pairs), 1)  # developers only: the admin's conversation is not a pair
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
            mock.patch.object(ib, "WORKSPACE_TEMPLATE", "UBUNTU"),
            mock.patch.object(ib, "LOG", None),
            mock.patch.object(ib, "start_rescan", side_effect=lambda image_id, user: self.rescans.append(image_id)),
            mock.patch.object(ib, "manifest", side_effect=lambda repo, ref: ({}, self.digest)),
            mock.patch.object(ib, "image_size", return_value=1234),
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
        self.assertEqual(img["tracked"]["template"], "UBUNTU")
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
        for method, sub in (("POST", ""), ("POST", "/build"), ("POST", "/delete"), ("POST", "/s3"),
                            ("PUT", "/files/deb/x.deb")):
            with self.assertRaisesRegex(ValueError, "image the workspace template runs"):
                ib.route(h, method, "/" + ib.TRACKED_ID + sub, "", user, True, True)
        self.assertIn(ib.TRACKED_ID, ib.RESERVED_NAMES)
        # as a base it is the workspace image itself
        self.assertEqual(ib.clean_spec({"base": {"kind": "image", "id": ib.TRACKED_ID}}, "mine")["base"],
                         {"kind": "workspace"})

    def test_addons_read_from_the_image(self):
        import json
        a = ib.addons_of(json.dumps({"schema": 1, "base": "codercom/enterprise-base:ubuntu", "software": [
            {"name": "code-server", "version": "4.141.0", "group": "Apps", "what": "VS Code in the browser"}],
            "packages": {"added": [{"name": "firefox", "version": "157.0"}], "dependencies": 800, "upgraded": 3,
                         "installed": 1300}, "python": {"packages": [{"name": "marimo", "version": "0.24.2"}]}}))
        self.assertEqual(a["software"][0]["version"], "4.141.0")
        self.assertEqual(a["packages"]["added"][0]["name"], "firefox")
        self.assertEqual(a["packages"]["dependencies"], 800)
        self.assertEqual(a["python"]["packages"][0]["name"], "marimo")
        self.assertIsNone(ib.addons_of("not json"))
        self.assertIsNone(ib.addons_of(None))



class BannerTransitionsTest(unittest.TestCase):
    """The banner service (coder-banner/files/server.py) takes the dashboard's effects and transitions, and no other."""

    @classmethod
    def setUpClass(cls):
        path = os.path.join(SRC, "../../charts/coder-banner/files/server.py")
        spec = importlib.util.spec_from_file_location("banner_server", path)
        cls.srv = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.srv)

    def clean(self, **fields):
        return self.srv.Banner.clean(dict({"message": "Hello"}, **fields))

    def test_open_transition(self):
        self.assertEqual(self.clean()["openEffect"], "none")  # saved before it existed: none
        for effect in ("fade", "rolldown", "kitt", "crt", "glitch"):
            self.assertEqual(self.clean(openEffect=effect)["openEffect"], effect)
        with self.assertRaises(self.srv.BannerError):
            self.clean(openEffect="explode")

    def test_knight_rider(self):
        self.assertEqual(self.clean(effect="kitt")["effect"], "kitt")
        self.assertEqual(self.clean(closeEffect="kitt")["closeEffect"], "kitt")


if __name__ == "__main__":
    unittest.main()


class SafeTest(unittest.TestCase):
    """The safe (safe.py): only ever the caller's own prefix, every account's volume made, the nodes' endpoint."""

    UID = "3f0c2a52-7d7e-4c5b-9a3e-2f4b8c1d0e9a"

    def setUp(self):
        self.safe = load("safe", "safe.py")
        self.safe.LOG = lambda **kw: None

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
                if method == "GET":
                    return (200, have[path]) if path in have else (404, {})
                have[path + "/" + body["metadata"]["name"]] = body                      # POST: made
                return 201, body

        self.safe.KUBE = Kube()
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
                    {"name": "ubuntu-vnc-desktop", "displayName": "UBUNTU", "image": "ghcr.io/user187x/coder-workspace:1.8.5"},
                    {"name": "ubuntu-dataflow", "displayName": "UBUNTU DATAFLOW",
                     "image": "ghcr.io/user187x/coder-workspace-dataflow:1.1.0"}]):
            out = {x["image"]: x for x in ib.images_in_use()}
        self.assertEqual(out["ghcr.io/user187x/coder-workspace:1.8.5"]["workspaces"], ["alice/w1"])
        self.assertEqual(out["ghcr.io/user187x/coder-workspace:1.8.5"]["templates"], ["UBUNTU"])
        self.assertEqual(out["ghcr.io/user187x/coder-workspace-dataflow:1.1.0"]["workspaces"], [])
        self.assertEqual(out["localhost:5000/coder-images/team-tool:2"]["entry"], "team-tool")
        self.assertNotIn("old:1", out)


class IsoLibraryTest(unittest.TestCase):
    def setUp(self):
        self.safe = load("safe", "safe.py")
        self.safe.LOG = lambda **kw: None

    def test_names_and_the_shared_read_only_volume(self):
        for ok in ("ubuntu-26.04-desktop-amd64.iso", "disk.IMG"):
            self.assertEqual(self.safe.iso_name(ok), ok)
        for bad in ("../x.iso", "notes.txt", "a b.iso", ".iso"):
            with self.assertRaises(ValueError, msg=bad):
                self.safe.iso_name(bad)
        pv, pvc = self.safe.iso_volume_objects()
        self.assertEqual((pv["spec"]["csi"]["volumeHandle"], pv["spec"]["accessModes"], pv["spec"]["csi"]["readOnly"]),
                         ("coder-user-storage/library/isos", ["ReadOnlyMany"], True))
        self.assertEqual(pvc["spec"]["volumeName"], "coder-isos")

    def test_only_editors_change_it(self):
        self.safe.ENABLED = True
        self.safe.CAN_EDIT = lambda user: "owner" in user["roles"]
        sent = []
        h = mock.Mock()
        h.headers = {"X-Requested-With": "coder-ui"}
        h.user.return_value = ({"id": "u", "username": "dev", "roles": []}, None)
        h.send_json = lambda status, body: sent.append(status)
        self.safe.handle_isos(h, "POST", "/__coder-ui/api/isos/delete", "")
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

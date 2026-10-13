"""Unit tests of the platform service (helm-chart/coder-airgapped/files/ui-updates): the parts that decide access,
keep it fast under load, and keep the image builder honest offline. Imported as modules, nothing started.

  python3 -m unittest platform_unit_test.py"""
import importlib.util, os, sys, threading, unittest
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


if __name__ == "__main__":
    unittest.main()

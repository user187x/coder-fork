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


if __name__ == "__main__":
    unittest.main()

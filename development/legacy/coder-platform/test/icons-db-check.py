#!/usr/bin/env python3
"""Templates > Icons against a real PostgreSQL: loads ../coder-ui-updates/resources/ui-service.py, points its icon
store at a throwaway database, serves it on a local port, and checks every icon route (who may upload, which images
are accepted, how they are served, replace and delete) and the logo / avatar size and AVIF checks.
   ICON_TEST_PG=postgresql://postgres:postgres@127.0.0.1:5432/postgres?sslmode=disable test/icons-db-check.py
The database is dropped afterwards. Exit 1 on the first failed expectation."""
import base64
import importlib.util
import json
import os
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "..", "coder-ui-updates", "resources", "ui-service.py")
ADMIN_URI = os.environ.get(
    "ICON_TEST_PG",
    "postgresql://postgres:postgres@127.0.0.1:5432/postgres?sslmode=disable",
)
DB = "coder_ui_icons_check_%d" % os.getpid()
sys.path.insert(0, os.path.dirname(SRC))
sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location("ui_service", SRC)
ui = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ui)
import pgwire  # noqa: E402  (next to ui-service.py)

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 32
GIF = b"GIF89a" + b"\0" * 32
AVIF = b"\0\0\0\x1cftypavis" + b"\0" * 32
SVG = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"><rect width="1" height="1"/></svg>'
USERS = {
    "owner-token": {"username": "olivia", "roles": ["owner"]},
    "tadmin-token": {"username": "tom", "roles": ["template-admin"]},
    "member-token": {"username": "mia", "roles": []},
}


def expect(cond, what):
    if not cond:
        print("FAIL:", what)
        sys.exit(1)
    print("ok  ", what)


def data_url(ctype, data):
    return "data:%s;base64,%s" % (ctype, base64.b64encode(data).decode())


def request(method, path, token=None, body=None, headers=None):
    req = urllib.request.Request(
        BASE_URL + path,
        method=method,
        data=None if body is None else json.dumps(body).encode(),
    )
    if token:
        req.add_header("Cookie", "%s=%s" % (ui.SESSION_COOKIE, token))
    if body is not None:
        req.add_header("Content-Type", "application/json")
        req.add_header(*ui.CSRF_HEADER)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=10) as res:
            return res.status, res.headers, res.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()


def post(path, token, body, **kw):
    status, _, raw = request("POST", ui.BASE + path, token, body, **kw)
    return status, json.loads(raw or b"{}")


admin = pgwire.Pool(ADMIN_URI, None, size=1)
admin.query("CREATE DATABASE %s" % DB)
try:
    pool = pgwire.Pool(ADMIN_URI.replace("/postgres?", "/%s?" % DB, 1), None, size=2)
    ui.coder_user = lambda token: (
        ("ok", USERS[token]) if token in USERS else ("unauthenticated", None)
    )
    ui.ICONS = ui.IconStore(type("Logos", (), {"pool": pool})())
    server = ThreadingHTTPServer(("127.0.0.1", 0), ui.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    BASE_URL = "http://127.0.0.1:%d" % server.server_address[1]

    # Who may see and change icons
    status, _, raw = request("GET", ui.BASE + "/api/icons")
    expect(status == 401, "listing icons needs a Coder session")
    status, _, raw = request("GET", ui.BASE + "/api/icons", "member-token")
    expect(
        status == 200 and json.loads(raw) == {"icons": [], "canManage": False},
        "a member sees an empty library and may not manage it",
    )
    status, body = post(
        "/api/icons",
        "member-token",
        {"name": "gpu", "dataUrl": data_url("image/png", PNG)},
    )
    expect(status == 403, "a member may not upload")
    status, body = post(
        "/api/icons",
        "owner-token",
        {"name": "gpu", "dataUrl": data_url("image/png", PNG)},
        headers={"Origin": "https://evil.example"},
    )
    expect(status == 403, "a cross-site upload is rejected")

    # Uploads
    status, body = post(
        "/api/icons",
        "tadmin-token",
        {"name": "gpu-node", "dataUrl": data_url("image/gif", GIF)},
    )
    expect(
        status == 200 and body["url"].startswith(ui.BASE + "/icons/gpu-node?v="),
        "a template admin uploads a GIF",
    )
    status, body = post(
        "/api/icons",
        "owner-token",
        {"name": "build.runner", "dataUrl": data_url("image/svg+xml", SVG)},
    )
    expect(status == 200, "an owner uploads an SVG")
    status, body = post(
        "/api/icons",
        "owner-token",
        {"name": "anim", "dataUrl": data_url("image/avif", AVIF)},
    )
    expect(status == 200, "an animated AVIF is accepted")
    for name in ("Upper", "-dash", "a/b", "x" * 65, ""):
        status, body = post(
            "/api/icons",
            "owner-token",
            {"name": name, "dataUrl": data_url("image/png", PNG)},
        )
        expect(status == 400, "the name %r is refused" % name)
    status, body = post(
        "/api/icons",
        "owner-token",
        {"name": "fake", "dataUrl": data_url("image/png", GIF)},
    )
    expect(status == 400 and "PNG" in body["error"], "a GIF labelled as PNG is refused")
    status, body = post(
        "/api/icons",
        "owner-token",
        {
            "name": "evil",
            "dataUrl": data_url(
                "image/svg+xml",
                b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
            ),
        },
    )
    expect(status == 400, "an SVG with a script is refused")
    big = PNG + b"\0" * ui.ICON_MAX_BYTES
    status, _, raw = request(
        "POST",
        ui.BASE + "/api/icons",
        "owner-token",
        {"name": "big", "dataUrl": data_url("image/png", big)},
    )
    expect(
        status == 400, "an icon over %d MB is refused" % (ui.ICON_MAX_BYTES // 1048576)
    )

    # Listing and serving
    status, _, raw = request("GET", ui.BASE + "/api/icons", "tadmin-token")
    listing = json.loads(raw)
    expect(
        listing["canManage"]
        and [i["name"] for i in listing["icons"]]
        == ["anim", "build.runner", "gpu-node"],
        "the library lists every icon by name, and a template admin may manage it",
    )
    gpu = next(i for i in listing["icons"] if i["name"] == "gpu-node")
    expect(
        gpu["type"] == "image/gif"
        and gpu["bytes"] == len(GIF)
        and gpu["uploadedBy"] == "tom",
        "an icon records its type, size and uploader",
    )
    status, headers, raw = request("GET", ui.BASE + "/icons/gpu-node")
    expect(
        status == 200
        and raw == GIF
        and headers["Content-Type"] == "image/gif"
        and headers["Cache-Control"] == "public, max-age=300",
        "an icon is public, briefly cached without ?v=",
    )
    status, headers, raw = request("GET", gpu["url"])
    expect(
        status == 200 and "immutable" in headers["Cache-Control"],
        "a versioned icon URL is cached for good",
    )
    status, headers, _ = request("GET", ui.BASE + "/icons/build.runner")
    expect(
        headers["Content-Type"] == "image/svg+xml"
        and headers["Content-Security-Policy"] == ui.SVG_CSP,
        "an SVG icon is served under the SVG content policy",
    )
    status, _, _ = request("GET", ui.BASE + "/icons/missing")
    expect(status == 404, "an unknown icon is 404")
    status, _, _ = request("GET", ui.BASE + "/icons/..%2Fetc")
    expect(status == 404, "a malformed icon name is 404")

    # Replace and delete
    status, body = post(
        "/api/icons",
        "owner-token",
        {"name": "gpu-node", "dataUrl": data_url("image/png", PNG)},
    )
    expect(
        status == 200 and body["url"] != gpu["url"],
        "replacing an icon gives it a new version",
    )
    _, headers, raw = request("GET", ui.BASE + "/icons/gpu-node")
    expect(
        raw == PNG and headers["Content-Type"] == "image/png",
        "the stable path serves the replacement",
    )
    status, body = post("/api/icons/delete", "member-token", {"name": "gpu-node"})
    expect(status == 403, "a member may not delete")
    status, body = post("/api/icons/delete", "owner-token", {"name": "gpu-node"})
    expect(status == 200 and body["removed"] is True, "an owner deletes an icon")
    status, body = post("/api/icons/delete", "owner-token", {"name": "gpu-node"})
    expect(
        status == 200 and body["removed"] is False,
        "deleting it again reports nothing removed",
    )
    status, _, _ = request("GET", ui.BASE + "/icons/gpu-node")
    expect(status == 404, "a deleted icon is gone")

    # The count limit
    limit, ui.ICON_MAX_COUNT = ui.ICON_MAX_COUNT, 2
    status, body = post(
        "/api/icons",
        "owner-token",
        {"name": "third", "dataUrl": data_url("image/png", PNG)},
    )
    expect(
        status == 400 and "already 2 icons" in body["error"],
        "uploads stop at the icon limit",
    )
    status, body = post(
        "/api/icons",
        "owner-token",
        {"name": "anim", "dataUrl": data_url("image/png", PNG)},
    )
    expect(status == 200, "an existing icon can still be replaced at the limit")
    ui.ICON_MAX_COUNT = limit

    # Logo and avatar checks shared with the icon parser
    expect(
        ui.parse_logo(data_url("image/avif", AVIF))[0] == "image/avif",
        "a logo may be AVIF",
    )
    expect(
        ui.LOGO_MAX_BYTES == ui.AVATAR_MAX_BYTES == 4 * 1048576,
        "logos and avatars may be up to 4 MB",
    )
    try:
        ui.parse_logo(data_url("image/png", PNG + b"\0" * ui.LOGO_MAX_BYTES))
        expect(False, "a logo over 4 MB is refused")
    except ValueError as e:
        expect("4 MB" in str(e), "a logo over 4 MB is refused, saying so")
    server.shutdown()
finally:
    try:
        pool.close()
    except Exception:  # noqa: BLE001
        pass
    admin.query("DROP DATABASE IF EXISTS %s WITH (FORCE)" % DB)
print("all icon checks passed")

#!/usr/bin/env python3
"""Coder banner service (standard library only).

Served on the Coder host under /__banner/ (the HTTPRoute sends that prefix here):

  public  GET  banner.js, banner.json     the loader script injected into Coder, and the live banner
          GET  fx.css                     the loader's stylesheet: pictures and the stylesheet text effects
          GET  vendor/anime.umd.min.js    the text-effects library (Anime.js, MIT), loaded only when an effect is set
  admin   GET  admin (+ app.js/app.css)   single-page editor, Coder admins only
          GET  admin/emoji-picker.js, admin/emoji-data.js   the emoji picker and its (self-contained) emoji list
          GET  api/state                  current banner + who last changed it
          POST api/banner                 publish changes
          POST api/reappear               make everyone who dismissed the banner see it again
          POST api/reset                  drop admin edits, fall back to the chart defaults

Authentication: there are no separate accounts. The browser already sends its Coder session cookie to
this host, so every admin request is verified against Coder itself (GET /api/v2/users/me) and must
hold an admin role. If Coder cannot be reached the request is refused (fail closed).

Live push: every open Coder tab holds a WebSocket to the hub in live.py (port LIVE_PORT, routed at
/__banner/live) and is told about a change the instant it is published - see that module.

State: what an admin publishes lives in a ConfigMap (state.json), so it survives restarts and can be
inspected with kubectl. The chart's values act as the defaults until something is published.

Dismissals: the loader remembers a dismissed banner by its `id`, which is a hash of the content plus a
`revision` counter. Editing the content, or bumping the revision ("show again"), changes the id.
"""
import hashlib
import json
import os
import re
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

PORT = int(os.environ.get("PORT", "8080"))
APP_DIR = os.environ.get("APP_DIR", "/app")
DEFAULTS_FILE = os.environ.get("DEFAULTS_FILE", "/config/banner.json")
CODER_URL = os.environ.get("CODER_URL", "http://coder").rstrip("/")
ADMIN_ROLES = {r.strip() for r in os.environ.get("ADMIN_ROLES", "owner").split(",") if r.strip()}
STATE_FILE = os.environ.get("STATE_FILE", "")  # local testing; in-cluster the ConfigMap is used
STATE_CONFIGMAP = os.environ.get("STATE_CONFIGMAP", "coder-banner-state")
VENDOR_DIR = os.environ.get("VENDOR_DIR", "/vendor")  # third-party files, kept apart from our own code
EMOJI_DIR = os.environ.get("EMOJI_DIR", "/emoji")  # emoji-data.js (generated data, own ConfigMap: it is large)

BASE = "/__banner"
SESSION_COOKIE = "coder_session_token"
LEVELS = ("info", "success", "warning", "critical")
# Text effects. The names are the contract with the dashboard (BANNER_EFFECTS), which draws them; banner.js and
# admin.html (the service's own loader and editor) draw and offer them all too (the first ten with Anime.js, the
# others with fx.css). The chart's defaults are checked against these lists as well (templates/_helpers.tpl).
EFFECTS = ("none", "typewriter", "fade", "rise", "wave", "bounce", "flip", "shake", "pulse", "rainbow", "starwars",
           "pewpew", "flap", "shimmer", "kitt")
# The Knight Rider sweep's pace (and the crawl's, in announcements saved before crawlSpeed) and the banner's font
# (the dashboard's bundled fonts and generic stacks: nothing is fetched, the network may be air-gapped). Keep in sync
# with the dashboard's bannerFonts.ts.
SPEEDS = ("slow", "normal", "fast")
# The crawl (effect "starwars"): its pace in pixels per second and how far its lines converge towards a horizon, in
# percent (0 a flat scroller from the bottom up, 100 the opening crawl); the dashboard's CRAWL_SPEED and CRAWL_TILT.
CRAWL_SPEED = (4, 60)
CRAWL_TILT = (0, 100)
# The crawl's pace before it had its own setting, by speed.
LEGACY_CRAWL_SPEED = {"slow": 9, "normal": 15, "fast": 26}
# How the dashboard takes the banner away when someone dismisses it.
CLOSE_EFFECTS = ("none", "pixelate", "rollup", "fade", "wipe", "matrix", "gaussian", "evaporate", "kitt")
# How the banner comes in when it appears (the dashboard's BANNER_OPEN_EFFECTS).
OPEN_EFFECTS = ("none", "fade", "rolldown", "kitt", "crt", "glitch")
FONTS = ("default", "sans", "serif", "condensed", "geist-mono", "ibm-plex-mono", "jetbrains-mono", "fira-code",
         "source-code-pro")
# The message's alignment, and how the background picture fills the banner's screen (the dashboard's PICTURE_FITS).
ALIGNS = ("left", "center", "right")
PICTURE_FITS = ("center", "span", "tile", "fill", "stretch")
# The countdown's moment: UTC, to the second at most ("2026-10-20T18:00:00Z").
COUNTDOWN_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?Z$")
# 4000: long broadcasts are what the crawl and the dashboard's scroll-down view are for.
LIMITS = {"message": 4000, "title": 80, "linkText": 60, "linkUrl": 500, "picture": 500}
CSRF_HEADER = ("X-Requested-With", "coder-banner-admin")
LIVE_ENABLED = os.environ.get("LIVE_ENABLED", "1") not in ("0", "false", "no", "")
LIVE_PORT = int(os.environ.get("LIVE_PORT", "8081"))
LIVE_MAX_CLIENTS = int(os.environ.get("LIVE_MAX_CLIENTS", "5000"))
LIVE_HEARTBEAT = int(os.environ.get("LIVE_HEARTBEAT", "20"))  # seconds between heartbeats/pings
LIVE_STALE = int(os.environ.get("LIVE_STALE", str(LIVE_HEARTBEAT * 3 + 10)))  # drop a tab silent for this long
LIVE_WATCH = int(os.environ.get("LIVE_WATCH", "5"))  # seconds between re-reads of the stored banner
STATE_TTL = 10  # seconds a state read is cached (writes update the cache immediately)
AUTH_TTL = 30  # seconds a verified Coder session is trusted before re-checking

FIELDS = {
    "enabled": True,
    "level": "info",
    "title": "",
    "message": "",
    "linkText": "",
    "linkUrl": "",
    "dismissible": True,
    "showOnLoginPage": True,
    "refreshSeconds": 60,
    "effect": "none",
    "repeat": False,
    "speed": "normal",
    "crawlSpeed": 15,
    "crawlTilt": 100,
    "font": "default",
    # Receipts: the dashboard records who viewed the announcement and lets them confirm it (the Viewed box, Confirm).
    # Off for a greeting nobody needs to account for.
    "receipts": True,
    "closeEffect": "fade",
    "openEffect": "none",
    # The dashboard's editor writes plain text (markdown false); announcements written before it are Markdown.
    "markdown": True,
    "align": "center",
    # The message outlined in a contrasting colour, to stay legible over any background picture.
    "outline": False,
    # The banner's background picture (a same-site path or an http(s) URL; "" none) and how it fills the screen.
    "picture": "",
    "pictureFit": "span",
    # The countdown shown as a watch at the banner's left ("" none).
    "countdown": "",
}
# Files the admin page loads, served only to admins: path -> (file, content type, directory; None = APP_DIR)
ADMIN_FILES = {
    BASE + "/admin/app.js": ("app.js", "application/javascript", None),
    BASE + "/admin/app.css": ("app.css", "text/css", None),
    BASE + "/admin/emoji-picker.js": ("emoji-picker.js", "application/javascript", None),
    BASE + "/admin/emoji-data.js": ("emoji-data.js", "application/javascript", EMOJI_DIR),
}
SAFE_URL = re.compile(r"^(https?://[^\s]+|/(?!/)[^\s]*)$", re.I)


def log(**kv):
    sys.stderr.write(json.dumps(kv, sort_keys=True) + "\n")
    sys.stderr.flush()


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- state storage


class FileStore:
    def __init__(self, path):
        self.path = path

    def read(self):
        try:
            with open(self.path) as f:
                return json.load(f)
        except FileNotFoundError:
            return {}

    def write(self, state):
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(state, f)
        os.replace(tmp, self.path)


class KubeStore:
    """state.json inside one ConfigMap, via the in-cluster API (RBAC is limited to that ConfigMap)."""

    SA = "/var/run/secrets/kubernetes.io/serviceaccount"

    def __init__(self, name):
        with open(self.SA + "/namespace") as f:
            namespace = f.read().strip()
        host, port = os.environ["KUBERNETES_SERVICE_HOST"], os.environ.get("KUBERNETES_SERVICE_PORT", "443")
        self.url = "https://%s:%s/api/v1/namespaces/%s/configmaps/%s" % (host, port, namespace, name)
        self.ctx = ssl.create_default_context(cafile=self.SA + "/ca.crt")

    def _call(self, method, body=None, content_type="application/json"):
        with open(self.SA + "/token") as f:  # re-read: projected tokens rotate
            token = f.read().strip()
        req = urllib.request.Request(
            self.url,
            method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": "Bearer " + token, "Accept": "application/json", "Content-Type": content_type},
        )
        with urllib.request.urlopen(req, timeout=5, context=self.ctx) as res:
            return json.load(res)

    def read(self):
        try:
            raw = (self._call("GET").get("data") or {}).get("state.json")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return {}
            raise
        return json.loads(raw) if raw else {}

    def write(self, state):
        self._call("PATCH", {"data": {"state.json": json.dumps(state)}}, "application/merge-patch+json")


# --------------------------------------------------------------------------- banner logic


class BannerError(ValueError):
    """A problem the admin can fix; the message is shown to them."""


class Banner:
    def __init__(self, store):
        self.store = store
        self.lock = threading.Lock()
        self._cached_at = 0.0
        self._cached = {}

    @staticmethod
    def with_fields(b):
        """b over the fields' defaults; a crawl saved before crawlSpeed keeps the pace its speed gave it."""
        out = {**FIELDS, **b}
        if "crawlSpeed" not in b:
            out["crawlSpeed"] = LEGACY_CRAWL_SPEED.get(out["speed"], FIELDS["crawlSpeed"])
        return out

    def defaults(self):
        try:
            with open(DEFAULTS_FILE) as f:
                return self.with_fields(json.load(f))
        except (OSError, ValueError):
            return dict(FIELDS)

    def state(self, fresh=False):
        if fresh or time.time() - self._cached_at > STATE_TTL:
            try:
                self._cached = self.store.read()
                self._cached_at = time.time()
            except Exception as e:  # keep serving the last good state
                log(event="state-read-failed", error=str(e))
        return self._cached

    @staticmethod
    def make_id(b, revision):
        parts = [b["level"], b["title"], b["message"], b["linkText"], b["linkUrl"], revision]
        # A background picture or a countdown makes another announcement, as they did in the message; only when
        # there is one, so the announcements published before them keep their ids (and their dismissals).
        for key in ("picture", "countdown"):
            if b.get(key):
                parts.append([key, b[key]])
        raw = json.dumps(parts)
        return hashlib.sha256(raw.encode()).hexdigest()[:12]

    def effective(self):
        state = self.state()
        override = state.get("override")
        if override:
            b = self.with_fields(override)
            b["id"] = self.make_id(b, state.get("revision", 0))
        else:
            b = self.defaults()
            b["id"] = b.get("id") or self.make_id(b, 0)
        return b

    def public(self):
        b = self.effective()
        return {k: b[k] for k in (*FIELDS, "id")}

    def summary(self):
        state = self.state()
        defaults = self.defaults()
        defaults.pop("id", None)
        return {
            "banner": self.public(),
            "overrideActive": bool(state.get("override")),
            "defaults": defaults,
            "revision": state.get("revision", 0),
            "updatedBy": state.get("updatedBy"),
            "updatedAt": state.get("updatedAt"),
        }

    @staticmethod
    def clean(payload):
        if not isinstance(payload, dict):
            raise BannerError("Expected a JSON object.")
        out = dict(FIELDS)
        for key in ("title", "message", "linkText", "linkUrl", "picture"):
            value = payload.get(key, "")
            if not isinstance(value, str):
                raise BannerError("%s must be text." % key)
            value = value.strip()
            if len(value) > LIMITS[key]:
                raise BannerError("%s is too long (max %d characters)." % (key, LIMITS[key]))
            out[key] = value
        for key in ("enabled", "dismissible", "showOnLoginPage", "repeat", "receipts", "markdown", "outline"):
            value = payload.get(key, FIELDS[key])
            if not isinstance(value, bool):
                raise BannerError("%s must be true or false." % key)
            out[key] = value
        out["level"] = payload.get("level", "info")
        if out["level"] not in LEVELS:
            raise BannerError("Style must be one of: %s." % ", ".join(LEVELS))
        out["closeEffect"] = payload.get("closeEffect", "fade")
        if out["closeEffect"] not in CLOSE_EFFECTS:
            raise BannerError("Close transition must be one of: %s." % ", ".join(CLOSE_EFFECTS))
        out["openEffect"] = payload.get("openEffect", "none")
        if out["openEffect"] not in OPEN_EFFECTS:
            raise BannerError("Open transition must be one of: %s." % ", ".join(OPEN_EFFECTS))
        out["effect"] = payload.get("effect", "none")
        if out["effect"] not in EFFECTS:
            raise BannerError("Text effect must be one of: %s." % ", ".join(EFFECTS))
        out["speed"] = payload.get("speed", "normal")
        if out["speed"] not in SPEEDS:
            raise BannerError("Speed must be one of: %s." % ", ".join(SPEEDS))
        crawl_speed = payload.get("crawlSpeed", LEGACY_CRAWL_SPEED.get(out["speed"], FIELDS["crawlSpeed"]))
        for key, value, (low, high), what in (("crawlSpeed", crawl_speed, CRAWL_SPEED, "The crawl's speed"),
                                              ("crawlTilt", payload.get("crawlTilt", FIELDS["crawlTilt"]), CRAWL_TILT,
                                               "The crawl's convergence")):
            if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
                raise BannerError("%s must be a whole number from %d to %d." % (what, low, high))
            out[key] = value
        out["font"] = payload.get("font", "default")
        if out["font"] not in FONTS:
            raise BannerError("Font must be one of: %s." % ", ".join(FONTS))
        out["align"] = payload.get("align", "center")
        if out["align"] not in ALIGNS:
            raise BannerError("Alignment must be one of: %s." % ", ".join(ALIGNS))
        out["pictureFit"] = payload.get("pictureFit", "span")
        if out["pictureFit"] not in PICTURE_FITS:
            raise BannerError("The picture's fit must be one of: %s." % ", ".join(PICTURE_FITS))
        if out["picture"] and not SAFE_URL.match(out["picture"]):
            raise BannerError("The picture's address must start with https://, http:// or /.")
        countdown = payload.get("countdown", "")
        if not isinstance(countdown, str) or (countdown and not COUNTDOWN_RE.match(countdown)):
            raise BannerError("The countdown must be a UTC time such as 2026-10-20T18:00:00Z.")
        out["countdown"] = countdown
        seconds = payload.get("refreshSeconds", FIELDS["refreshSeconds"])
        if isinstance(seconds, bool) or not isinstance(seconds, int) or not 15 <= seconds <= 3600:
            raise BannerError("Check interval must be between 15 and 3600 seconds.")
        out["refreshSeconds"] = seconds
        # A message, a background picture or a countdown: an announcement may be only a picture.
        if out["enabled"] and not (out["message"] or out["picture"] or out["countdown"]):
            raise BannerError("Write a message (or choose a background image or a countdown) before publishing the banner.")
        if out["linkUrl"] and not SAFE_URL.match(out["linkUrl"]):
            raise BannerError("The link must start with https://, http:// or /.")
        return out

    def _write(self, state):
        self.store.write(state)
        self._cached, self._cached_at = state, time.time()

    def save(self, payload, user):
        fields = self.clean(payload)
        with self.lock:
            state = self.state(fresh=True)
            self._write({"override": fields, "revision": state.get("revision", 0), "updatedBy": user, "updatedAt": now()})
        log(event="banner-saved", user=user, level=fields["level"], effect=fields["effect"], enabled=fields["enabled"], message=fields["message"][:120])

    def reappear(self, user):
        with self.lock:
            state = self.state(fresh=True)
            override = state.get("override") or {k: v for k, v in self.defaults().items() if k in FIELDS}
            if not override.get("message"):
                raise BannerError("There is no banner message to show. Write and publish one first.")
            override["enabled"] = True
            revision = state.get("revision", 0) + 1
            self._write({"override": override, "revision": revision, "updatedBy": user, "updatedAt": now()})
        log(event="banner-reappear", user=user, revision=revision)

    def reset(self, user):
        with self.lock:
            revision = self.state(fresh=True).get("revision", 0)
            self._write({"revision": revision, "updatedBy": user, "updatedAt": now()})
        log(event="banner-reset", user=user)


# --------------------------------------------------------------------------- authentication


class Auth:
    """Verifies a Coder session token against Coder itself and checks for an admin role."""

    def __init__(self):
        self.lock = threading.Lock()
        self.cache = {}

    def check(self, token):
        """Returns (status, user) where status is ok | unauthenticated | forbidden | unavailable."""
        key = hashlib.sha256(token.encode()).hexdigest()
        with self.lock:
            hit = self.cache.get(key)
            if hit and hit[0] > time.time():
                return hit[1], hit[2]
        status, user, ttl = self._ask_coder(token)
        if status != "unavailable":
            with self.lock:
                if len(self.cache) > 1000:
                    self.cache.clear()
                self.cache[key] = (time.time() + ttl, status, user)
        return status, user

    @staticmethod
    def _ask_coder(token):
        req = urllib.request.Request(CODER_URL + "/api/v2/users/me", headers={"Coder-Session-Token": token, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=5) as res:
                me = json.load(res)
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                return "unauthenticated", None, 5
            log(event="coder-error", status=e.code)
            return "unavailable", None, 0
        except Exception as e:
            log(event="coder-unreachable", error=str(e))
            return "unavailable", None, 0
        roles = {r.get("name") for r in me.get("roles") or []}
        user = {"username": me.get("username"), "email": me.get("email"), "roles": sorted(r for r in roles if r)}
        if me.get("status") == "active" and roles & ADMIN_ROLES:
            return "ok", user, AUTH_TTL
        return "forbidden", user, AUTH_TTL


# --------------------------------------------------------------------------- HTTP


class Handler(BaseHTTPRequestHandler):
    server_version = "coder-banner"
    protocol_version = "HTTP/1.1"
    banner = None  # set in main()
    hub = None  # live.Hub, set in main() unless LIVE_ENABLED=0
    auth = Auth()

    ADMIN_HEADERS = {
        "Cache-Control": "no-store",
        "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
        "img-src 'self' data: https: http:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
    }

    def log_message(self, fmt, *args):  # request lines only; never headers or cookies
        log(event="request", client=self.address_string(), line=fmt % args)

    # -- helpers
    def send(self, status, body=b"", content_type="text/plain; charset=utf-8", headers=None):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def send_json(self, status, payload):
        self.send(status, json.dumps(payload), "application/json", self.ADMIN_HEADERS)

    def send_file(self, name, content_type, headers, directory=None):
        try:
            with open(os.path.join(directory or APP_DIR, name), "rb") as f:
                self.send(200, f.read(), content_type, headers)
        except FileNotFoundError:
            self.send(404, "not found")

    def session_token(self):
        header = self.headers.get("Coder-Session-Token")
        if header:
            return header
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except Exception:
            return ""
        morsel = cookie.get(SESSION_COOKIE)
        return morsel.value if morsel else ""

    def authenticate(self):
        token = self.session_token()
        if not token:
            return "unauthenticated", None
        return Handler.auth.check(token)

    @staticmethod
    def subscribers():
        return Handler.hub.count if Handler.hub is not None else 0

    def csrf_ok(self):
        name, value = CSRF_HEADER
        if self.headers.get(name) != value:
            return False
        if not (self.headers.get("Content-Type") or "").lower().startswith("application/json"):
            return False
        origin = self.headers.get("Origin")
        if origin and urlsplit(origin).netloc != self.headers.get("Host"):
            return False
        return self.headers.get("Sec-Fetch-Site", "same-origin") in ("same-origin", "none")

    # -- routing
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/healthz":
            if Handler.hub is not None and not Handler.hub.alive():
                return self.send(500, "live hub is not running\n")  # Kubernetes restarts the pod
            return self.send(200, "ok\n")
        if path == BASE + "/banner.js":
            return self.send_file("banner.js", "application/javascript", {"Cache-Control": "no-cache"})
        if path == BASE + "/fx.css":
            return self.send_file("fx.css", "text/css", {"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"})
        if path == BASE + "/vendor/anime.umd.min.js":
            # Versioned by the ?v= the loader adds, so it can be cached for good.
            return self.send_file(
                "anime.umd.min.js",
                "application/javascript",
                {"Cache-Control": "public, max-age=31536000, immutable", "X-Content-Type-Options": "nosniff"},
                directory=VENDOR_DIR,
            )
        if path == BASE + "/banner.json":
            return self.send(200, json.dumps(Handler.banner.public()), "application/json", {"Cache-Control": "no-store"})
        if path in ADMIN_FILES or path in (BASE + "/admin", BASE + "/admin/") or path == BASE + "/api/state":
            return self.admin_get(path)
        self.send(404, "not found")

    def admin_get(self, path):
        status, user = self.authenticate()
        is_api = path.startswith(BASE + "/api/")
        if status == "unauthenticated":
            if is_api:
                return self.send_json(401, {"error": "Sign in to Coder first."})
            return self.send(302, "", headers={"Location": "/login?redirect=" + "%2F__banner%2Fadmin", "Cache-Control": "no-store"})
        if status == "forbidden":
            if is_api:
                return self.send_json(403, {"error": "Only Coder admins can change the banner."})
            page = "<!doctype html><meta charset=utf-8><title>Not allowed</title><h1>Not allowed</h1><p>Only Coder admins can change the banner. You are signed in as <b>%s</b>.</p>" % (
                (user or {}).get("username", "unknown").replace("<", "&lt;")
            )
            return self.send(403, page, "text/html; charset=utf-8", self.ADMIN_HEADERS)
        if status != "ok":
            message = "Could not verify your Coder session right now. Try again in a moment."
            if is_api:
                return self.send_json(503, {"error": message})
            return self.send(503, message, headers=self.ADMIN_HEADERS)

        if is_api:
            return self.send_json(200, {"user": user, **Handler.banner.summary(), "subscribers": self.subscribers()})
        name, content_type, directory = ADMIN_FILES.get(path) or ("admin.html", "text/html; charset=utf-8", None)
        return self.send_file(name, content_type, self.ADMIN_HEADERS, directory=directory)

    def do_POST(self):
        self.close_connection = True  # early rejections leave the body unread; do not reuse the socket
        path = urlsplit(self.path).path
        routes = {BASE + "/api/banner", BASE + "/api/reappear", BASE + "/api/reset"}
        if path not in routes:
            return self.send(404, "not found")
        if not self.csrf_ok():
            return self.send_json(403, {"error": "Request rejected (cross-site protection)."})
        status, user = self.authenticate()
        if status == "unauthenticated":
            return self.send_json(401, {"error": "Your Coder session has expired. Sign in again."})
        if status == "forbidden":
            return self.send_json(403, {"error": "Only Coder admins can change the banner."})
        if status != "ok":
            return self.send_json(503, {"error": "Could not verify your Coder session right now. Try again in a moment."})

        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length > 16384:
                return self.send_json(413, {"error": "Request too large."})
            payload = json.loads(self.rfile.read(length) or b"{}")
            who = user["username"]
            if path.endswith("/banner"):
                Handler.banner.save(payload, who)
            elif path.endswith("/reappear"):
                Handler.banner.reappear(who)
            else:
                Handler.banner.reset(who)
        except BannerError as e:
            return self.send_json(400, {"error": str(e)})
        except ValueError:
            return self.send_json(400, {"error": "Invalid JSON."})
        except Exception as e:
            log(event="write-failed", error=str(e))
            return self.send_json(500, {"error": "Could not save the banner. Check the banner service logs."})
        # Push it to every open tab right now. `delivered` = how many tabs were connected to receive it.
        delivered = Handler.hub.publish(Handler.banner.public()) if Handler.hub is not None else 0
        log(event="banner-published", user=who, delivered=delivered)
        return self.send_json(200, {"user": user, **Handler.banner.summary(), "subscribers": self.subscribers(), "delivered": delivered})


def main():
    store = FileStore(STATE_FILE) if STATE_FILE else KubeStore(STATE_CONFIGMAP)
    Handler.banner = Banner(store)
    if LIVE_ENABLED:
        import live  # noqa: imported here so LIVE_ENABLED=0 needs nothing from it

        Handler.hub = live.Hub(Handler.banner, LIVE_PORT, LIVE_MAX_CLIENTS, LIVE_HEARTBEAT, LIVE_STALE, LIVE_WATCH)
        Handler.hub.start()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    server.daemon_threads = True
    log(event="listening", port=PORT, coder=CODER_URL, admin_roles=sorted(ADMIN_ROLES))
    server.serve_forever()


if __name__ == "__main__":
    main()

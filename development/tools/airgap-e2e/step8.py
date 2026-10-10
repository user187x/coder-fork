"""DEPLOY.md step 8, as a browser inside the air-gapped network would do it (runs in a pod, standard library only)."""
import html, http.cookiejar, json, re, ssl, sys, time, urllib.error, urllib.parse, urllib.request

CODER = "https://coder.airgap.test"
ADMIN_EMAIL, ADMIN_PASSWORD = "admin@admin.com", "admin"          # the chart's global.firstUser defaults
CTX = ssl.create_default_context(cafile="/ca/ca.crt")
jar = http.cookiejar.CookieJar()
web = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar), urllib.request.HTTPSHandler(context=CTX))
results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + name + (" - " + detail if detail else ""), flush=True)


def get(url, data=None, headers=None, method=None):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with web.open(req, timeout=30) as r:
            return r.status, r.read(), r.geturl()
    except urllib.error.HTTPError as e:
        return e.code, e.read(), url


def api(path, body=None, method=None, token=None):
    """API call with a session token (API clients send it as a header; cookie POSTs would need the CSRF token)."""
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Coder-Session-Token"] = token
    req = urllib.request.Request(CODER + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30, context=CTX) as r:
            s, b = r.status, r.read()
    except urllib.error.HTTPError as e:
        s, b = e.code, e.read()
    try:
        return s, json.loads(b or b"null")
    except ValueError:
        return s, b


def roles(me):
    return [r.get("name") for r in (me.get("roles") or [])]


s, b, _ = get(CODER + "/healthz"); check("Coder answers over HTTPS through the Gateway (CA-verified)", s == 200, str(s))
s, info = api("/api/v2/buildinfo"); check("Coder version is the fork's", s == 200 and "platform.4" in info.get("version", ""), info.get("version", ""))
s, am = api("/api/v2/users/authmethods")
check("Keycloak (OIDC) sign-in is on", am.get("oidc", {}).get("enabled") is True)
check("password sign-in is on", am.get("password", {}).get("enabled") is True)

# --- the default admin, with its password ---
s, b = api("/api/v2/users/login", {"email": ADMIN_EMAIL, "password": "not-the-password"})
check("a wrong admin password is refused", s == 401, str(s))
s, login = api("/api/v2/users/login", {"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
check("admin@admin.com / admin signs in", s in (200, 201), str(s))
admin = login.get("session_token") if isinstance(login, dict) else None
s, me = api("/api/v2/users/me", token=admin)
check("...as username admin, an owner, with a password login",
      s == 200 and me.get("username") == "admin" and "owner" in roles(me) and me.get("login_type") == "password",
      "%s %s %s" % (me.get("username"), roles(me), me.get("login_type")) if s == 200 else str(s))

# --- Keycloak sign-in for everyone else (authorization code flow, like a browser) ---
s, page, url = get(CODER + "/api/v2/users/oidc/callback")
check("Coder redirects to Keycloak", "keycloak.airgap.test" in url and s == 200, url.split("?")[0])
m = re.search(r'<form[^>]+action="([^"]+)"', page.decode(errors="replace"))
check("Keycloak shows its login form", bool(m))
if m:
    form = urllib.parse.urlencode({"username": "tester", "password": "Tester-pw-123", "credentialId": ""}).encode()
    s, b, url = get(html.unescape(m.group(1)), form, {"Content-Type": "application/x-www-form-urlencoded"})
    check("Keycloak accepts the credentials and sends the browser back to Coder", url.startswith(CODER), url.split("?")[0])
user = next((c.value for c in jar if c.name == "coder_session_token"), None)
s, me = api("/api/v2/users/me", token=user)
check("a Keycloak user signs in to Coder", s == 200 and me.get("login_type") == "oidc",
      "as %s <%s> (%s)" % (me.get("username"), me.get("email"), me.get("login_type")) if s == 200 else str(s))
check("...with a normal account (not an owner)", s == 200 and "owner" not in roles(me), str(roles(me)))

# --- what the install should have produced ---
s, tpls = api("/api/v2/templates", token=admin)
names = [t["name"] for t in tpls] if s == 200 else []
check("template ubuntu-vnc-desktop was pushed", "ubuntu-vnc-desktop" in names, str(names))
s, b, _ = get(CODER + "/__coder-ui/healthz"); check("platform service /__coder-ui/ through the Gateway", s == 200, str(s))
s, b, _ = get(CODER + "/__icons/marimo.svg"); check("icon server /__icons/ through the Gateway", s == 200 and b"<svg" in b, str(s))
s, b, _ = get(CODER + "/__banner/banner.json")
banner = json.loads(b) if s == 200 else {}
check("banner service /__banner/ through the Gateway", s == 200, str(s))
check("the banner starts blank (hidden, no message)", banner.get("enabled") is False and banner.get("message") == "",
      "enabled=%s message=%r" % (banner.get("enabled"), banner.get("message")))
s, b, _ = get(CODER + "/__banner/admin"); check("banner admin page loads", s in (200, 302, 303, 401, 403), str(s))
s, b, _ = get(CODER + "/", headers={"Accept": "text/html"}); check("dashboard HTML loads", s == 200 and b"<html" in b.lower(), str(s))

# --- a workspace, end to end (as the admin) ---
if "ubuntu-vnc-desktop" in names:
    tpl = next(t for t in tpls if t["name"] == "ubuntu-vnc-desktop")
    s, ws = api("/api/v2/organizations/%s/members/me/workspaces" % tpl["organization_id"],
                {"template_id": tpl["id"], "name": "e2e"}, token=admin)
    check("workspace created", s in (200, 201), "" if s in (200, 201) else "%s %s" % (s, str(ws)[:700]))
    if s in (200, 201):
        deadline, status, agent = time.time() + 900, "", {}
        while time.time() < deadline:
            s, w = api("/api/v2/workspaces/" + ws["id"], token=admin)
            status = w["latest_build"]["job"]["status"]
            agents = [a for r in w["latest_build"]["resources"] for a in r.get("agents") or []]
            agent = agents[0] if agents else {}
            if status == "failed" or (status == "succeeded" and agent.get("status") == "connected"):
                break
            time.sleep(10)
        check("workspace build succeeded", status == "succeeded", status + (" " + w["latest_build"]["job"].get("error", "") if status == "failed" else ""))
        check("workspace agent connected to Coder", agent.get("status") == "connected", str(agent.get("status")))
        time.sleep(60)
        s, w = api("/api/v2/workspaces/" + ws["id"], token=admin)
        agents = [a for r in w["latest_build"]["resources"] for a in r.get("agents") or []]
        if agents:
            a = agents[0]
            print("INFO agent lifecycle: %s; apps: %s" % (a.get("lifecycle_state"),
                  ", ".join("%s=%s" % (x.get("slug"), x.get("health")) for x in a.get("apps") or [])))

print("\n%d/%d checks passed" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)

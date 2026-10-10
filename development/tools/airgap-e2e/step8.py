"""DEPLOY.md step 8, as a browser inside the air-gapped network would do it (runs in a pod, standard library only)."""
import html, http.cookiejar, json, re, ssl, sys, time, urllib.error, urllib.parse, urllib.request

CODER = "https://coder.airgap.test"
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


def api(path, body=None, method=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    token = next((c.value for c in jar if c.name == "coder_session_token"), None)
    if token:  # API clients send the session as a header (cookie-authenticated POSTs need the browser's CSRF token)
        headers["Coder-Session-Token"] = token
    s, b, _ = get(CODER + path, data, headers, method)
    try:
        return s, json.loads(b or b"null")
    except ValueError:
        return s, b


s, b, _ = get(CODER + "/healthz"); check("Coder answers over HTTPS through the Gateway (CA-verified)", s == 200, str(s))
s, info = api("/api/v2/buildinfo"); check("Coder version is the fork's", s == 200 and "platform.4" in info.get("version", ""), info.get("version", ""))
s, am = api("/api/v2/users/authmethods")
check("Keycloak (OIDC) sign-in is on", am.get("oidc", {}).get("enabled") is True)
check("password sign-in is off", am.get("password", {}).get("enabled") is False)
s, b = api("/api/v2/users/login", {"email": "tester@airgap.test", "password": "x"})
check("password login refused", s in (401, 403), str(s))

# --- Keycloak sign-in (authorization code flow, like a browser) ---
s, page, url = get(CODER + "/api/v2/users/oidc/callback")
check("Coder redirects to Keycloak", "keycloak.airgap.test" in url and s == 200, url.split("?")[0])
m = re.search(r'<form[^>]+action="([^"]+)"', page.decode(errors="replace"))
check("Keycloak shows its login form", bool(m))
if m:
    form = urllib.parse.urlencode({"username": "tester", "password": "Tester-pw-123", "credentialId": ""}).encode()
    s, b, url = get(html.unescape(m.group(1)), form, {"Content-Type": "application/x-www-form-urlencoded"})
    check("Keycloak accepts the credentials and sends the browser back to Coder", url.startswith(CODER), url.split("?")[0])
s, me = api("/api/v2/users/me")
check("signed in to Coder", s == 200, "as %s <%s>" % (me.get("username"), me.get("email")) if s == 200 else str(s))
roles = [r.get("name") for r in (me.get("roles") or [])] if s == 200 else []
check("the Keycloak account with global.firstUser.email is the owner", "owner" in roles, str(roles))
check("...and it is the bootstrap user, now linked to Keycloak", me.get("login_type") == "oidc" and me.get("username") == "admin",
      "%s / %s" % (me.get("username"), me.get("login_type")))

# --- what the install should have produced ---
s, tpls = api("/api/v2/templates")
names = [t["name"] for t in tpls] if s == 200 else []
check("template ubuntu-vnc-desktop was pushed", "ubuntu-vnc-desktop" in names, str(names))
s, b, _ = get(CODER + "/__coder-ui/healthz"); check("platform service /__coder-ui/ through the Gateway", s == 200, str(s))
s, b, _ = get(CODER + "/__icons/marimo.svg"); check("icon server /__icons/ through the Gateway", s == 200 and b"<svg" in b, str(s))
s, b, _ = get(CODER + "/__banner/banner.json"); check("banner service /__banner/ through the Gateway", s == 200, str(s))
s, b, _ = get(CODER + "/", headers={"Accept": "text/html"}); check("dashboard HTML loads", s == 200 and b"<html" in b.lower(), str(s))

# --- a workspace, end to end ---
if "ubuntu-vnc-desktop" in names:
    tpl = next(t for t in tpls if t["name"] == "ubuntu-vnc-desktop")
    s, ws = api("/api/v2/organizations/%s/members/me/workspaces" % tpl["organization_id"], {"template_id": tpl["id"], "name": "e2e"})
    check("workspace created", s in (200, 201), "" if s in (200, 201) else "%s %s" % (s, str(ws)[:700]))
    if s in (200, 201):
        deadline, status, agent = time.time() + 900, "", {}
        while time.time() < deadline:
            s, w = api("/api/v2/workspaces/" + ws["id"])
            status = w["latest_build"]["job"]["status"]
            agents = [a for r in w["latest_build"]["resources"] for a in r.get("agents") or []]
            agent = agents[0] if agents else {}
            if status == "failed" or (status == "succeeded" and agent.get("status") == "connected"):
                break
            time.sleep(10)
        check("workspace build succeeded", status == "succeeded", status + (" " + w["latest_build"]["job"].get("error", "") if status == "failed" else ""))
        check("workspace agent connected to Coder", agent.get("status") == "connected", str(agent.get("status")))
        time.sleep(60)
        s, w = api("/api/v2/workspaces/" + ws["id"])
        agents = [a for r in w["latest_build"]["resources"] for a in r.get("agents") or []]
        if agents:
            a = agents[0]
            print("INFO agent lifecycle: %s; apps: %s" % (a.get("lifecycle_state"),
                  ", ".join("%s=%s" % (x.get("slug"), x.get("health")) for x in a.get("apps") or [])))

print("\n%d/%d checks passed" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)

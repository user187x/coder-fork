"""Lock Down, live: a throwaway workspace (deleted afterwards) locked while it runs and while it is stopped, checked
through an admin's web terminal (as an admin inspecting a runaway agent would) and kubectl exec (as the workspace's
own user trying to undo it). Needs kubectl on the deployment's cluster.

  python3 lockdown_live.py --url https://coder.example.com [--ca ca.crt] [--admin-email ... --admin-password ...]
      [--template ubuntu-vnc-desktop] [--registry-url http://coder-image-registry.coder.svc:5000/v2/]

The network probe is a service inside the cluster (the image builder's registry): Lock Down must cut that too.
Exit status 1 when a check fails."""
import argparse, json, os, re, ssl, subprocess, sys, time, urllib.request, urllib.error
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from live import pty_echo  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--url", required=True); ap.add_argument("--ca")
ap.add_argument("--admin-email", default="admin@admin.com"); ap.add_argument("--admin-password", default="admin")
ap.add_argument("--template", default="ubuntu-vnc-desktop"); ap.add_argument("--name", default="verify-lockdown")
ap.add_argument("--registry-url", default="http://coder-image-registry.coder.svc:5000/v2/")
ARGS = ap.parse_args()
U = ARGS.url.rstrip("/")
CTX = ssl.create_default_context(cafile=ARGS.ca) if ARGS.ca else ssl.create_default_context()
NAME = ARGS.name
results = []


def check(name, ok, detail=""):
    results.append(ok)
    print("%s %s%s" % ("PASS" if ok else "FAIL", name, (" - " + str(detail)) if detail else ""), flush=True)


def call(path, body=None, method=None, tok=None):
    h = {"Content-Type": "application/json", "X-Requested-With": "coder-ui"}
    if tok:
        h["Coder-Session-Token"] = tok
    r = urllib.request.Request(U + path, data=json.dumps(body).encode() if body is not None else None, headers=h,
                               method=method)
    try:
        with urllib.request.urlopen(r, timeout=180, context=CTX) as x:
            return x.status, json.loads(x.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:300]


def pod():
    return subprocess.run(["kubectl", "-n", "coder", "get", "pods", "-l", "com.coder.workspace.id=" + WID, "-o",
                           "jsonpath={.items[0].metadata.name}"], capture_output=True, text=True).stdout.strip()


def as_user(script):
    """The workspace's own user, in it (as an agent there would)."""
    p = subprocess.run(["kubectl", "-n", "coder", "exec", "-i", pod(), "-c", "dev", "--", "bash", "-s"],
                       input=script, capture_output=True, text=True, timeout=120)
    return (p.stdout + p.stderr).strip()


def admin_terminal(cmd):
    """An admin's web terminal (Coder's reconnecting PTY, through the agent's connection to Coder): the values the
    command printed (the last of each: the typed command's echo comes first)."""
    out = pty_echo(U, CTX, A, agent_id(), cmd + "; echo verify-pty-$((6*7))", timeout=60).decode(errors="replace")
    found = {}
    for k, v in re.findall(r"\b(NET|CODER|SUDO|RUNAWAY)=([0-9A-Za-z]+)", out):
        found[k] = v
    return found or {"raw": out[-300:]}


def agent_id():
    w = call("/api/v2/workspaces/" + WID, tok=A)[1]
    return [a for r in w["latest_build"]["resources"] for a in (r.get("agents") or [])][0]["id"]


def agent_state():
    w = call("/api/v2/workspaces/" + WID, tok=A)[1]
    ag = [a for r in w["latest_build"]["resources"] for a in (r.get("agents") or [])]
    return (ag[0]["status"], ag[0]["lifecycle_state"]) if ag else (None, None)


def wait_ready(timeout=600):
    t0 = time.time()
    while time.time() - t0 < timeout:
        w = call("/api/v2/workspaces/" + WID, tok=A)[1]
        ag = [a for r in w["latest_build"]["resources"] for a in (r.get("agents") or [])]
        if w["latest_build"]["status"] == "running" and ag and ag[0]["lifecycle_state"] == "ready":
            return time.time() - t0
        time.sleep(5)
    raise RuntimeError("not ready")


PROBE = ("echo NET=$(curl -s -m 5 -o /dev/null -w %{http_code} " + ARGS.registry_url + " 2>/dev/null; true) "
         "CODER=$(curl -s -m 8 -o /dev/null -w %{http_code} " + U + "/healthz 2>/dev/null; true) "
         "SUDO=$(sudo -n id -u 2>/dev/null || echo refused) "
         "RUNAWAY=$(pgrep -c -f 'sleep 7777' || echo 0)")


def lock(mode):
    t0 = time.time()
    s, res = call("/__coder-ui/api/lockdown/" + WID, {"mode": mode}, "POST", tok=A)
    return s, res, round(time.time() - t0, 1)


A = call("/api/v2/users/login", {"email": ARGS.admin_email, "password": ARGS.admin_password})[1]["session_token"]
tpl = [t for t in call("/api/v2/templates", tok=A)[1] if t["name"] == ARGS.template][0]
s, w = call("/api/v2/users/me/workspaces", {"template_id": tpl["id"], "name": NAME}, tok=A)
WID = w["id"]
print("workspace", NAME, WID, "ready after %ds" % wait_ready())
try:
    # the stand-in for a runaway agent: a process started before the lock
    as_user("nohup sleep 7777 >/dev/null 2>&1 &")
    base = dict(re.findall(r"(\w+)=(\S+)", as_user(PROBE)))
    check("before: network, Coder and sudo work", base.get("NET") == "200" and base.get("CODER") == "200"
          and base.get("SUDO") == "0", base)

    # --- running: Complete
    s, res, took = lock("complete")
    check("Complete on the running workspace, at once", s == 200 and res.get("applied") == "now" and res.get("inForce")
          and not res.get("error"), "%s in %ss: %s" % (s, took, {k: res.get(k) for k in ("modes", "applied", "inForce", "error")}))
    st = agent_state()
    check("its agent stays connected", st == ("connected", "ready"), st)
    t = admin_terminal(PROBE.replace("echo ", "printf '%s ' ", 0) if False else PROBE)
    check("admin's web terminal opens while locked", "raw" not in t, t)
    check("network cut (the cluster's other services too)", t.get("NET") == "000", t.get("NET"))
    check("Coder still reachable", t.get("CODER") == "200", t.get("CODER"))
    check("sudo refused", t.get("SUDO") == "refused", t.get("SUDO"))
    check("the runaway process still runs, for inspection", t.get("RUNAWAY") == "1", t.get("RUNAWAY"))
    undo = as_user("sudo -n iptables -F 2>&1 | head -1; iptables -F 2>&1 | head -1; curl -s -m 5 -o /dev/null -w 'NET=%{http_code}' " + ARGS.registry_url + " 2>/dev/null; true")
    check("the workspace's user cannot undo it", "NET=000" in undo, undo.replace("\n", " | "))

    # --- running: Network only (sudo back, but even root cannot lift the network)
    s, res, took = lock("network")
    t = dict(re.findall(r"(\w+)=(\S+)", as_user(PROBE)))
    check("Network only: sudo works again, network still cut", s == 200 and t.get("SUDO") == "0"
          and t.get("NET") == "000" and t.get("CODER") == "200", t)
    undo = as_user("sudo -n iptables -F 2>&1 | head -1; curl -s -m 5 -o /dev/null -w 'NET=%{http_code}' " + ARGS.registry_url + " 2>/dev/null; true")
    check("...and root (sudo) in the workspace cannot flush its rules", "NET=000" in undo,
          undo.replace("\n", " | "))

    # --- running: Sudo only
    s, res, took = lock("sudo")
    t = dict(re.findall(r"(\w+)=(\S+)", as_user(PROBE)))
    check("Sudo Command only: network back, sudo refused", t.get("NET") == "200" and t.get("SUDO") == "refused", t)

    # --- lifted
    s, res, took = lock("off")
    t = dict(re.findall(r"(\w+)=(\S+)", as_user(PROBE)))
    check("lifted: everything back, the process kept running throughout", s == 200 and not res.get("locked")
          and t.get("NET") == "200" and t.get("SUDO") == "0" and t.get("RUNAWAY") == "1", t)

    # --- stopped: locked off, enforced from the start
    call("/api/v2/workspaces/%s/builds" % WID, {"transition": "stop"}, "POST", tok=A)
    for _ in range(60):
        if call("/api/v2/workspaces/" + WID, tok=A)[1]["latest_build"]["status"] == "stopped":
            break
        time.sleep(5)
    s, res, took = lock("complete")
    check("Complete on the stopped workspace: from its next start", s == 200 and res.get("applied") == "next start",
          {k: res.get(k) for k in ("modes", "applied", "running")})
    call("/api/v2/workspaces/%s/builds" % WID, {"transition": "start"}, "POST", tok=A)
    print("started again, ready after %ds" % wait_ready())
    u = dict(re.findall(r"(\w+)=(\S+)", as_user(PROBE)))
    check("at start (as the workspace's user): network cut, sudo refused, Coder reachable",
          u.get("NET") == "000" and u.get("SUDO") == "refused" and u.get("CODER") == "200", u)
    t = admin_terminal(PROBE)
    check("at start: network cut and sudo refused before anyone signed in, admin's terminal works",
          t.get("NET") == "000" and t.get("SUDO") == "refused" and t.get("CODER") == "200", t)
    init = subprocess.run(["kubectl", "-n", "coder", "logs", pod(), "-c", "lockdown"], capture_output=True, text=True).stdout.strip()
    check("the pod's init container set Network", "network on" in init, init)
    s, res, took = lock("off")
    t = dict(re.findall(r"(\w+)=(\S+)", as_user(PROBE)))
    check("lifted again, live", t.get("NET") == "200" and t.get("SUDO") == "0", t)
finally:
    lock("off")
    print("delete:", call("/api/v2/workspaces/%s/builds" % WID, {"transition": "delete"}, "POST", tok=A)[0])
    call("/api/v2/users/logout", {}, "POST", tok=A)
print("%d/%d checks passed" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)

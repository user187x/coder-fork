#!/usr/bin/env python3
"""General > Keycloak through its API, as an owner: connect with a Keycloak admin login, then print every check.
   test/keycloak-view-check.py <coder url> <connect-to address> <ca file> <owner token> <kc admin user> <kc admin password>
Exit 1 if a check is an error, or the group / secret / claim checks aren't OK."""
import json
import ssl
import sys
import urllib.request

base, addr, ca, token, kc_user, kc_pw = sys.argv[1:7]
host = base.split("//", 1)[1]
ctx = ssl.create_default_context(cafile=ca)


def call(method, path, body=None):
    # connect to addr, but keep the hostname for TLS and routing (like curl --connect-to)
    import http.client
    conn = http.client.HTTPSConnection(addr, 443, context=ctx, timeout=60)
    conn.sock = ctx.wrap_socket(__import__("socket").create_connection((addr, 443), 60), server_hostname=host)
    headers = {"Host": host, "Cookie": "coder_session_token=" + token, "X-Requested-With": "coder-ui",
               "Content-Type": "application/json"}
    conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
    r = conn.getresponse()
    data = r.read()
    return r.status, (json.loads(data) if data else None)


status, report = call("GET", "/__coder-ui/api/keycloak")
assert status == 200, (status, report)
if not report["facts"].get("keycloakConnected"):
    status, out = call("POST", "/__coder-ui/api/keycloak/connect",
                       {"username": kc_user, "password": kc_pw, "settings": report["facts"]["settings"]})
    print("connect:", status, (out or {}).get("done") or out)
    assert status == 200, out
    status, report = call("GET", "/__coder-ui/api/keycloak")
bad = 0
for c in report["checks"]:
    print("%-8s %-24s %-45s %s" % (c["status"].upper(), c["id"][:24], c["title"][:45], (c.get("current") or "")[:110]))
    if c["status"] == "error":
        bad += 1
need = {"kc.groups.claim", "coder.secret", "coder.ca"} | {c["id"] for c in report["checks"] if c["id"].startswith("kc.group:")}
for c in report["checks"]:
    if c["id"] in need and c["status"] != "ok":
        bad += 1
print("group settings:", report["facts"].get("groupSettings"), "| OIDC secret:", report["facts"].get("oidcSecret"))
print("ALL OK" if bad == 0 else "%d PROBLEM(S)" % bad)
sys.exit(1 if bad else 0)

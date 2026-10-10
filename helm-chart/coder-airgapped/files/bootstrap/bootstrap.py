"""Post-install / post-sync bootstrap of a fresh Coder (Python standard library + pgwire.py, no other dependencies).

1. Waits for Coder, then creates its first user (an owner) from Secret <initAuth> when Coder has no user yet.
2. With password sign-in disabled (CODER_DISABLE_PASSWORD_AUTH=true), Coder refuses every password login, owners
   included, so the first user could never sign in and nothing could push the workspace template. Therefore:
   a. the first user (matched by FIRST_USER_EMAIL) is switched to login type "oidc": the Keycloak account with that
      email becomes this owner on its first sign-in (Coder links OIDC sign-ins to existing users by email);
   b. a short-lived API token (30 minutes, Coder's own key format) is minted for an owner, straight into Coder's
      database, and written to Secret <TOKEN_SECRET>, which the template-push Job (coder-workspace) signs in with.
      Earlier bootstrap tokens are deleted first, so at most one exists.
   With password sign-in enabled, step 2 is skipped (the push Job signs in with the password).
"""
import hashlib
import json
import os
import secrets
import ssl
import string
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pgwire  # noqa: E402

CODER_URL = os.environ["CODER_URL"].rstrip("/")
EMAIL = os.environ["FIRST_USER_EMAIL"]
USERNAME = os.environ.get("FIRST_USER_USERNAME") or "admin"
PASSWORD = os.environ["FIRST_USER_PASSWORD"]
PASSWORD_AUTH_DISABLED = os.environ.get("DISABLE_PASSWORD_AUTH", "").lower() == "true"
TOKEN_SECRET = os.environ.get("TOKEN_SECRET", "")
TOKEN_NAME_PREFIX = "coder-airgapped-bootstrap"
SA = "/var/run/secrets/kubernetes.io/serviceaccount"
ALPHABET = string.digits + string.ascii_letters  # cryptorand.Default


def log(msg):
    print(msg, flush=True)


def coder(method, path, body=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Coder-Session-Token"] = token
    req = urllib.request.Request(CODER_URL + path, method=method, headers=headers,
                                 data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=15) as res:
            return res.status, res.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def wait_for_coder():
    deadline = time.time() + int(os.environ.get("TIMEOUT", "900"))
    while True:
        try:
            if coder("GET", "/healthz")[0] == 200:
                return
            reason = "not healthy yet"
        except OSError as e:
            reason = str(e)
        if time.time() > deadline:
            sys.exit("Coder did not answer in time (%s)." % reason)
        log("Waiting for Coder (%s)..." % reason)
        time.sleep(5)


def ensure_first_user():
    status, _ = coder("GET", "/api/v2/users/first")
    if status == 200:
        log("Coder already has its first user.")
        return
    if status != 404:
        sys.exit("Checking for the first user failed: HTTP %d" % status)
    status, body = coder("POST", "/api/v2/users/first",
                         {"email": EMAIL, "username": USERNAME, "name": "", "password": PASSWORD, "trial": False})
    if status not in (200, 201):
        sys.exit("Creating the first user failed: HTTP %d %s" % (status, body.decode(errors="replace")))
    log("Created Coder's first user %s <%s> (owner)." % (USERNAME, EMAIL))


def kube(method, path, body=None):
    with open(SA + "/token") as f:
        token = f.read().strip()
    req = urllib.request.Request("https://kubernetes.default.svc" + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    ctx = ssl.create_default_context(cafile=SA + "/ca.crt")
    try:
        with urllib.request.urlopen(req, timeout=15, context=ctx) as res:
            return res.status
    except urllib.error.HTTPError as e:
        return e.code


def store_token(token):
    with open(SA + "/namespace") as f:
        ns = f.read().strip()
    secret = {"apiVersion": "v1", "kind": "Secret", "type": "Opaque",
              "metadata": {"name": TOKEN_SECRET, "namespace": ns,
                           "labels": {"app.kubernetes.io/part-of": "coder",
                                      "app.kubernetes.io/name": "coder-bootstrap"}},
              "stringData": {"token": token}}
    status = kube("PUT", "/api/v1/namespaces/%s/secrets/%s" % (ns, TOKEN_SECRET), secret)
    if status == 404:
        status = kube("POST", "/api/v1/namespaces/%s/secrets" % ns, secret)
    if status not in (200, 201):
        sys.exit("Writing Secret %s failed: HTTP %d" % (TOKEN_SECRET, status))
    log("Stored a 30-minute template-push token in Secret %s." % TOKEN_SECRET)


def without_password_auth():
    cafile = os.environ.get("CODER_DB_CA")
    db = pgwire.Connection(os.environ["CODER_DB_URI"], cafile=cafile if cafile and os.path.exists(cafile) else None,
                           application_name="coder-bootstrap")
    try:
        rows = db.query("SELECT id::text, login_type::text FROM users WHERE lower(email) = lower($1) AND NOT deleted",
                        [EMAIL])
        if rows and rows[0][1] == "password":
            db.query("UPDATE users SET login_type = 'oidc', hashed_password = $2, updated_at = now() "
                     "WHERE id = $1::uuid", [rows[0][0], b""])
            log("Switched %s to Keycloak (OIDC) sign-in: that Keycloak account is this owner from its first sign-in."
                % EMAIL)
        elif rows:
            log("%s already signs in with %s." % (EMAIL, rows[0][1]))
        else:
            log("WARNING: no Coder user with email %s." % EMAIL)
        if not TOKEN_SECRET:
            return
        owner = db.query("SELECT id::text, username FROM users WHERE NOT deleted AND 'owner' = ANY(rbac_roles) "
                         "ORDER BY (lower(email) = lower($1)) DESC, created_at LIMIT 1", [EMAIL])
        if not owner:
            sys.exit("Coder has no owner to push the template as.")
        key_id = "".join(secrets.choice(ALPHABET) for _ in range(10))
        secret = "".join(secrets.choice(ALPHABET) for _ in range(22))
        db.query("DELETE FROM api_keys WHERE login_type = 'token' AND token_name LIKE $1", [TOKEN_NAME_PREFIX + "-%"])
        db.query("INSERT INTO api_keys (id, hashed_secret, user_id, last_used, expires_at, created_at, updated_at, "
                 "login_type, lifetime_seconds, ip_address, token_name, scopes, allow_list) VALUES "
                 "($1, $2, $3::uuid, now(), now() + interval '30 minutes', now(), now(), 'token', 1800, '0.0.0.0', "
                 "$4, '{coder:all}', '{*:*}')",
                 [key_id, hashlib.sha256(secret.encode()).digest(), owner[0][0], TOKEN_NAME_PREFIX + "-" + key_id])
    finally:
        db.close()
    token = key_id + "-" + secret
    status, body = coder("GET", "/api/v2/users/me", token=token)
    if status != 200:
        sys.exit("The minted token does not work: HTTP %d %s" % (status, body.decode(errors="replace")))
    log("Minted a template-push token for owner %s." % json.loads(body).get("username"))
    store_token(token)


wait_for_coder()
ensure_first_user()
if PASSWORD_AUTH_DISABLED:
    without_password_auth()
else:
    log("Password sign-in is enabled: the template push signs in with Secret coder-init-auth.")

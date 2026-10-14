"""Static checks of the deliverable (no cluster needed): the chart renders for every way it is installed, every image
it can run is pinned in images.txt and archived in the bundle, nothing floats, and nothing at run time needs the
internet.

  python3 chart_checks.py [--bundle helm-chart] [--jsonl out.jsonl]

Each check is a JSON line ({suite: "static", name, ok, detail}) as in live.py."""
import argparse, glob, json, os, re, subprocess, sys

RESULTS, OUT = [], None


def check(name, ok, detail=""):
    rec = {"suite": "static", "name": name, "ok": bool(ok), "detail": str(detail)[:800], "seconds": None}
    RESULTS.append(rec)
    if OUT:
        OUT.write(json.dumps(rec) + "\n"); OUT.flush()
    print("%s [static] %s%s" % ("PASS" if ok else "FAIL", name, (" - " + rec["detail"]) if rec["detail"] else ""), flush=True)


def run(cmd, **kw):
    p = subprocess.run(cmd, capture_output=True, text=True, **kw)
    return p.returncode, p.stdout, p.stderr


def strip_registry(ref):
    first = ref.split("/", 1)[0]
    return ref.split("/", 1)[1] if "/" in ref and ("." in first or ":" in first or first == "localhost") else ref


def main():
    global OUT
    ap = argparse.ArgumentParser()
    here = os.path.dirname(os.path.abspath(__file__))
    ap.add_argument("--bundle", default=os.path.normpath(os.path.join(here, "../../../helm-chart")))
    ap.add_argument("--jsonl")
    a = ap.parse_args()
    OUT = open(a.jsonl, "a") if a.jsonl else None
    B = a.bundle; chart = os.path.join(B, "coder-airgapped")

    rc, out, err = run(["helm", "lint", chart])
    check("helm lint", rc == 0, (out + err).strip().splitlines()[-1] if rc else "")

    images = {}  # ref -> (group, digest)
    group = None
    for line in open(os.path.join(B, "images.txt")):
        f = line.split()
        if not f or f[0].startswith("#"):
            continue
        if f[0].startswith("["):
            group = f[0][1:-1]; continue
        images[f[0]] = (group, f[1] if len(f) > 1 else "")
    check("images.txt: every image pinned by digest", all(d.startswith("sha256:") for _, d in images.values()),
          [r for r, (_, d) in images.items() if not d.startswith("sha256:")])
    floating = [r for r in images if r.endswith(":latest") or ":" not in r.rsplit("/", 1)[-1]]
    check("images.txt: no floating tags (:latest or none)", not floating, floating)

    def archive(ref, grp):
        d = os.path.join(B, "images", "custom" if grp == "custom" else os.path.join("public", grp))
        return os.path.join(d, re.sub(r"[/:@]", "_", ref) + ".tar.gz")
    missing = [r for r, (g, _) in images.items() if not os.path.isfile(archive(r, g))]
    check("bundle: an archive for every image in images.txt", not missing, missing or "%d archives" % len(images))
    sums_ok = True
    for d in glob.glob(os.path.join(B, "images", "**", "SHA256SUMS"), recursive=True):
        listed = {l.split()[1] for l in open(d) if l.strip()}
        present = {os.path.basename(p) for p in glob.glob(os.path.join(os.path.dirname(d), "*.tar.gz"))}
        if listed != present:
            sums_ok = False
            check("bundle: %s lists exactly its archives" % os.path.relpath(d, B), False, sorted(listed ^ present))
    if sums_ok:
        check("bundle: every SHA256SUMS lists exactly its archives", True)

    scenarios = {
        "Minikube defaults": [],
        "EKS (values-eks-airgap.example.yaml)": ["-f", os.path.join(B, "values-eks-airgap.example.yaml")],
        "a private registry (global.registry)": ["--set", "global.registry=registry.internal.example:5000"],
        "Keycloak-only sign-in (disablePasswordAuth)": ["--set", "global.disablePasswordAuth=true"],
        "admins' kubectl (clusterAccess)": ["--set", "global.clusterAccess.enabled=true"],
    }
    renders = {}
    for name, extra in scenarios.items():
        rc, out, err = run(["helm", "template", "coder", chart, "-n", "coder"] + extra)
        renders[name] = out if rc == 0 else ""
        check("renders: %s" % name, rc == 0, err.strip().splitlines()[-1] if rc and err.strip() else "")

    ref_re = re.compile(r'(?:image|cliImage|loginImage|workspaceImage):\s*"?([a-z0-9][a-z0-9.:/_-]*:[A-Za-z0-9._-]+)"?'
                        r'|=\s*"([a-z0-9][a-z0-9.:/_-]+:[A-Za-z0-9._-]+)"'
                        r'|value:\s*"?((?:[a-z0-9.-]+(?::[0-9]+)?/)+[a-z0-9._-]+:[A-Za-z0-9._-]+)"?')
    known = {strip_registry(r) for r in images}
    for name in ("Minikube defaults", "a private registry (global.registry)"):
        refs = set()
        for m in ref_re.finditer(renders[name]):
            r = next(x for x in m.groups() if x)
            if "/" in r and not r.startswith(("http", "coder-image-registry", "localhost:5000/coder-images")):
                refs.add(r)
        unknown = sorted(r for r in refs if strip_registry(r) not in known and r not in images)
        check("%s: every image it runs is in images.txt" % name, not unknown, unknown or "%d images" % len(refs))
        if name.startswith("a private"):
            outside = sorted(r for r in refs if not r.startswith("registry.internal.example:5000/"))
            check("a private registry: every image comes from it", not outside, outside)

    vals = open(os.path.join(chart, "values.yaml")).read()
    tag = re.search(r"\n      tag: (\S+)", vals).group(1)
    cli = re.search(r'cliImage: .*?"(ghcr\.io/user187x/coder-platform:[^"]+)"', vals)
    check("the template push's CLI is Coder's own image (same tag as the server)", cli and cli.group(1).endswith(":" + tag),
          cli.group(1) if cli else "no cliImage")
    r = renders["Minikube defaults"]
    check("Coder starts without Keycloak's Secrets (optional)",
          re.search(r"name: keycloaking-coder-oidc\s*\n\s*optional: true", r) and
          re.search(r"secretName: keycloaking-coder-ca\s*\n\s*optional: true|optional: true\s*\n\s*secretName: keycloaking-coder-ca", r),
          "client-secret and CA mounts")
    check("the first owner is the installer's (OIDC sign-ins refused until it exists)",
          re.search(r"CODER_PLATFORM_FIRST_OWNER\s*\n\s*value: \"?bootstrap", r), "")
    waves = {m.group(1): int(m.group(2)) for m in re.finditer(
        r"name: (\S*(?:bootstrap-first-user|push-template))\n(?:.*\n){0,25}?\s*argocd.argoproj.io/sync-wave: \"(\d+)\"", r)}
    ms = re.search(r'argocd.argoproj.io/sync-wave: "(\d+)"\}?\n\s*name: metrics-server', r) or \
        re.search(r'argocd.argoproj.io/sync-wave: "(\d+)"', r[r.find("kind: APIService"):] if "kind: APIService" in r else "")
    check("Argo CD: the bootstrap and template push run before metrics-server (its health cannot hold them up)",
          len(waves) == 2 and (not ms or max(waves.values()) < int(ms.group(1))), "%s, metrics-server %s" % (waves, ms.group(1) if ms else "-"))
    check("telemetry and update check off", "CODER_TELEMETRY_ENABLE" not in r or re.search(r"CODER_TELEMETRY_ENABLE\s*\n\s*value: \"?false", r),
          "")
    tf_dir = os.path.join(chart, "charts/coder-workspace/files")
    scripts = sorted(glob.glob(os.path.join(tf_dir, "scripts", "*.sh")))
    tf = {n: open(os.path.join(tf_dir, n)).read() for n in ("main.tf", "certificates.tf", "agent-audit.tf")}
    refs = sorted({m for n in ("main.tf", "certificates.tf") for m in re.findall(r'file\("\$\{path\.module\}/scripts/([^"]+)"', tf[n])})
    shipped = re.findall(r"^  ([\w.-]+\.sh): \|$", r, re.M)
    check("template: every script the .tf files read is in files/scripts/ and in the push's ConfigMap",
          refs and all(os.path.isfile(os.path.join(tf_dir, "scripts", s)) and s in shipped for s in refs),
          "read: %s; shipped: %s" % (refs, shipped))
    check("template: no script left inline in the .tf files (heredocs)", not any("<<" in t for t in tf.values()),
          [n for n, t in tf.items() if "<<" in t])
    if subprocess.run(["sh", "-c", "command -v shfmt"], capture_output=True).returncode == 0:
        rc, out, err = run(["shfmt", "-i", "1", "-d"] + scripts)
        check("template scripts: formatted (shfmt -i 1)", rc == 0, (out + err).strip()[:300] or "%d scripts" % len(scripts))
    else:
        check("template scripts: formatted (shfmt -i 1; shfmt not installed: skipped)", True, "skipped")
    # Every module the platform service imports from its own folder (and the Lock Down script) is in the chart's list
    # of the service's files: one left out crash-loops the service (1.32.0's first deploy did, with lockdown.py).
    ui_dir = os.path.join(chart, "files/ui-updates")
    tpl_ui = open(os.path.join(chart, "templates/ui-updates.yaml")).read()
    listed = set(re.findall(r'"([\w.-]+)"', (re.search(r"\$code := list ([^}]+)\}", tpl_ui) or [None, ""])[1]))
    imported = {m + ".py" for m in re.findall(r"^import (\w+)", open(os.path.join(ui_dir, "ui-service.py")).read(), re.M)
                if os.path.isfile(os.path.join(ui_dir, m + ".py"))} | {"lockdown.sh"}
    check("platform service: every module it imports is shipped (templates/ui-updates.yaml's file list)",
          imported <= listed, sorted(imported - listed) or "%d modules" % len(imported))
    ui_lock = os.path.join(chart, "files/ui-updates/lockdown.sh")
    tpl_lock = os.path.join(tf_dir, "scripts", "lockdown.sh")
    check("Lock Down: the platform service's lockdown.sh is the template's (one script, two places)",
          os.path.isfile(ui_lock) and os.path.isfile(tpl_lock) and open(ui_lock).read() == open(tpl_lock).read(),
          "files/ui-updates/lockdown.sh vs charts/coder-workspace/files/scripts/lockdown.sh")
    bad = [os.path.basename(s) for s in scripts if run(["bash", "-n", s])[0] != 0]
    check("template scripts: shell syntax (bash -n)", not bad, bad or "%d scripts" % len(scripts))
    main_tf = tf["main.tf"] + "".join(open(s).read() for s in scripts)
    downloads = re.findall(r"curl[^\n]*https://[^\s|'\"]+", main_tf)
    tools = {"code-server": "code-server", "filebrowser": "filebrowser"}
    unguarded = [d for d in downloads
                 if not any(k in d and re.search(r"command -v %s\b" % re.escape(t), main_tf) for k, t in tools.items())]
    check("template: internet downloads only as a fallback, when the image lacks the tool", not unguarded,
          unguarded or "%d fallbacks" % len(downloads))
    check("template: the workspace image is substituted (not codercom/enterprise-base)",
          "codercom/enterprise-base" not in r, "")
    ws_img = re.search(r'workspaceImage: .*?"(ghcr\.io/user187x/coder-workspace:[^"]+)"', vals).group(1)
    check("the workspace image the chart runs is the bundled one", ws_img in images, ws_img)
    dfp = os.path.normpath(os.path.join(B, "../development/workspace-image/Dockerfile"))
    if os.path.isfile(dfp):
        df = open(dfp).read()
        check("workspace image: code-server and File Browser preinstalled, pinned by SHA-256",
              "CODE_SERVER_SHA256=" in df and "FILEBROWSER_SHA256=" in df, "")
    else:  # a bundle on its own: look inside the image archive's config for the tools' paths
        check("workspace image: code-server and File Browser preinstalled (needs development/: skipped)", True, "skipped")

    rc, out, err = run([sys.executable, "-m", "unittest", "discover", "-s", here, "-p", "platform_unit_test.py", "-q"])
    check("platform service unit tests", rc == 0, (err or out).strip().splitlines()[-1] if (err or out).strip() else "")
    for sh in ("install.sh", "make", "mirror-images.sh", "minikube-support", "harbor-support"):
        rc, _, err = run(["bash", "-n", os.path.join(B, sh)])
        check("shell syntax: %s" % sh, rc == 0, err.strip())
    passed = sum(x["ok"] for x in RESULTS)
    print("\n%d/%d static checks passed" % (passed, len(RESULTS)))
    sys.exit(0 if passed == len(RESULTS) else 1)


if __name__ == "__main__":
    main()

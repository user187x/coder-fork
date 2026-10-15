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


def objects(render):
    """(kind, name) of every object in a helm template output."""
    out = set()
    for doc in render.split("\n---"):
        k = re.search(r"^kind: (\S+)", doc, re.M)
        n = re.search(r"^metadata:\n(?:  .*\n)*?  name: (\S+)", doc, re.M)
        if k and n:
            out.add((k.group(1), n.group(1).strip('"')))
    return out


def decorator_checks(B, chart, full_render):
    """The Argo CD decorator install: the two charts packaged from coder-airgapped, and its two Applications."""
    import shutil, tempfile
    tmp = tempfile.mkdtemp(prefix="decorator-")
    rc, out, err = run([os.path.join(B, "mirror-images.sh"), "decorator-charts", tmp])
    pkgs = {p: next(iter(glob.glob(os.path.join(tmp, "coder-airgapped-%s-*.tgz" % p))), None) for p in ("base", "layer")}
    check("decorator: coder-airgapped-base and coder-airgapped-layer package (mirror-images.sh decorator-charts)",
          rc == 0 and all(pkgs.values()), (err or out).strip()[-300:] if rc else sorted(os.path.basename(p or "") for p in pkgs.values()))
    if not all(pkgs.values()):
        return
    renders = {}
    for name, (pkg, extra) in {"base": (pkgs["base"], []), "base+layer": (pkgs["base"], ["--set", "global.decorator.layer=true"]),
                               "layer": (pkgs["layer"], [])}.items():
        rc, out, err = run(["helm", "template", "coder", pkg, "-n", "coder"] + extra)
        renders[name] = out if rc == 0 else ""
        check("decorator: %s renders" % name, rc == 0, err.strip().splitlines()[-1] if rc and err.strip() else "")
    full, base, layer = objects(full_render), objects(renders["base"]), objects(renders["layer"])
    check("decorator: the base and the layer share no object, and together are coder-airgapped",
          base and layer and not (base & layer) and (base | layer) == full,
          {"both": sorted(base & layer), "missing": sorted(full - base - layer), "extra": sorted((base | layer) - full)}
          if (base & layer) or (base | layer) != full else "%d + %d objects" % (len(base), len(layer)))
    check("decorator: the base is Coder, its database and first owner only",
          {k for k, _ in base} <= {"Deployment", "Service", "ServiceAccount", "Role", "RoleBinding", "HTTPRoute", "Cluster",
                                    "Secret", "ConfigMap", "Job"} and ("Cluster", "coder-db") in base and ("Deployment", "coder") in base
          and not any(n.startswith(("coder-ui", "coder-banner", "coder-workspace-push", "coder-image")) for _, n in base),
          sorted(base))
    plain, dec = renders["base"], renders["base+layer"]
    plain_env = re.findall(r"name: CODER_PLATFORM_(?:SERVICE_ROUTES|FIRST_OWNER)\n\s+value: (\S*)", plain)
    check("decorator: without the layer the base runs upstream Coder, without the fork's settings",
          "/user187x/coder-upstream:" in plain and "/user187x/coder-platform:" not in plain and plain_env == ['""', '""'],
          plain_env)
    dec_env = re.findall(r"name: CODER_PLATFORM_(?:SERVICE_ROUTES|FIRST_OWNER)\n\s+value: (\S*)", dec)
    check("decorator: with global.decorator.layer the base runs the fork's image with its settings",
          "/user187x/coder-platform:" in dec and "/user187x/coder-upstream:" not in dec and all(v not in ('""', "") for v in dec_env),
          dec_env)
    # The Applications: the plain one is the decorated one without the layer (application.py --plain), both parse, and
    # they install this chart version.
    argo = os.path.join(B, "argocd")
    made = os.path.join(tmp, "plain.yaml")
    rc, _, err = run([sys.executable, os.path.join(argo, "application.py"), "--plain",
                      os.path.join(argo, "coder-decorated-application.yaml"), made])
    same = rc == 0 and open(made).read() == open(os.path.join(argo, "coder-plain-application.yaml")).read()
    check("decorator: coder-plain-application.yaml is coder-decorated-application.yaml without the layer (application.py --plain)",
          same, err.strip() if rc else ("" if same else "differs: python3 argocd/application.py --plain "
                                        "coder-decorated-application.yaml coder-plain-application.yaml"))
    try:
        import yaml
        version = re.search(r"^version: (\S+)", open(os.path.join(chart, "Chart.yaml")).read(), re.M).group(1)
        detail = {}
        for f, charts in (("coder-decorated-application.yaml", ["coder-airgapped-base", "coder-airgapped-layer"]),
                          ("coder-plain-application.yaml", ["coder-airgapped-base"])):
            app = [d for d in yaml.safe_load_all(open(os.path.join(argo, f))) if d and d.get("kind") == "Application"][0]
            srcs = app["spec"]["sources"]
            g = [s["helm"]["valuesObject"]["global"] for s in srcs]
            ok = ([s["chart"] for s in srcs] == charts and all(s["targetRevision"] == version for s in srcs)
                  and all(x == g[0] for x in g) and g[0]["decorator"]["layer"] == (len(charts) == 2))
            detail[f] = ok
        check("decorator: both Applications parse, install chart %s, and share one global block" % version,
              all(detail.values()), detail)
    except ImportError:
        check("decorator: the Applications parse (needs PyYAML: skipped)", True, "skipped")
    # kubectl (and Argo CD) keep only the last of two equal keys, silently: a value given twice is a value lost
    try:
        import yaml

        class Strict(yaml.SafeLoader):
            pass

        def mapping(loader, node, deep=False):
            seen = set()
            for k, _ in node.value:
                key = loader.construct_object(k, deep=deep)
                if key in seen:
                    raise yaml.constructor.ConstructorError(None, None, "duplicate key %r (line %d)" % (key, k.start_mark.line + 1), k.start_mark)
                seen.add(key)
            return yaml.SafeLoader.construct_mapping(loader, node, deep)
        Strict.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)
        dups = {}
        for f in sorted(glob.glob(os.path.join(argo, "*.yaml")) + glob.glob(os.path.join(B, "values-*.yaml")) + [os.path.join(chart, "values.yaml")]):
            try:
                list(yaml.load_all(open(f), Loader=Strict))
            except yaml.YAMLError as e:
                dups[os.path.relpath(f, B)] = str(e).splitlines()[0]
        check("the Applications and values files: no key given twice (kubectl would keep only the last)", not dups, dups or "")
    except ImportError:
        pass


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
    r = renders["Minikube defaults"]
    forks = sorted(set(re.findall(r'image: "?(\S*/user187x/coder-platform:[^"\s]+)"?', r)))
    check("the template push's CLI is Coder's own image (same tag as the server)", len(forks) == 1, forks)
    decorator_checks(B, chart, r)
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
        # code-server's release download, and File Browser's source (built in the image's filebrowser-* stages)
        check("workspace image: code-server and File Browser preinstalled, pinned by SHA-256",
              "CODE_SERVER_SHA256=" in df and "FILEBROWSER_SRC_SHA256=" in df
              and "COPY --from=filebrowser /out/filebrowser" in df, "")
    else:  # a bundle on its own: look inside the image archive's config for the tools' paths
        check("workspace image: code-server and File Browser preinstalled (needs development/: skipped)", True, "skipped")

    rc, out, err = run([sys.executable, "-m", "unittest", "discover", "-s", here, "-p", "platform_unit_test.py", "-q"])
    check("platform service unit tests", rc == 0, (err or out).strip().splitlines()[-1] if (err or out).strip() else "")
    for sh in ("install.sh", "make", "mirror-images.sh", "minikube-support", "harbor-support", "argocd-support",
               "argocd/update-application.sh", "../development/tools/build-decorator-update.sh"):
        rc, _, err = run(["bash", "-n", os.path.join(B, sh)])
        check("shell syntax: %s" % sh, rc == 0, err.strip())
    # The decorator update's helper, on this release's Application set back to an older release (a scratch copy, with
    # stand-ins for the charts and the image: empty, but the layer's values): both sources move to this version, the
    # dataflow template goes off, and the file still parses; with --dataflow keep, it stays on, on the layer chart's
    # image in the Application's registry.
    import shutil, tarfile, tempfile
    with tempfile.TemporaryDirectory() as tmp:
        version = re.search(r"^version:\s*(\S+)", open(os.path.join(chart, "Chart.yaml")).read(), re.M).group(1)
        os.makedirs(os.path.join(tmp, "charts")); os.makedirs(os.path.join(tmp, "images"))
        open(os.path.join(tmp, "charts", "coder-airgapped-base-%s.tgz" % version), "w").close()
        with tarfile.open(os.path.join(tmp, "charts", "coder-airgapped-layer-%s.tgz" % version), "w:gz") as tgz:
            tgz.add(os.path.join(chart, "values.yaml"), "coder-airgapped-layer/values.yaml")
        dataflow_image = re.search(r'^  dataflowImage:.*"([^"\n]+)"', open(os.path.join(chart, "values.yaml")).read(),
                                   re.M).group(1)
        open(os.path.join(tmp, "images", "ghcr.io_user187x_coder-platform_0.0.0-test-airgap.tar.gz"), "w").close()
        shutil.copy(os.path.join(B, "argocd", "update-application.sh"), tmp)
        app = os.path.join(tmp, "app.yaml")
        open(app, "w").write(open(os.path.join(B, "argocd", "coder-decorated-application.yaml")).read()
                             .replace("targetRevision: %s" % version, "targetRevision: 0.0.1"))
        rc, out, err = run(["bash", os.path.join(tmp, "update-application.sh"), "--app", app, "--dry-run"])
        ok = rc == 0 and all(s in out for s in (
            "coder-airgapped-base 0.0.1 -> %s" % version, "coder-airgapped-layer 0.0.1 -> %s" % version,
            "dataflowTemplate.enabled: false"))
        check("argocd/update-application.sh: updates an older Application (dry run)", ok,
              (err or out).strip()[-300:] if not ok else "")
        rc, out, err = run(["bash", os.path.join(tmp, "update-application.sh"), "--app", app, "--dry-run",
                            "--dataflow", "keep"])
        ok = rc == 0 and "dataflowTemplate.enabled: true" in out and \
            ("/" + dataflow_image.split("/", 1)[1]) in out and "dataflowImage: " in out
        check("argocd/update-application.sh: --dataflow keep runs the layer chart's image (%s)" % dataflow_image, ok,
              (err or out).strip()[-300:] if not ok else "")
    # the layer bundle's helper (Python): compiles, and answers --help
    rc, out, err = run([sys.executable, os.path.join(B, "layer-support"), "--help"])
    check("layer-support: runs (--help)", rc == 0 and "layer-support build" in out, (err or "").strip()[-200:])
    passed = sum(x["ok"] for x in RESULTS)
    print("\n%d/%d static checks passed" % (passed, len(RESULTS)))
    sys.exit(0 if passed == len(RESULTS) else 1)


if __name__ == "__main__":
    main()

"""Static checks of the deliverable (no cluster needed): the chart renders for every way it is installed, every image
it can run is pinned in images.txt and archived in the bundle, nothing floats, and nothing at run time needs the
internet.

  python3 chart_checks.py [--bundle helm-chart] [--jsonl out.jsonl]

Each check is a JSON line ({suite: "static", name, ok, detail}) as in live.py."""
import argparse, glob, hashlib, json, os, re, subprocess, sys

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


def ha_checks(B, ha_render, default_render):
    """High availability (values-ha.example.yaml): 3 Coder replicas with a PodDisruptionBudget, 3 database instances,
    2 banners; one Coder replica gets no budget (it would only block node drains)."""
    import yaml
    docs = {(d["kind"], d["metadata"]["name"]): d for d in yaml.safe_load_all(ha_render) if d}
    got = {"coder": docs.get(("Deployment", "coder"), {}).get("spec", {}).get("replicas"),
           "coder-db": docs.get(("Cluster", "coder-db"), {}).get("spec", {}).get("instances"),
           "coder-banner": docs.get(("Deployment", "coder-banner"), {}).get("spec", {}).get("replicas"),
           "budget": docs.get(("PodDisruptionBudget", "coder"), {}).get("spec")}
    check("high availability: 3 Coder replicas (one down at a time), 3 database instances, 2 banners",
          got == {"coder": 3, "coder-db": 3, "coder-banner": 2, "budget": {
              "maxUnavailable": 1, "selector": {"matchLabels": {"app.kubernetes.io/name": "coder",
                                                                "app.kubernetes.io/instance": "coder"}}}}, got)
    check("one Coder replica: no PodDisruptionBudget", ("PodDisruptionBudget", "coder") not in objects(default_render))


def banner_checks(chart):
    """The banner chart's four copies of the banner's contract agree with its service (files/server.py): the chart's
    defaults (values.yaml, the ConfigMap's banner.json, the choices _helpers.tpl checks them against), the admin page's
    lists and the loader's effects. They drifted once: the defaults could not set a picture, Pew-Pew was unknown."""
    import ast, yaml
    d = os.path.join(chart, "charts/coder-banner")
    tree = ast.parse(open(os.path.join(d, "files/server.py")).read())
    server = {t.id: ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
              for t in n.targets if isinstance(t, ast.Name) and t.id in (
                  "LEVELS", "EFFECTS", "SPEEDS", "FONTS", "ALIGNS", "PICTURE_FITS", "OPEN_EFFECTS", "CLOSE_EFFECTS")}
    fields = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                  and any(isinstance(t, ast.Name) and t.id == "FIELDS" for t in n.targets))
    helpers = open(os.path.join(d, "templates/_helpers.tpl")).read()
    choices = yaml.safe_load(re.search(r'define "coder-banner.choices" -\}\}\n(.*?)\{\{- end', helpers, re.S).group(1))
    pairs = {"level": "LEVELS", "effect": "EFFECTS", "speed": "SPEEDS", "font": "FONTS", "align": "ALIGNS",
             "pictureFit": "PICTURE_FITS", "openEffect": "OPEN_EFFECTS", "closeEffect": "CLOSE_EFFECTS"}
    differ = {k: (choices.get(k), list(server[v])) for k, v in pairs.items() if choices.get(k) != list(server[v])}
    check("banner: the chart's choices are the service's (templates/_helpers.tpl vs files/server.py)", not differ, differ)
    values = yaml.safe_load(open(os.path.join(d, "values.yaml")))["banner"]
    check("banner: every field the service takes can be set as a chart default (values.yaml banner.*)",
          set(fields) <= set(values), sorted(set(fields) - set(values)))
    rc, out, err = run(["helm", "template", "t", d])
    m = re.search(r"banner\.json: \|\n((?:    .*\n)+)", out)
    keys = set(json.loads(m.group(1))) if rc == 0 and m else set()
    check("banner: the chart's default banner.json has every field the service has, and only those",
          keys == set(fields) | {"id"}, (err.strip()[-200:] if rc else sorted(keys ^ (set(fields) | {"id"}))))
    page = open(os.path.join(d, "files/admin.html")).read()
    lists = {k: re.findall(r'<option value="([\w-]+)"', (re.search(r'<select id="%s"[^>]*>(.*?)</select>' % k, page, re.S)
                                                          or [None, ""])[1]) for k in ("effect", "speed", "pictureFit")}
    want = {"effect": list(server["EFFECTS"]), "speed": list(server["SPEEDS"]), "pictureFit": sorted(server["PICTURE_FITS"])}
    got = dict(lists, pictureFit=sorted(lists["pictureFit"]))
    check("banner: the admin page offers every effect, speed and picture fit (files/admin.html)", got == want,
          {k: (got[k], want[k]) for k in want if got[k] != want[k]})
    js = open(os.path.join(d, "files/banner.js")).read()
    drawn = set(re.findall(r"^    (\w+): function \(a, sp", js, re.M)) | set(
        re.findall(r"^    (\w+): \{", re.search(r"var CSS_EFFECTS = \{\n(.*?)\n  \};", js, re.S).group(1), re.M))
    check("banner: the loader draws every effect (files/banner.js: Anime.js and fx.css)",
          drawn == set(server["EFFECTS"]) - {"none"}, sorted(drawn ^ (set(server["EFFECTS"]) - {"none"})))


def safe_checks(chart, default_render):
    """The safe (global.userStorage): on, the template push mounts it, the platform service serves it and the chart
    runs its MinIO and CSI driver; off, nothing of it anywhere; an S3 service of one's own needs its endpoint and keys."""
    import yaml
    docs = {(d["kind"], d["metadata"]["name"]): d for d in yaml.safe_load_all(default_render) if d}
    ui_env = {e["name"]: e.get("value") for c in docs[("Deployment", "coder-ui-updates")]["spec"]["template"]["spec"]["containers"]
              for e in c.get("env", [])}
    push = "\n".join(c["args"][0] for k, d in docs.items() if k[0] == "Job" and "push-template" in k[1]
                     for c in d["spec"]["template"]["spec"]["containers"])
    want = {("Deployment", "coder-user-storage-minio"), ("Service", "coder-user-storage-minio"),
            ("DaemonSet", "coder-user-storage-csi"), ("CSIDriver", "ru.yandex.s3.csi"), ("Secret", "coder-user-storage-s3")}
    check("safe: on by default - MinIO, the CSI driver, the platform service's safe, the templates mount it",
          want <= set(docs) and ui_env.get("SAFE_ENABLED") == "true" and ui_env.get("SAFE_NODE_SERVICE")
          and push.count("--variable user_storage=true") >= 1,
          sorted(want - set(docs)) or {k: ui_env.get(k) for k in ("SAFE_ENABLED", "SAFE_NODE_SERVICE")})
    def parsed(out):
        found = {(d["kind"], d["metadata"]["name"]): d for d in yaml.safe_load_all(out) if d}
        env = {e["name"]: e for c in found[("Deployment", "coder-ui-updates")]["spec"]["template"]["spec"]["containers"]
               for e in c.get("env", [])}
        args = "\n".join(c["args"][0] for k, d in found.items() if k[0] == "Job" and "push-template" in k[1]
                         for c in d["spec"]["template"]["spec"]["containers"])
        return found, env, args

    rc, off, err = run(["helm", "template", "coder", chart, "--namespace", "coder", "--set", "global.userStorage.enabled=false"])
    found, env, args = parsed(off) if rc == 0 else ({}, {}, "")
    check("safe: off - no MinIO, no CSI driver, no mount in the templates",
          rc == 0 and not want & set(found) and "--variable user_storage" not in args and "SAFE_ENABLED" not in env,
          err.strip()[-200:] or sorted(want & set(found)))
    rc, _, err = run(["helm", "template", "coder", chart, "--set", "userStorage.minio.enabled=false"])
    check("safe: an S3 service of one's own needs s3.endpoint and s3.existingSecret", rc != 0 and "s3.endpoint" in err,
          err.strip()[-160:])
    rc, ext, err = run(["helm", "template", "coder", chart, "--namespace", "coder", "--set", "userStorage.minio.enabled=false",
                        "--set", "userStorage.s3.endpoint=https://s3.us-east-1.amazonaws.com",
                        "--set", "userStorage.s3.existingSecret=my-s3"])
    found, env, args = parsed(ext) if rc == 0 else ({}, {}, "")
    keys = {k: env.get(k, {}).get("valueFrom", {}).get("secretKeyRef") for k in ("S3_ACCESS_KEY", "S3_SECRET_KEY")}
    check("safe: with an S3 service of one's own, no MinIO, its keys from its Secret",
          rc == 0 and ("Deployment", "coder-user-storage-minio") not in found
          and ("DaemonSet", "coder-user-storage-csi") in found
          and env.get("S3_ENDPOINT", {}).get("value") == "https://s3.us-east-1.amazonaws.com"
          and keys == {"S3_ACCESS_KEY": {"name": "my-s3", "key": "accessKeyID"},
                       "S3_SECRET_KEY": {"name": "my-s3", "key": "secretAccessKey"}},
          err.strip()[-200:] or keys)


def ai_checks(B, chart, default_render):
    """The AI templates (aiTemplates): on, two push Jobs on the AI image (app_set lmstudio and opencode), the Images
    view lists them, the template knows both sets and mounts the model library; off, no Job; the image is pinned."""
    import yaml
    docs = {(d["kind"], d["metadata"]["name"]): d for d in yaml.safe_load_all(default_render) if d}
    jobs = {k[1]: "\n".join(c["args"][0] for c in d["spec"]["template"]["spec"]["containers"])
            for k, d in docs.items() if k[0] == "Job" and "push-template" in k[1]}
    ai_image = re.search(r'^  aiImage:.*"([^"\n]+)"', open(os.path.join(chart, "values.yaml")).read(), re.M).group(1)
    lms = next((a for n, a in jobs.items() if n.endswith("-lmstudio")), "")
    oc = next((a for n, a in jobs.items() if n.endswith("-opencode")), "")
    check("AI templates: ubuntu-lmstudio and ubuntu-opencode pushed on %s" % ai_image,
          'app_set="lmstudio"' in lms and 'app_set="opencode"' in oc
          and all('workspace_image="%s"' % ai_image in a for a in (lms, oc)), sorted(jobs))
    ui_env = {e["name"]: e.get("value") for c in docs[("Deployment", "coder-ui-updates")]["spec"]["template"]["spec"]["containers"]
              for e in c.get("env", [])}
    names = [t["name"] for t in json.loads(ui_env.get("BUILDER_TEMPLATES") or "[]")]
    check("AI templates: in the Images view's In use list", {"ubuntu-lmstudio", "ubuntu-opencode"} <= set(names), names)
    rc, off, err = run(["helm", "template", "coder", chart, "--namespace", "coder",
                        "--set", "coder-workspace.aiTemplates.enabled=false"])
    check("AI templates: off - no push Job", rc == 0 and "push-template-lmstudio" not in off
          and "push-template-opencode" not in off, err.strip()[-200:])
    tf = open(os.path.join(chart, "charts", "coder-workspace", "files", "main.tf")).read()
    startup = open(os.path.join(chart, "charts", "coder-workspace", "files", "scripts", "startup.sh")).read()
    check("AI templates: main.tf knows lmstudio and opencode, mounts the model library, OpenCode's app and settings",
          '"lmstudio", "opencode"' in tf and 'claim_name = "coder-models"' in tf and 'slug         = "opencode"' in tf
          and "OPENCODE_CONFIG" in tf and "coder-lmstudio-start" in startup, "")
    icons = os.path.join(chart, "charts", "coder-workspace", "files", "icons")
    check("AI templates: their icons (lmstudio.svg, opencode.svg)",
          all(os.path.isfile(os.path.join(icons, f)) for f in ("lmstudio.svg", "opencode.svg")), "")
    build = os.path.join(B, "..", "development", "tools", "build-ai-image.sh")
    if os.path.isfile(build):
        b = open(build).read()
        check("AI image: LM Studio and OpenCode downloads pinned by SHA-256",
              re.search(r"^LMSTUDIO_SHA256=[0-9a-f]{64}", b, re.M) and re.search(r"^OPENCODE_SHA256=[0-9a-f]{64}", b, re.M),
              "")
    images_txt = open(os.path.join(B, "images.txt")).read()
    check("AI image: listed in images.txt (%s)" % ai_image, re.search(r"^%s\s+sha256:[0-9a-f]{64}" % re.escape(ai_image),
                                                                   images_txt, re.M) is not None, "")


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

    images = {}  # ref -> (folder, digest)
    folder, modes = None, {}
    for line in open(os.path.join(B, "images.txt")):
        f = line.split()
        if not f or f[0].startswith("#"):
            continue
        if f[0].startswith("["):
            folder = f[1].rstrip("/") if len(f) > 1 else ""
            modes[f[0][1:-1]] = (folder, f[2] if len(f) > 2 else "")
            continue
        images[f[0]] = (folder, f[1] if len(f) > 1 else "")
    check("images.txt: every section names its folder under images/ and push or reference",
          all(d.startswith("images/") and "/" not in d[len("images/"):] and m in ("push", "reference")
              for d, m in modes.values()), modes)
    check("images.txt: every image pinned by digest", all(d.startswith("sha256:") for _, d in images.values()),
          [r for r, (_, d) in images.items() if not d.startswith("sha256:")])
    floating = [r for r in images if r.endswith(":latest") or ":" not in r.rsplit("/", 1)[-1]]
    check("images.txt: no floating tags (:latest or none)", not floating, floating)

    def archive(ref, folder):
        return os.path.join(B, folder, re.sub(r"[/:@]", "_", ref) + ".tar.gz")
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
        "high availability (values-ha.example.yaml)": ["-f", os.path.join(B, "values-eks-airgap.example.yaml"),
                                                       "-f", os.path.join(B, "values-ha.example.yaml")],
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
    ha_checks(B, renders["high availability (values-ha.example.yaml)"], r)
    safe_checks(chart, r)
    ai_checks(B, chart, r)
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
    left = sorted(set(re.findall(r'\{\{ *include "platform\.[\w]+"[^}]*\}\}', r)))
    check("every values string is rendered (no {{ include ... }} left in the manifests: ca.values lists its section)",
          not left, left[:3])
    check("telemetry and update check off", "CODER_TELEMETRY_ENABLE" not in r or re.search(r"CODER_TELEMETRY_ENABLE\s*\n\s*value: \"?false", r),
          "")
    tf_dir = os.path.join(chart, "charts/coder-workspace/files")
    scripts = sorted(glob.glob(os.path.join(tf_dir, "scripts", "*.sh")))
    tf = {n: open(os.path.join(tf_dir, n)).read() for n in ("main.tf", "agent-audit.tf")}
    refs = sorted({m for m in re.findall(r'file\("\$\{path\.module\}/scripts/([^"]+)"', tf["main.tf"])})
    # the base template (the UBUNTU prototype): one main.tf, its platform sections marked for the template decorator
    check("template: consolidated - no certificates.tf, no lockdown-start.sh (lockdown.sh's start mode)",
          not os.path.exists(os.path.join(tf_dir, "certificates.tf"))
          and not os.path.exists(os.path.join(tf_dir, "scripts", "lockdown-start.sh"))
          and "set -- start" in tf["main.tf"] and 'if [ "$1" = start ]' in open(os.path.join(tf_dir, "scripts",
                                                                                          "lockdown.sh")).read(), "")
    marks, opened, bad_marks = [], None, []
    for i, line in enumerate(tf["main.tf"].split("\n"), 1):
        t = line.strip()
        if t.startswith("# platform:begin "):
            if opened:
                bad_marks.append(i)
            opened = t.split()[-1]
            marks.append(opened)
        elif t.startswith("# platform:end "):
            if opened != t.split()[-1]:
                bad_marks.append(i)
            opened = None
    check("template: the platform's sections (certificates, Lock Down, Cloud Drive, metrics, code-server HA) marked for the decorator",
          not bad_marks and not opened and set(marks) == {"certificates", "lockdown", "cloud-drive", "metrics", "code-server-ha"},
          "features %s, unbalanced at lines %s" % (sorted(set(marks)), bad_marks))
    check("template: the Desktop, File Browser and Terminal apps are the template's choice (variables)",
          all(re.search(r'variable "%s" \{\s*type\s*=\s*bool' % v, tf["main.tf"]) for v in ("desktop", "file_browser",
                                                                                          "terminal"))
          and "count        = var.desktop ? 1 : 0" in tf["main.tf"] and "web_terminal = var.terminal" in tf["main.tf"],
          "")
    left = subprocess.run(["git", "-C", os.path.dirname(os.path.dirname(chart)), "grep", "-il", "marimo", "--",
                           ".", ":!development/reference", ":!development/tools/airgap-e2e/*.out", ":!CHANGELOG.md",
                           ":!docs/READINESS.md", ":!development/tools/verify/chart_checks.py"],
                          capture_output=True, text=True).stdout.split()
    check("marimo is gone (code, templates, images, docs; history and reference exports aside)", not left, left[:5])
    # code-server HA (templates/code-server-ha.yaml, files/ui-updates/csha.py)
    ui_img = re.search(r"image: (ghcr\.io/user187x/coder-ui-updates:code-[0-9a-f]+)", r)
    op = re.search(r"name: coder-code-server-replicator\n(?:.*\n){0,40}?\s+command: \[python3, /app/csha\.py, operator\]", r)
    check("code-server HA: the code-server-replicator runs the platform service's code, as Coder's database's owner",
          op and "CSHA_DB" in r and re.search(r"name: CSHA_DB\s*\n\s*valueFrom: \{secretKeyRef: \{name: coder-db-app, key: uri\}\}", r),
          "")
    check("code-server HA: the relay role is CloudNativePG's (managed role from its basic-auth Secret), and the template "
          "push gives the relay the platform service's image",
          re.search(r"managed:\s*\n\s*roles:\s*\n\s*- name: code_server_relay", r)
          and re.search(r"name: coder-code-server-ha-relay\n(?:.*\n){0,12}?type: kubernetes\.io/basic-auth", r)
          and ui_img and ('--variable code_server_ha_image="%s"' % ui_img.group(1)) in r, "")
    check("code-server HA: the template's section (a relay where code-server would be; the startup script starts none)",
          "# platform:begin code-server-ha" in tf["main.tf"] and '"/app/csha.py", "front"' in tf["main.tf"]
          and "CODER_CODE_SERVER_HA" in tf["main.tf"]
          and "CODER_CODE_SERVER_HA" in open(os.path.join(tf_dir, "scripts", "startup.sh")).read(), "")
    shipped = re.findall(r"^  ([\w.-]+\.(?:sh|py)): \|$", r, re.M)
    check("template: every script the .tf files read is in files/scripts/ and in the push's ConfigMap",
          refs and all(os.path.isfile(os.path.join(tf_dir, "scripts", s)) and s in shipped for s in refs),
          "read: %s; shipped: %s" % (refs, shipped))
    # templatefile() reads ${...} in a script as its own: each must be a variable main.tf passes to that script (a
    # shell expansion is written $${...}), else the template fails to plan.
    bad = []
    for name, body in tf.items():
        for m in re.finditer(r'templatefile\("\$\{path\.module\}/scripts/([\w.-]+)",\s*\{(.*?)\n\s*\}\)', body, re.S):
            keys = set(re.findall(r"^\s*(\w+)\s*=", m.group(2), re.M))
            text = open(os.path.join(tf_dir, "scripts", m.group(1))).read()
            for ref in re.findall(r"(?<!\$)\$\{([^}]*)\}", text):
                if ref.strip() not in keys:
                    bad.append("%s: ${%s}" % (m.group(1), ref))
    check("template scripts: every ${...} is a variable main.tf passes (shell's written $${...})", not bad, bad[:5])
    check("template: no script left inline in the .tf files (heredocs)", not any("<<" in t for t in tf.values()),
          [n for n, t in tf.items() if "<<" in t])
    if subprocess.run(["sh", "-c", "command -v shfmt"], capture_output=True).returncode == 0:
        rc, out, err = run(["shfmt", "-i", "1", "-d"] + scripts)
        check("template scripts: formatted (shfmt -i 1)", rc == 0, (out + err).strip()[:300] or "%d scripts" % len(scripts))
    else:
        check("template scripts: formatted (shfmt -i 1; shfmt not installed: skipped)", True, "skipped")
    # The platform service's code is in its image (development/tools/build-ui-image.sh), tagged with the code's hash:
    # values.yaml must name the build of the code as it is now (code changed without a new build would deploy the old
    # code), and the chart must not carry the code (Helm keeps each release in a Secret of at most 1 MiB).
    ui_dir = os.path.join(chart, "files/ui-updates")
    names = sorted(n for n in os.listdir(ui_dir) if os.path.isfile(os.path.join(ui_dir, n)) and not n.endswith(".pyc"))
    lines = "".join("%s  %s\n" % (hashlib.sha256(open(os.path.join(ui_dir, n), "rb").read()).hexdigest(), n)
                    for n in names)
    want = "coder-ui-updates:code-" + hashlib.sha256(lines.encode()).hexdigest()[:12]
    values_txt = open(os.path.join(chart, "values.yaml")).read()
    pinned = re.search(r"ghcr\.io/user187x/(coder-ui-updates:code-[0-9a-f]+)", values_txt)
    check("platform service: values.yaml pins the image of the code as it is (development/tools/build-ui-image.sh)",
          pinned and pinned.group(1) == want, "pinned %s, code is %s" % (pinned and pinned.group(1), want))
    images_txt = open(os.path.join(chart, "..", "images.txt")).read()
    check("platform service: images.txt lists that image", bool(pinned) and pinned.group(1) in images_txt,
          pinned and pinned.group(1))
    ignore = open(os.path.join(chart, ".helmignore")).read()
    check("platform service: its code stays out of the chart (.helmignore files/ui-updates/*)",
          re.search(r"^files/ui-updates/\*$", ignore, re.M) is not None, "")
    # The Jobs it starts run its own image (the code in /app): no ConfigMap of the code exists any more.
    mounts = [n for n in names if n.endswith(".py") and re.search(r'"configMap": \{"name": "coder-ui-updates"',
                                                                open(os.path.join(ui_dir, n)).read())]
    check("platform service: its Jobs take the code from its image, not a ConfigMap", not mounts, mounts or "")
    banner_checks(chart)
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
        ai_image = re.search(r'^  aiImage:.*"([^"\n]+)"', open(os.path.join(chart, "values.yaml")).read(),
                             re.M).group(1)
        rc, out, err = run(["bash", os.path.join(tmp, "update-application.sh"), "--app", app, "--dry-run"])
        ok = rc == 0 and "aiTemplates.enabled: false" in out
        rc2, out2, err2 = run(["bash", os.path.join(tmp, "update-application.sh"), "--app", app, "--dry-run",
                               "--ai", "keep"])
        ok = ok and rc2 == 0 and "aiTemplates.enabled: true" in out2 and \
            ("/" + ai_image.split("/", 1)[1]) in out2 and "aiImage: " in out2
        check("argocd/update-application.sh: the AI templates off by default, --ai keep runs %s" % ai_image, ok,
              (err2 or out2 or err or out).strip()[-300:] if not ok else "")
    # the layer bundle's helper (Python): compiles, and answers --help
    rc, out, err = run([sys.executable, os.path.join(B, "layer-support"), "--help"])
    check("layer-support: runs (--help)", rc == 0 and "layer-support build" in out, (err or "").strip()[-200:])
    passed = sum(x["ok"] for x in RESULTS)
    print("\n%d/%d static checks passed" % (passed, len(RESULTS)))
    sys.exit(0 if passed == len(RESULTS) else 1)


if __name__ == "__main__":
    main()

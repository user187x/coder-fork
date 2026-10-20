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


def icon_checks(chart, default_render):
    """The workspace template's own icons (charts/coder-workspace/files/icons/, served at /__icons/<file> by the
    platform service from their ConfigMap): any image - each comes through byte for byte (binaryData), its type and
    name ones the service serves (ui-service.py's TEMPLATE_ICON_TYPES, TEMPLATE_ICON_RE: else it ships and 404s),
    every /__icons/ reference a file there, the ConfigMap well under Kubernetes' 1 MiB (it is mounted optional: one
    that fails to apply leaves every icon a 404)."""
    import ast, base64, yaml
    icons = os.path.join(chart, "charts", "coder-workspace", "files", "icons")
    files = sorted(os.listdir(icons))
    cms = [d for d in yaml.safe_load_all(default_render) if d and d.get("kind") == "ConfigMap"
           and d["metadata"]["name"].endswith("-icons")]
    data = (cms[0].get("binaryData") or {}) if cms else {}
    differ = [f for f in files if base64.b64decode(data.get(f, "")) != open(os.path.join(icons, f), "rb").read()]
    kinds = {os.path.splitext(f)[1].lower() for f in files}
    check("template icons: every file in files/icons/ comes through byte for byte (binaryData; SVG and PNG among them)",
          len(cms) == 1 and not differ and not (cms[0].get("data") or {}) and {".svg", ".png"} <= kinds,
          differ or "%d icons" % len(files))
    svc = ast.parse(open(os.path.join(chart, "files", "ui-updates", "ui-service.py")).read())
    consts = {t.id: n.value for n in svc.body if isinstance(n, ast.Assign) for t in n.targets if isinstance(t, ast.Name)}
    types = set(ast.literal_eval(consts["TEMPLATE_ICON_TYPES"]))
    name_re = re.compile(ast.literal_eval(consts["TEMPLATE_ICON_RE"].args[0]))
    unserved = [f for f in files if os.path.splitext(f)[1].lower() not in types or not name_re.match(f)]
    check("template icons: each one's type and name the platform service serves (else a 404)", not unserved, unserved)
    texts = [os.path.join(chart, "charts", "coder-workspace", "files", "main.tf"),
             os.path.join(chart, "charts", "coder-workspace", "values.yaml"),
             os.path.join(chart, "charts", "coder-workspace", "templates", "job.yaml"), os.path.join(chart, "values.yaml")]
    refs = sorted({m for t in texts for m in re.findall(r"/__icons/([A-Za-z0-9._-]+)", open(t).read())})
    missing = [r for r in refs if r not in files]
    check("template icons: every /__icons/ reference is a file in files/icons/", not missing,
          missing or "%d referenced" % len(refs))
    size = sum(len(v) for v in data.values())
    check("template icons: their ConfigMap well under 1 MiB (base64)", 0 < size < 512 * 1024, "%d KiB" % (size // 1024))
    tf = open(texts[0]).read()
    check("template: File Browser's icon is FileBrowser Quantum's logo (files/icons/filebrowser-quantum.png)",
          re.search(r'slug\s+= "filebrowser"\n\s+display_name = "File Browser"\n\s+icon\s+= "/__icons/filebrowser-quantum\.png"', tf)
          is not None, "")


def ai_checks(B, chart, default_render):
    """The AI template (aiTemplates): on, one push Job on the AI image (app_set opencode), the Images view lists it, the
    template knows the set and mounts the model library; off, no Job; the image is pinned. The templates removed
    (Unsloth's, LM Studio's, Obsidian's) are gone for good."""
    import yaml
    docs = {(d["kind"], d["metadata"]["name"]): d for d in yaml.safe_load_all(default_render) if d}
    jobs = {k[1]: "\n".join(c["args"][0] for c in d["spec"]["template"]["spec"]["containers"])
            for k, d in docs.items() if k[0] == "Job" and "push-template" in k[1]}
    vals = open(os.path.join(chart, "values.yaml")).read()
    ai_image = re.search(r'^  aiImage:.*"([^"\n]+)"', vals, re.M).group(1)
    oc = next((a for n, a in jobs.items() if n.endswith("-opencode")), "")
    check("AI template: ubuntu-opencode pushed on %s" % ai_image,
          'app_set="opencode"' in oc and 'workspace_image="%s"' % ai_image in oc, sorted(jobs))
    check("AI template: no Unsloth or Obsidian template pushed",
          not any(n.endswith(("-unsloth", "-lmstudio", "-obsidian")) for n in jobs), sorted(jobs))
    ui_env = {e["name"]: e.get("value") for c in docs[("Deployment", "coder-ui-updates")]["spec"]["template"]["spec"]["containers"]
              for e in c.get("env", [])}
    names = [t["name"] for t in json.loads(ui_env.get("BUILDER_TEMPLATES") or "[]")]
    check("AI template: in the Images view's In use list, the removed ones not",
          "ubuntu-opencode" in names and not {"ubuntu-unsloth", "ubuntu-obsidian"} & set(names), names)
    rc, off, err = run(["helm", "template", "coder", chart, "--namespace", "coder",
                        "--set", "coder-workspace.aiTemplates.enabled=false"])
    check("AI template: off - no push Job", rc == 0 and "push-template-opencode" not in off, err.strip()[-200:])
    tf = open(os.path.join(chart, "charts", "coder-workspace", "files", "main.tf")).read()
    startup = open(os.path.join(chart, "charts", "coder-workspace", "files", "scripts", "startup.sh")).read()
    check("AI template: main.tf knows opencode, mounts the model library, OpenCode's app on Unsloth",
          '"developer", "dataflow", "opencode"]' in tf and 'claim_name = "coder-models"' in tf
          and 'slug         = "opencode"' in tf and "exec coder-opencode" in tf and "coder-unsloth-start" in startup
          and "lmstudio" not in tf and "LM Studio" not in tf + startup, "")
    check("main.tf: no Obsidian app set, code-server everywhere but dataflow",
          "obsidian" not in (tf + startup).lower() and "local.dataflow ? 0 : 1" in tf, "")
    # "(cd X && app >log 2>&1 &)" backgrounds a bash that keeps the script's output open: the agent then reports
    # start_error ("script exited without closing output pipes"); the whole subshell is redirected instead
    tpl_scripts = os.path.join(chart, "charts", "coder-workspace", "files", "scripts")
    held = ["%s: %s" % (n, ln.strip()) for n in sorted(os.listdir(tpl_scripts)) if n.endswith(".sh")
            for ln in open(os.path.join(tpl_scripts, n)).read().splitlines()
            if re.search(r"\(.*&&.*&\)\s*$", ln) and not ln.lstrip().startswith("#")]
    check("template scripts: no background subshell keeps the script's output open", not held, held[:3])
    # A metric's output is its value: reading a /proc entry of a process that ended meanwhile fails the redirection
    # before a trailing 2>/dev/null applies, and "bash: line N: /proc/...: No such file" showed in the dashboard
    metadata = open(os.path.join(chart, "charts", "coder-workspace", "files", "scripts", "metadata.sh")).read()
    leaky = [ln.strip() for ln in metadata.splitlines()
             if re.search(r'<\s*"?/proc/\$', ln) and not re.search(r"\{[^}]*<[^}]*;\s*\}\s*2>/dev/null", ln)]
    check("metadata.sh: a process's /proc entry is read with its errors silenced (in braces)", not leaky, leaky[:3])
    # The Linux user and the hostname are the owner's, not "coder": the account renamed by editing its files (usermod
    # refuses while its processes run), after a sudo rule for the new name that visudo accepted
    check("startup.sh: the Linux user renamed after the owner (sudo rule checked first, files edited, USER set)",
          "visudo -cqf" in startup and "/etc/sudoers.d/coder-user" in startup and "/etc/passwd /etc/shadow" in startup
          and "/etc/group /etc/gshadow" in startup and 'export USER="$(id -un)"' in startup
          and startup.index("visudo -cqf") < startup.index("/etc/passwd /etc/shadow"), "")
    # The agent keeps the name it started with (coder) and finds every terminal's and SSH session's shell by it in
    # /etc/passwd: renamed away, they exited at once (the Terminal panel would not open)
    check("startup.sh: the renamed account keeps coder as a second name (the agent's terminals and SSH find their shell)",
          "! getent passwd coder" in startup and "/etc/passwd /etc/shadow /etc/group /etc/gshadow" in startup
          and "s/^$(id -un):/coder:/" in startup
          and startup.index("s/^$(id -un):/coder:/") > startup.index("/etc/passwd /etc/shadow\n"), "")
    check("main.tf: the pod's hostname is the owner's and the workspace's names (a DNS label)",
          "hostname = local.hostname" in tf and re.search(r'hostname = trim\(substr\(replace\(lower\(', tf) is not None, "")
    # A launcher of a terminal emulator with Terminal=true (XFCE's launcher editor writes it) opened Terminator inside
    # xfce4-terminal, showing Terminator's start-up warnings: the home's launchers of terminals are put back at start
    check("startup.sh: the home's terminal launchers never run in a terminal (Terminal=true undone at start)",
          "grep -q '^Categories=.*TerminalEmulator'" in startup and "s/^Terminal=true$/Terminal=false/" in startup
          and startup.index("Terminal=false/") < startup.index("Xtigervnc :1 -auth"), "")
    check("startup.sh: only copies reach the computer's clipboard (Xtigervnc -SendPrimary=0)",
          "-SendPrimary=0" in startup, "")
    # The Model Library's scanner (garak) is a plug-in: imported in the dashboard, never shipped with the platform
    scanner = yaml.safe_load(vals).get("modelScanner") or {}
    check("model scanner: a plug-in - no image pinned or bundled (Model library > Import scanner)",
          scanner.get("image") == "" and scanner.get("enabled") is True
          and "coder-garak" not in open(os.path.join(B, "images.txt")).read(), scanner.get("image"))
    # Every template has the GPU parameter (the workspace settings' GPU switch), disabled by the template where its
    # workspaces do not run on GPUs: only the AI template's value reaches the pod (local.gpus)
    gpu_param = re.search(r'data "coder_parameter" "gpus" \{(.*?)\n\}', tf, re.S)
    check("main.tf: the GPU parameter on every template, disabled (styling) but on the AI template",
          gpu_param is not None and "count" not in gpu_param.group(1)
          and "styling      = jsonencode({ disabled = !local.ai })" in gpu_param.group(1)
          and "gpus           = local.ai ? tonumber(data.coder_parameter.gpus.value) : 0" in tf, "")
    # The Images view's "Coder fork" tree reads what each image of ours holds from image-contents.json: it must be the
    # images images.txt pins (rebuilt with development/tools/image-contents.py after pinning a new one)
    contents = subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                                            "image-contents.py"), "--check"],
                              capture_output=True, text=True)
    check("image-contents.json: what our images hold, for the images images.txt pins", contents.returncode == 0,
          (contents.stderr or contents.stdout).strip()[-300:])
    # Coder's form evaluates parameters in passes (the choice, then the version plan's data sources): try() settles
    # before they are known - a conditional field would never show, the dropdown never list the images kept
    check("main.tf: no try() on what Coder's form learns late (a conditional count, the images kept)",
          not re.search(r"count\s*=\s*try\(data\.coder_parameter|try\((jsondecode\()?data\.kubernetes_config_map_v1"
                        r"\.workspace_images|try\(local\.images_kept", tf), "")
    icons = os.path.join(chart, "charts", "coder-workspace", "files", "icons")
    check("AI template: its icons (unsloth.svg for the context length, opencode.svg); none for the removed ones",
          all(os.path.isfile(os.path.join(icons, f)) for f in ("unsloth.svg", "opencode.svg"))
          and not any(os.path.exists(os.path.join(icons, f)) for f in ("obsidian.svg", "lmstudio.svg")), "")
    build = os.path.join(B, "..", "development", "tools", "build-ai-image.sh")
    if os.path.isfile(build):
        b = open(build).read()
        check("AI image: Unsloth's AppImage and OpenCode's download pinned by SHA-256",
              re.search(r"^UNSLOTH_APPIMAGE_SHA256=[0-9a-f]{64}", b, re.M)
              and re.search(r"^OPENCODE_SHA256=[0-9a-f]{64}", b, re.M), "")
    images_txt = open(os.path.join(B, "images.txt")).read()
    check("workspace image listed in images.txt (%s)" % ai_image,
          re.search(r"^%s\s+sha256:(?!0{64})[0-9a-f]{64}" % re.escape(ai_image), images_txt, re.M) is not None, "")


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


def pit_checks(B, chart, default_render):
    """Images > Fix image > Pit stop: Copa (Copacetic) patches an image in a Job of the platform service's image (its
    tools layer, coder-image-tools, carries copa and trivy) beside a BuildKit sidecar, which reaches the builder's
    registry through the Job's own forwarder on localhost:5000."""
    r = default_render
    repo = os.path.dirname(B)
    tools = open(os.path.join(repo, "development/tools/build-image-tools.sh")).read()
    ui = open(os.path.join(repo, "development/tools/build-ui-image.sh")).read()
    tag = re.search(r'tag="\$\{1:-([0-9.]+)\}"', tools)
    tag = tag.group(1) if tag else ""
    check("pit stop: the tools image pins Copa's release by checksum and checks it runs",
          re.search(r"copa_[0-9.]+_linux_amd64\.tar\.gz", tools) and re.search(r"\b[0-9a-f]{64}\b", tools)
          and "copa --version" in tools, tag)
    check("pit stop: the platform service's image is built on that tools image, and its smoke test runs copa",
          tag and ("coder-image-tools:%s" % tag) in ui and "copa --version" in ui, tag)
    toml = re.search(r"buildkitd\.toml: \|\n((?:    .*\n|\n)+)", r)
    toml = toml.group(1) if toml else ""
    check("pit stop: BuildKit reaches the builder's registry by its Service and by the Job's localhost forwarder (HTTP)",
          re.search(r'\[registry\."coder-image-registry\.coder\.svc:5000"\]\s*\n\s*http = true', toml)
          and re.search(r'\[registry\."localhost:5000"\]\s*\n\s*http = true', toml), toml.strip()[:200])
    ib = open(os.path.join(chart, "files/ui-updates/imagebuilder.py")).read()
    check("pit stop: Copa's Job runs BuildKit as a native sidecar, patches from the original image with the whole "
          "report (--ignore-errors), and writes VEX",
          '"restartPolicy": "Always"' in ib and "--ignore-errors" in ib and "openvex" in ib
          and "COPA_EXPERIMENTAL" in ib, "")
    mk = open(os.path.join(B, "make")).read()
    check("pit stop: a minikube deploy seeds the workspace images into the builder's registry",
          "seed-registry" in mk and "coder-workspace(-dataflow|-ai)?" in mk, "")


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
    pit_checks(B, chart, r)
    icon_checks(chart, r)
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
    # the base template (the DEFAULT prototype): one main.tf, its platform sections marked for the template decorator
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
    # Coder reads the platform's sections from this template (its decorator: the Source Code view's Materialized, and
    # every template made from the prototype gets them at build): the fork's own extraction, run on it, where the fork
    # is checked out (CODER_FORK, else ~/workspace/coder). A for expression's names once broke it ([for p in ...]).
    import shutil
    fork = os.path.expanduser(os.environ.get("CODER_FORK", "~/workspace/coder"))
    if os.path.isfile(os.path.join(fork, "coderd/templatedecorator/decorator.go")) and shutil.which("go"):
        dec = subprocess.run(["go", "test", "-count=1", "-run", "TestTheChartsPrototype", "./coderd/templatedecorator/"],
                             cwd=fork, capture_output=True, text=True, timeout=900,
                             env=dict(os.environ, CODER_DECORATOR_PROTOTYPE=tf_dir, GOPROXY="off", GOFLAGS="-mod=mod"))
        check("template: Coder's decorator reads its platform sections (the fork's extraction, on this template)",
              dec.returncode == 0 and dec.stdout.startswith("ok"), (dec.stdout + dec.stderr).strip()[-300:])
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
    # The image store (development/image-store): the provided pictures, each with its preview, in the platform
    # service's image as a layer of their own under the code (build-ui-image.sh), served and listed by the service.
    repo_top = os.path.dirname(B)
    store_dir = os.path.join(repo_top, "development/image-store")
    pics = sorted(os.listdir(os.path.join(store_dir, "pictures"))) if os.path.isdir(os.path.join(store_dir, "pictures")) else []
    no_preview = [n for n in pics if not os.path.isfile(os.path.join(store_dir, "thumbs", n + ".webp"))]
    build_ui = open(os.path.join(repo_top, "development/tools/build-ui-image.sh")).read()
    svc = open(os.path.join(chart, "files/ui-updates/ui-service.py")).read()
    check("image store: the provided pictures, each with its preview, baked into the platform service's image under "
          "its code, served publicly and listed for every chooser",
          len(pics) > 0 and not no_preview
          and re.search(r"COPY image-store/ /image-store/\nCOPY app/ /app/", build_ui)
          and 'BASE + "/image-store/"' in svc and 'BASE + "/api/image-store"' in svc,
          {"pictures": len(pics), "without a preview": no_preview[:5]})
    # Ghostty in place of the web terminal (the DATAFLOW template's): its push asks for it, the app's slug is the one
    # the dashboard draws an agent's terminals with Ghostty for (site/src/modules/terminal/terminalConfig.ts), its icon
    # is served, and the built-in terminal is off where it is on.
    pushes = dict(re.findall(r'--variable app_set="(\w+)" \\\n\s*--variable terminal_app="(\w+)"', r))
    fork_slug = ""
    cfg_ts = os.path.join(os.path.expanduser(os.environ.get("CODER_FORK", "~/workspace/coder")),
                          "site/src/modules/terminal/terminalConfig.ts")
    if os.path.isfile(cfg_ts):
        m = re.search(r'GHOSTTY_APP = "([^"]+)"', open(cfg_ts).read())
        fork_slug = m.group(1) if m else ""
    app = re.search(r'resource "coder_app" "ghostty" \{(.*?)\n\}', tf["main.tf"], re.S)
    check("template: DATAFLOW's terminal is Ghostty (the Ghostty app in the Terminal's place), the others' Coder's own",
          pushes.get("dataflow") == "ghostty" and pushes.get("developer") == "web" and app
          and 'count        = local.ghostty ? 1 : 0' in app.group(1)
          and re.search(r'slug\s*=\s*"%s"' % (fork_slug or "ghostty"), app.group(1))
          and "web_terminal = var.terminal && !local.ghostty" in tf["main.tf"]
          and os.path.isfile(os.path.join(chart, "charts/coder-workspace/files/icons/ghostty.png"))
          and '"/__icons/ghostty.png"' in app.group(1), {"pushes": pushes, "fork slug": fork_slug})
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
    # ... and the image store's files (pictures/, thumbs/), as build-ui-image.sh hashes them after the code
    store_top = os.path.join(os.path.dirname(B), "development/image-store")
    store_files = sorted((os.path.relpath(os.path.join(d, f), store_top) for sub in ("pictures", "thumbs")
                          for d, _, fs in os.walk(os.path.join(store_top, sub)) for f in fs),
                         key=lambda x: x.encode())
    lines += "".join("%s  %s\n" % (hashlib.sha256(open(os.path.join(store_top, f), "rb").read()).hexdigest(), f)
                     for f in store_files)
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
    startup = open(os.path.join(tf_dir, "scripts", "startup.sh")).read() if os.path.isfile(
        os.path.join(tf_dir, "scripts", "startup.sh")) else ""
    fb_path = os.path.join(tf_dir, "scripts", "filebrowser.sh")
    fb = open(fb_path).read() if os.path.isfile(fb_path) else ""
    check("template: File Browser is FileBrowser Quantum, under the app's path, on 127.0.0.1, no sign-in of its own",
          "FileBrowser Quantum" in startup and '"$HOME/.local/bin/coder-filebrowser" start' in startup
          and '"$APPS/filebrowser/"' in fb and 'listen: "127.0.0.1"' in fb and "noauth: true" in fb
          and "disableUpdateCheck: true" in fb
          and 'filebrowser = base64encode(file("${path.module}/scripts/filebrowser.sh"))' in tf["main.tf"]
          and "/apps/filebrowser/health" in tf["main.tf"]
          # Coder strips the app's path and joins the rest to the URL's path: the URL must carry the path back
          and 'url          = "http://localhost:13339/@${data.coder_workspace_owner.me.name}/${data.coder_workspace.me.name}.main/apps/filebrowser/"' in tf["main.tf"], "")
    # Quantum opens no link out of a source's folder (403): the Cloud Drives (links to /mnt/...) are sources of their
    # own, read only where shared read only, their links hidden in the Start folder; the watcher restarts it as the
    # drive is connected or disconnected.
    cd_sh = open(os.path.join(tf_dir, "scripts", "cloud-drive.sh")).read()
    check("template: File Browser shows every Cloud Drive here as a source of its own (read only where shared so)",
          '"$HOME/cloud-drive-shared"' in cd_sh and "readOnly" in cd_sh and "readOnly" in fb
          and "rule filePath" in fb and "rule folderPath" in fb and "sidebarLinks" in fb
          and 'cloud_drive drives | jq' in fb and 'cloud_drive targets |' in fb and 'cloud_drive links |' in fb
          and '"$FB" restart' in cd_sh and 'setsid "$SELF" watch' in cd_sh
          and 'declare -f state_now target_of sync_link files_again files_check watch probe source_of drives targets links' in cd_sh, "")
    # The kubelet may take a minute or more to update the annotation's file in the pod: the platform service also
    # tells the workspace at once (cloud-drive.sh push, by exec), and the watcher follows whichever was written last.
    # The workspace says whether the drive is attached (status: its agent's metadata cloud_drive, in the Cloud Drive's
    # platform section), which the service reads from Coder's database for the Workspaces page.
    safe_py = open(os.path.join(chart, "files/ui-updates/safe.py")).read()
    ui_py = open(os.path.join(chart, "files/ui-updates/ui-service.py")).read()
    agent_part = re.search(r'resource "coder_agent" "main" \{.*?# platform:begin cloud-drive\n(.*?)# platform:end cloud-drive',
                           tf["main.tf"], re.S)
    check("Cloud Drive: told at once (push by exec), the newer of it and the annotation's file followed",
          'if { read -r at state <"$PUSHED"; } 2>/dev/null && [ "$${at:-0}" -ge "$file_at" ]; then' in cd_sh
          and 'file_at="$(stat -L -c %Y "$STATE"' in cd_sh and "push) push" in cd_sh
          and 'PUSH_HELPER = "/tmp/coder-cloud-drive/cloud-drive.sh"' in safe_py
          and '[PUSH_HELPER, "push", want]' in safe_py and "push_soon(pod[\"metadata\"][\"name\"], wid, want)" in safe_py, "")
    check("Cloud Drive: the workspace says whether it is attached (metadata cloud_drive), the service reads it",
          bool(agent_part) and 'key          = "9_cloud_drive"' in agent_part.group(1)
          and "for_each = var.user_storage ? [1] : []" in agent_part.group(1)
          and "cloud-drive.sh status 2>/dev/null || echo starting" in agent_part.group(1)
          and all(w in cd_sh for w in ("echo attached", "echo unanswered", "echo detached", "echo waiting"))
          and "m.key ~ '(^|_)cloud_drive$'" in ui_py and "attachment=safe_cloud_drive_attachment" in ui_py
          and '"attachment": attachment(user["id"])' in safe_py, "")
    # The Cloud Drive's logic is in its own script alone: File Browser asks it which drives are here (it writes itself
    # to /tmp/coder-cloud-drive/cloud-drive.sh at start, per container), and the startup script knows nothing of it.
    cd_paths = ("cloud-drive-shared", "/mnt/cloud-drive", "CLOUD_DRIVE_LINK", "cloud_drive_link")
    leaked = ["%s: %s" % (n, p) for n, text in (("startup.sh", startup), ("filebrowser.sh", fb)) for p in cd_paths
              if p in "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))]
    check("template: the Cloud Drive's logic in one script (cloud-drive.sh; File Browser asks it, startup.sh has none)",
          not leaked and "cloud_drive_link" not in tf["main.tf"]
          and "SELF=/tmp/coder-cloud-drive/cloud-drive.sh" in cd_sh
          and "cloud_drive_sh=/tmp/coder-cloud-drive/cloud-drive.sh" in fb
          and cd_sh.index("write_self\n") < cd_sh.index("sync_link\nlink_shared\nfiles_again"), leaked[:3])
    # NiFi and Airflow start in scripts of their own (the dataflow template's only), beside the startup script, which
    # knows nothing of them.
    nifi_sh = open(os.path.join(tf_dir, "scripts", "apache_nifi.sh")).read()
    airflow_sh = open(os.path.join(tf_dir, "scripts", "apache_airflow.sh")).read()
    code = "\n".join(re.sub(r"\s+#.*$", "", ln) for ln in startup.splitlines()
                     if not ln.lstrip().startswith("#")).lower()
    check("template: NiFi and Airflow each a coder_script of its own (apache_nifi.sh, apache_airflow.sh), dataflow only",
          not any(w in code for w in ("nifi", "airflow", "dataflow"))
          and re.search(r'resource "coder_script" "nifi" \{\n  count        = local\.dataflow \? 1 : 0\n(?:.*\n){0,6}?'
                        r'  script = templatefile\("\$\{path\.module\}/scripts/apache_nifi\.sh"', tf["main.tf"])
          and re.search(r'resource "coder_script" "airflow" \{\n  count        = local\.dataflow \? 1 : 0\n(?:.*\n){0,6}?'
                        r'  script = templatefile\("\$\{path\.module\}/scripts/apache_airflow\.sh"', tf["main.tf"])
          and "nifi.web.proxy.context.path=$NIFI_PATH" in nifi_sh and "exec airflow standalone" in airflow_sh
          and "code_server            = !local.dataflow" in tf["main.tf"], "")
    # Beside the startup script, Airflow's line in ~/.bashrc waits for the startup script's default ~/.bashrc (added
    # first, it would keep the default out).
    check("apache_airflow.sh: its ~/.bashrc line after the startup script's default ~/.bashrc",
          "bashrc_ready && break" in airflow_sh and "/usr/share/coder-workspace/ubuntu-bashrc" in airflow_sh
          and "/usr/share/coder-workspace/ubuntu-bashrc" in startup, "")
    # Every workspace, whatever started it: the PID file per container (one in the home names another process after a
    # restart); the helper also comes with the Cloud Drive's platform section (the decorator gives templates made in
    # the builder the base's current one at every build), the newer one kept; the watcher's check takes over a File
    # Browser an older startup.sh started; a drive whose storage does not answer is left out, not waited on.
    cd_section = re.search(r"# platform:begin cloud-drive\n(.*?)# platform:end cloud-drive", tf["main.tf"], re.S)
    check("template: File Browser keeps every Cloud Drive through restarts and in every template (builder-made too)",
          "pidfile=/tmp/" in fb and '"/proc/$1/comm"' in fb and "HELPER_VERSION=" in fb and "install)" in fb
          and "check) flock -n 9" in fb and "timeout 10 test" in cd_sh and 'grep -qxF "$config"' in fb
          and bool(cd_section) and 'filebrowser = base64encode(file("${path.module}/scripts/filebrowser.sh"))'
          in cd_section.group(1)
          and 'bash "$fb_helper" install' in cd_sh and 'bash "$fb_helper" install' in startup
          and "files_check" in cd_sh, "")
    # Folders and files of a Cloud Drive shared with the owner: each its owner's volume's subPath, mounted apart
    # (/mnt/cloud-drive-items/<share id>, one volume per owner's volume and access) and linked under
    # ~/cloud-drive-shared/<owner>/; File Browser makes each shared folder a source of its own.
    check("template: shared folders and files of a Cloud Drive mounted by subPath and linked in ~/cloud-drive-shared",
          "/mnt/cloud-drive-items/${volume_mount.value.id}" in tf["main.tf"]
          and "sub_path  = volume_mount.value.path" in tf["main.tf"]
          and "cloud_drive_peer_volumes" in tf["main.tf"] and "for_each = local.cloud_drive_peer_volumes" in tf["main.tf"]
          and "items = base64encode(jsonencode(" in tf["main.tf"]
          and 'ITEMS=/mnt/cloud-drive-items' in cd_sh and '-lname "$ITEMS/*" -delete' in cd_sh
          and "(shared items)" in cd_sh and 'source_of "$item"' in cd_sh and "unique_names" in fb, "")
    # The desktop's look: the Desktop theme parameter (where the template has the desktop), put in the home by the
    # workspace image's coder-desktop-theme before the desktop starts (an image without it: skipped).
    check("template: the Desktop theme parameter (Serenade by default, or Classic) applied before the desktop starts",
          'data "coder_parameter" "desktop_theme"' in tf["main.tf"] and 'default      = "serenade"' in tf["main.tf"]
          and 'value       = "classic"' in tf["main.tf"]
          and 'desktop_theme          = var.desktop ? data.coder_parameter.desktop_theme[0].value : "classic"' in tf["main.tf"]
          and "DESKTOP_THEME='${desktop_theme}'" in startup
          and '/usr/local/bin/coder-desktop-theme apply "$DESKTOP_THEME"' in startup
          and startup.index("coder-desktop-theme apply") < startup.index("Xtigervnc :1 -auth"), "")
    # An image without coder-desktop-theme (one derived from a workspace image before 1.9.1, the dataflow image until
    # 1.1.3): the desktop comes up Classic whatever was chosen - said in the startup log, not silent.
    # ./make --deploy rewrites .deploy/<domain>/values.yaml: a cluster's own settings (one template not pushed again,
    # say) go in values-local.yaml beside it, passed after it.
    make_sh = open(os.path.join(B, "make")).read()
    check("make --deploy applies .deploy/<domain>/values-local.yaml after the values it writes",
          '"${STATE}/values-local.yaml"' in make_sh
          and '-f "$VALUES" "${extra[@]}"' in make_sh, "")
    check("template: a theme the image cannot apply is said in the startup log",
          "cannot be applied: Classic instead" in startup
          and startup.index("cannot be applied: Classic instead") < startup.index("Xtigervnc :1 -auth"), "")
    check("template: the workspace image is substituted (not codercom/enterprise-base)",
          "codercom/enterprise-base" not in r, "")
    ws_img = re.search(r'workspaceImage: .*?"(ghcr\.io/user187x/coder-workspace:[^"]+)"', vals).group(1)
    check("the workspace image the chart runs is the bundled one", ws_img in images, ws_img)
    dfp = os.path.normpath(os.path.join(B, "../development/workspace-image/Dockerfile"))
    if os.path.isfile(dfp):
        df = open(dfp).read()
        # code-server's release download, and FileBrowser Quantum's release binary (its filebrowser stage unpacks it)
        check("workspace image: code-server and File Browser (Quantum) preinstalled, pinned by SHA-256",
              "CODE_SERVER_SHA256=" in df and "FILEBROWSER_QUANTUM_SHA256=" in df
              and "COPY --from=filebrowser /out/filebrowser" in df and "FILEBROWSER_SRC_SHA256" not in df, "")
        # Chromium (xtradeb's, its key by fingerprint, pinned to Chromium alone) the default browser, Terminator the
        # terminal; the Serenade theme's sources (picom's fork, eww, Miserable_Xfce) each at a commit, by SHA-256.
        theme_sh = open(os.path.join(os.path.dirname(dfp), "desktop", "coder-desktop-theme")).read()
        helpers = open(os.path.join(os.path.dirname(dfp), "desktop", "helpers.rc")).read()
        check("workspace image: Chromium the default browser and Terminator the terminal; Serenade's sources pinned",
              "5301FA4FD93244FBC6F6149982BB6851C64F6880" in df and "Pin: release o=LP-PPA-xtradeb-apps" in df
              and "update-alternatives --set x-www-browser /usr/bin/chromium" in df
              and "update-alternatives --set x-terminal-emulator /usr/bin/terminator" in df
              and "WebBrowser=chromium" in helpers and "TerminalEmulator=terminator" in helpers
              and all(re.search(r"^ARG %s=[0-9a-f]{%d}$" % (k, n), df, re.M) for k, n in (
                  ("PICOM_COMMIT", 40), ("PICOM_SHA256", 64), ("EWW_COMMIT", 40), ("EWW_SHA256", 64),
                  ("MISERABLE_COMMIT", 40), ("MISERABLE_SHA256", 64)))
              and 'backend = "xrender";' in df and "WebBrowser=chromium" in theme_sh
              and "TerminalEmulator=terminator" in theme_sh and "before-serenade" in theme_sh, "")
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
        check("argocd/update-application.sh: the AI template off by default, --ai keep runs %s" % ai_image, ok,
              (err2 or out2 or err or out).strip()[-300:] if not ok else "")
    # the layer bundle's helper (Python): compiles, and answers --help
    rc, out, err = run([sys.executable, os.path.join(B, "layer-support"), "--help"])
    check("layer-support: runs (--help)", rc == 0 and "layer-support build" in out, (err or "").strip()[-200:])
    passed = sum(x["ok"] for x in RESULTS)
    print("\n%d/%d static checks passed" % (passed, len(RESULTS)))
    sys.exit(0 if passed == len(RESULTS) else 1)


if __name__ == "__main__":
    main()

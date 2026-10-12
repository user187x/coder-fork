# Changelog

## 1.26.1 – 2026-10-12

Coder `2.0.0-platform.23` (rebuilt: `fbfb8f451`).

* **Workspace metric graphs, coloured by magnitude.** Each bar shows how much of what there is its reading uses:
  * green under 50 %;
  * yellow-green at 50–64 %;
  * yellow at 65–74 %;
  * orange at 75–89 %;
  * red from 90 % (critical).

  Load average counts per core (1.0 = every core busy). A metric without a scale stays green.
* **Metrics Interval menu**, in the agent card's header, directly left of Open ports and built like it: the interval
  in the circle (10s, 1s, 5s, 30s, 1m, 5m or off), then "Metrics Interval". Its menu offers:
  * Live (a bar per reading, as the agent sends it);
  * 1 s or 5 s: the graphs advance every second or five. Between the agent's readings (every 10 s, set in the
    template), the last reading holds, drawn dimmer;
  * 30 s, 1 min or 5 min (a bar per period, its highest reading, so spikes stay visible; 40 bars cover 20 min to
    3.3 h);
  * Off (no graphs and nothing recorded, for a browser short on processing power).

  It also says how often the agent reports each metric (Home Disk and Load Average every 60 s) and shows the colour
  legend. The choice is remembered in the browser.

## 1.26.0 – 2026-10-12

Coder `2.0.0-platform.23`.

* **HUD: live usage, and three graphs under the totals.**
  * **metrics-server is now part of the chart** (`metricsServer.enabled: auto`): it is installed only where the
    cluster has no metrics API yet, so an existing one (the EKS add-on) stays in charge. The HUD said "no live usage"
    because the cluster had none. On Minikube, `./make` sets `metricsServer.kubeletInsecureTLS`.
  * New totals: **CPU in use** and **Memory in use** by running workspaces, live, next to what they claimed.
  * **Graphs** of system latency (the median and 95th percentile of users' connections to their workspaces, from
    Coder's agent stats), total CPU used and total memory used (the cluster, and the workspaces' part of it). Ranges:
    15 minutes to 7 days, with hover details. The platform service samples them every 15 seconds whether or not
    anyone looks, and keeps 7 days (`/data/hud.sqlite`).
  * The "Live" mark turns amber when metrics-server does not answer. (Before, it went by whether any workspace pod
    was measured.)
* **Templates:**
  * **Overview** replaces Resources. It shows the resources, with the docs (README) below them; the Docs tab is gone.
  * **Source Code** holds the versions: a compact list under the file tree. Click one to see its code there; Promote
    sits on the right of each entry; small active / newest / failed marks.
  * **Squash** (template editors): pick the two ends of a range. The newest version of the range stays, with the
    others' messages added to its own, and the others are archived. The active version and any version a workspace
    still uses stay.
  * **Insights** is now **Details**.
  * **Embed** no longer shows the button preview or "Copy button Markdown". **⋮ › Share** copies the Open in Coder
    button (Markdown or HTML) or its link, with the Embed tab's settings. Share is in everyone's menu; the rest of
    the menu is still for editors.
  * Old links (`/docs`, `/resources`, `/versions`) redirect.
* **Workspaces:**
  * **Their own icon:** Settings › General › Icon (emoji, Coder icon or path). It shows in the workspaces list and
    next to the name on the workspace page. Empty: the template's icon again.
  * **⋮ › Back up home** (owners and admins): the home volume as a .tar.gz, to S3 (Images › Settings) or as a
    download kept 24 hours. A Job beside the workspace streams it; the workspace keeps running.
  * **⋮ › Expand disk** (owners and admins): a bigger home volume. Where the storage class grows volumes (EBS on
    EKS), in place, with one restart only if the file system needs it. Elsewhere (local-path), the workspace is
    stopped, its home copied onto a bigger volume that takes the old one's name, and started again; owners and modes
    are kept. Tested on Minikube: 1 → 3 GiB in 28 s, data intact.
  * The workspace template ignores the home volume's size after creation (`lifecycle { ignore_changes }`), so a
    later build never shrinks a grown disk.
* **Workspace metrics graphs** (workspace page, the agent's metrics): every metric (CPU Usage, RAM Usage, Home Disk,
  Load Average, the host's, …) has a live bar graph of its readings between its name and its value: the last 40
  readings (about 7 minutes), newest on the right, percentages on a 0–100 % scale, hover for each reading's time and
  value. Kept while the page is open.
* **Agent outdated indicator** (workspace page, next to the agent's name): the glowing green **Update** pill of
  `workspace-update.html` (rim, recessed refresh icon with a pulsing ring), built in HTML and CSS for both themes,
  instead of the badge images. Clicking it still opens the details and "Update workspace".
* **Images:** the New Image dialog fits without a horizontal scroll bar.
* New bundled image: `registry.k8s.io/metrics-server/metrics-server:v0.8.1`. The platform service's builder Role may
  now manage PVCs in the workspace namespace; workspace backups use the image builder's volume.

## 1.25.0 – 2026-10-12

Coder `2.0.0-platform.22`.

* **Templates › Images: an image builder in the cluster.** Workspace images for templates to run, built from the
  dashboard. The tab sits next to Templates (also in the Resources menu). The page shows a filtered table of images:
  status, contents, size, a security grade, FIPS readiness, when and by whom each was built, a download button and S3
  backup. Admins and template admins create, edit, build and delete images; everyone else can see them.
  * **The builder, step by step,** saved as you go:
    * **Basics:** the image starts from the workspace image or from another built image.
    * **Packages:** apt packages with suggestions as you type. Each is checked in the apt sources, about 150,000
      packages for Ubuntu 26.04, or your mirror. A package that isn't there is marked and stops the build until it is
      dealt with: upload its `.deb`, turn it into a manual installation, or remove it.
    * **Installations:** named, each a row that opens to its files (uploads up to 2 GB) and its bash script. The
      script editor highlights, lints with shellcheck as you type (squiggles plus a list), and formats with
      `shfmt -i 1`. A script with shellcheck errors stops the build.
    * **Remove & harden:** packages to purge (one the image doesn't have is skipped), plus 8 hardening switches:
      documentation, sudo, root password, setuid bits, compilers, umask, core dumps, SSH. Also FIPS-aligned crypto
      and your own hardening script.
    * **Desktop:** as the base has it, XFCE, GNOME (Flashback, made for VNC) or none.
    * **Configuration:** environment, aliases, functions and dotfiles (typed or imported).
    * **Review & build:** a summary, what stops the build, the build's steps, and the generated Dockerfile.
  * **Builds run as Kubernetes Jobs with BuildKit rootless.** They are pushed to a registry in the cluster
    (`coder-image-registry`). Every node pulls from `localhost:5000` through a small proxy DaemonSet, so no node
    needs configuring.
  * **The build view is the workspace start's panel:**
    * the stripes and bricks, "IMAGE BUILDING";
    * a bar that moves on the build's real steps;
    * the current and next step, the time taken, and a checklist;
    * the live log;
    * on failure, the step that failed and why (apt's own message).
  * **When an image is ready,** the build view shows its reference (`localhost:5000/coder-images/<name>:<version>`)
    and the Terraform for a template.
  * **A security health report for every build:**
    * vulnerabilities by severity (trivy, against the vulnerability database in the bundle), with the worst listed;
    * secrets found in files;
    * checks on users, passwords, root, sudo, setuid programs, world-writable files, SSH host keys, leftovers,
      packages and compilers;
    * a FIPS 140 check: the OpenSSL FIPS module and default, TLS, SSH, other crypto libraries, the kernel.
  * **Download** gives a `docker load` tarball. **S3 backup**, per image after every build or on demand, goes to AWS
    S3 or an S3-compatible store such as MinIO. The settings are in Images › Settings, and the secret key is never
    shown again.
  * Finished builds remove their Jobs (and BuildKit's scratch space) at once. A slow Kubernetes API is retried
    instead of failing the build. The registry garbage-collects deleted images' layers each time it starts.
  * **The workspace template follows the image:** its desktop session (GNOME), no desktop when it has none, and its
    dotfiles copied into home directories that lack them.
* **New images in the bundle:**
  * `ghcr.io/user187x/coder-image-tools:1.0.0` (`development/image-tools/`): Python with shellcheck, shfmt, trivy and
    its database, and regctl. It is now the platform service's image.
  * `moby/buildkit:v0.33.0-rootless`.
  * `registry:3.1.2`.
* **Platform service:**
  * memory limit 192 Mi → 512 Mi;
  * volume `coder-image-builder` (20 Gi);
  * registry volume (50 Gi).
  * Settings: `imageBuilder` in `values.yaml`. EKS notes: `DEPLOY.md` › *the image builder*.

## 1.24.1 – 2026-10-12

Coder `2.0.0-platform.21` (unchanged).

* **Cleanup: the platform service's old dashboard add-ons are gone.** These were 41 scripts, styles and pages in
  `files/ui-updates/` (`dashboard.js`, `views.js`, `chat.js`, …, about 570 KB). The fork's dashboard has had all of
  them built in, and nothing loaded them any more.
  * With them go the service's route for its static files, the `static` volume and `STATIC_DIR`. Only the service's
    own code remains: `ui-service.py`, `pgwire.py` and `certinfo.py`.
  * The Helm release is smaller, leaving more room under its 1 MiB limit.
* `example.html` is removed: it is the built-in Particles background now (1.24.0).

## 1.24.0 – 2026-10-12

Coder `2.0.0-platform.21`.

* **Animated and HTML backgrounds** (Customize › Background image), next to pictures.
  * **Particles, built in.** The animated background of `example.html`: glowing orbs circling slowly over a plain
    colour. Its own dials: the background colour, the three particle colours, how many particles (1–60), their size,
    how far each glow sits from its particle, and how soft it is. "Original colours and sizes" brings back
    `example.html` exactly.
  * **Your own HTML.** Paste a page (HTML with its CSS, and scripts if it needs them), or import an `.html` file (up
    to 256 KB). "Start from the Particles example" fills in the example to edit.
  * **Dials for every HTML background:**
    * **Speed** (0.1–4×) changes the playback rate of every CSS / Web animation on the page, so it works for any
      HTML;
    * **Hue shift, saturation, brightness and blur** are colour filters;
    * then the usual **strength** and **layer** (behind the page or over it).
  * A live preview shows the result before saving.
  * **Safe to show to everyone, on the sign-in page too.** The page runs in a sandboxed frame with an origin of its
    own. It cannot reach Coder's page, its cookies or the network: the platform service serves it with a sandbox
    Content-Security-Policy that blocks connections, and allows only inline styles and scripts and `data:` pictures.
  * The mouse passes through it, and it pauses while the Animations switch (user menu) is off.
  * **Behind the page,** the frame lies beneath every element. While it shows, the page colour is moved from `body`
    to `html` so the frame can show through; the page looks the same, and the change is undone when it goes.
  * Stored like the pictures (`coder_ui_updates.background`, new column `params` for the dials).
* The platform service loads the backgrounds as it starts, so the first pages after a restart have them too.

## 1.23.0 – 2026-10-12

Coder `2.0.0-platform.20`.

* **Chat: screenshots work again.** "No screenshot: Attempting to parse an unsupported color function "oklab"": html2canvas
  1.4.1 cannot read the `oklab()` / `oklch()` colours Tailwind 4 uses. The dashboard now uses html2canvas-pro 2.4.5 (a
  maintained fork that reads them). It is bundled into the dashboard, so nothing is fetched at run time and it works
  air-gapped.
* **Chat: no more double messages.** When a message opened a window, it was queued while the conversation's history
  loaded, and the history held it too: it showed twice (stored once). Every message is now shown once per id.
  * Each send also carries an id of its own (`clientId`), and the platform service stores a retried send once. The
    dashboard retries a send that got no answer or a proxy error (502 / 503 / 504), up to twice. A second Enter in
    the same instant no longer sends twice.
* **Chat: built for volume.**
  * The platform service's HTTP server now queues up to 1024 waiting connections, up from Python's default of 5, at
    which a burst of sends got 502 from the proxy. It closes the connections it says it will close
    (`Connection: close`). A tab's long poll ending mid-answer is no longer logged as an error. Sending is thread-safe.
  * Admins may send 120 messages a minute (others 30).
  * Measured on Minikube: 40 developers sending 120 messages at once were all accepted in about 0.6 s, each stored
    once.
  * Long conversations load 100 messages at a time: scroll to the top, or "Earlier messages".
* **Chat: the console, for admins.** The admin's Chats bar opens one command center instead of a pile of windows.
  * **Left, the card index ("Rolodex").** Every conversation in one list, in three views:
    * **Waiting:** the last message is theirs, longest wait first (triage order);
    * **Unread;**
    * **All.**

    Search by person or text. Each row shows how long they have waited, the unread count and "typing…". Only the
    rows on screen are drawn, so hundreds of conversations stay quick.
  * **Right, the card.** One conversation, with previous / next, "Next waiting", screenshot, pop out (into its own
    window) and delete. Moving through the index flips the card in, unless animations are off.
  * **Next after reply.** When on, sending an answer flips straight to the next conversation waiting.
  * **Keys:** Alt+↓ / Alt+↑ next / previous, Alt+N next waiting, / or Ctrl+K search, ↑ ↓ Enter in the list, Escape
    back to the list (from the message box) or closed.
  * **No more pop-up storms.** For admins, a new message opens a window only while the console is closed and the
    dock has room (up to three). Otherwise it waits, counted, in the console. Developers' chat is as before.
* **Customize: background images** for the sign-in page and the dashboard, each with its own image, strength (2–30 %,
  default 6 %: barely noticeable), fit (fill, fit inside, tile) and layer.
  * **Behind the page (default).** The picture lies beneath everything, mixed into the page's own background colour
    (under a veil of that colour). It shows only where the page has no background of its own, follows the theme, and
    leaves every other style as it is.
  * **Over the page.** A faint watermark the mouse passes through.
  * Upload any picture (made at most 1920 × 1200, WebP; an animated GIF is kept as it is) or pick one of the
    provided pictures. A preview shows the result.
  * The platform service keeps them in Coder's database (`coder_ui_updates.background`). `boot.js` hands them to
    every page, so they are there from the first paint, the sign-in page included.

## 1.22.1 – 2026-10-11

Coder `2.0.0-platform.19` (unchanged).

* **Cleanup.**
  * The route that sent `/workspaces-icon.png` to the platform service is gone, and so is the chart's copy of that
    icon. The dashboard now shows its own light and dark versions (1.22.0).
  * The platform service's old logo page (`logo.html`, `logo-view.js`, `logo.css`) is gone. **Admin settings ›
    Customize** has been the logo editor since the fork took it over.
  * The design source folders `alt-logos/` and `update-resources/` were moved into the chart (`defaults/gallery/`) and
    the fork (`site/static/`) in 1.22.0, and are removed.

## 1.22.0 – 2026-10-11

Coder `2.0.0-platform.19` (fork `2260748f8`).

* **The update gear and the Resources button follow the theme.** Each has its own image for the light theme (black)
  and the dark theme (white), and the dashboard shows the one for the selected theme (`agent-outdated-light/-dark.png`,
  `workspaces-icon-light/-dark.png`).
* **Provided pictures for the logo and the avatar.** Three pictures come with the platform (destructed cube, loading
  cube, pulsing cube) in `helm-chart/defaults/gallery/`.
  * **Customize › Logo** (*Choose a new logo*) and the **Avatar** dialog show them under "choose your own". A click
    uses one exactly like a chosen file: preview, then Save.
  * `install.sh` puts each picture in its own ConfigMap (`coder-ui-updates-gallery-1`, `-2`, …; a ConfigMap holds at
    most 1 MiB), outside the Helm release. The platform service serves them (`/__coder-ui/api/gallery`,
    `/__coder-ui/gallery/<file>`). Add or remove files there and redeploy. Up to 8 (`uiUpdates.defaults.gallerySlots`).
* **Post-it notes: from the workspace page's sidebar, and in the users table.**
  * Admins looking at another user's workspace get an amber sticky-note button in the left sidebar, under Resources
    and History. It starts a new note, with a count of the notes up there. It replaces the floating "Add a note"
    button. It does not show on an admin's own workspace.
  * **Admin settings › Users:** the Groups column is now **Notes**. A sticky-note icon (with a count) shows when admins
    have notes up on the user's workspaces that the user has not dismissed. A click shows them: the text, the
    workspace (a link) and who wrote it.
  * Notes now record their workspace's owner and name. Older notes get them the first time an admin opens the users
    table.
* **Announcement banner.**
  * The scroll-down view's prompt is root's: `root@platform:~#`.
  * The Confirm button's background is a grayer, slightly lighter shade of the banner's colour.
  * The Viewed box's tooltip reads `Announcement Viewed:` / `Date: October 11, 2026 @ 10:29 PM UTC-5` /
    `TLS/x509 Fingerprint: AB:CD:…`, in the viewer's time zone.
* **Certificates record their x509 fingerprint.** Settings › Certificates now saves the certificate's SHA-256
  fingerprint (of the certificate itself, as `openssl x509 -fingerprint -sha256` shows it) with its other details,
  when the password is given at upload. The password is still only used in the browser. Certificates uploaded
  before have no fingerprint on record, and the tooltip says so: upload them again, with the password, to add it.

## 1.21.0 – 2026-10-11

Coder `2.0.0-platform.18` (unchanged), workspace image `coder-workspace:1.7.0`.

* **Admins run kubectl and k9s on Coder's cluster from their workspaces, each signed in as themselves.**
  * Coder's Owners get `~/.kube/coder-cluster.yaml` (context `<owner>@coder-cluster`, first in `KUBECONFIG`; their own
    `~/.kube/config` is never touched). Other users' workspaces get nothing.
  * The first `kubectl` command prints a Keycloak link and code (device login). The admin opens it in their own
    browser and signs in as they do for Coder (e.g. with their certificate). The first time, they confirm the grant.
    No browser opens by itself.
  * The cluster sees the person: `keycloak:<username>`, in group `keycloak:coder-cluster-admins`, which the chart
    binds to `cluster-admin`. An API server audit log, where one is turned on, records that name. No account or token
    is shared.
  * The workspace keeps no long-lived credential. The tokens live 10 minutes and are kept in `/tmp`, so they are
    forgotten when the workspace restarts. The sign-in ends with the Keycloak session (development Keycloak: 30
    minutes idle, 8 hours at most).
  * The platform service keeps the Keycloak group equal to Coder's Owners who sign in with Keycloak. It checks every
    minute, matching by email, else username. The built-in password `admin` has no Keycloak account and so cannot
    sign in to the cluster.
  * `./make --deploy` on Minikube sets up the development Keycloak's public client `kubernetes` (device grant only,
    consent, a `groups` claim) and the group. It also makes the API server trust Keycloak's tokens with a structured
    authentication configuration; the API server restarts once, and the flag is kept across `minikube start`. EKS
    and other clusters: `DEPLOY.md` › *kubectl and k9s for admins*.
  * Settings: `global.clusterAccess` (off by default in the chart).
* **Workspace image 1.7.0:** adds kubelogin `v1.36.4` (`kubectl oidc-login`, pinned by its release checksum) to the
  `k8s-devtools` package (1.1.0, the same k9s and kubectl).

## 1.20.0 – 2026-10-11

Coder `2.0.0-platform.18` (fork `25b73e9ed`).

* **The outdated-agent gear pulses.** Its glow swells and fades now and then, at irregular intervals: two loops of
  different lengths (11 s and 17 s) overlap, so the rhythm never settles. It is off when animations are off (user
  menu) or the system asks for reduced motion.
* **Post-it notes on workspace pages.** Admins stick short notes on a user's workspace page: reminders, how-tos,
  "don't forget" messages.
  * **Add a note** (admins only, bottom left) opens a draft. Write up to 600 characters, pick a colour (yellow, pink,
    blue, green, orange), then **Stick it**.
  * The notes sit above everything on the page. Each hangs a little crooked (its own tilt, kept), with a strip of
    tape, a curled corner and a handwritten look.
  * Admins drag a note to move it (the place is kept, relative to the window), double-click or use the pencil to
    edit it, and use the bin to delete it.
  * The workspace's owner sees the notes and dismisses them with ×. Admins still see a dismissed note, faded and
    marked "dismissed by the owner", until they delete it.
  * The platform service stores the notes (`/__coder-ui/api/notes`). Only the workspace's owner and admins can read
    a workspace's notes; only admins can create, change or delete them.

## 1.19.0 – 2026-10-11

Coder `2.0.0-platform.17` (fork `73d2c8a54`).

* **App panels pop out into their own browser window, and come back.** This works like the Terminal panel.
  * **Pop out** moves the app (File Browser, Desktop, marimo, code-server) into a browser window of its own. The
    panel lets go of it: one copy of the app, not two (the desktop has a single screen).
  * **Bring back**, or closing that window, shows it in the panel again. The bin closes the window too.
* **Announcements: the Star Wars opening crawl.** The new effect "Star Wars crawl" makes the message rise through the
  banner, tilted away, and recede into the distance, fading at the top.
  * Its pace is steady: the duration follows the text's height and a **speed** setting (slow, normal, fast), so a
    long broadcast takes as long as it needs.
  * Without repeat it plays once and then the message stays.
* **Announcements: a font selector.** It applies to the banner and its scroll-down view. The options are the
  dashboard's bundled fonts (Geist, Geist Mono, IBM Plex Mono, JetBrains Mono, Fira Code, Source Code Pro) and
  generic system serif, sans and condensed stacks. Nothing is downloaded, as the network may be air-gapped.
* **Announcements: scroll mode.** A bordered down arrow at the banner's left rolls a terminal-like view of the whole
  message down over the page, so nobody has to wait for an effect.
  * It sits above everything, about a quarter of the page tall, and scrolls when the message is longer.
  * It renders headings, lists, links, code, emoji and images in the banner's font, on a dark, monospaced,
    faintly scan-lined screen with a prompt and a blinking cursor.
  * A click outside, Escape or the arrow rolls it back up.
* **Announcements can be longer and richer.**
  * Up to 4000 characters (was 400), in the dashboard and in the banner service, including its own admin page.
  * Markdown images: `![alt](https://… or /path)`, small inline in the banner and full width in the scroll-down view.
  * For the scroll-down view: `#` headings, `-` / `1.` lists, and blank lines between paragraphs.
  * The banner service stores `speed` and `font`. Announcements saved before keep their look (normal, the dashboard
    font).

## 1.18.1 – 2026-10-11

Coder `2.0.0-platform.16` (fork `6e0f2b39b`).

* **A workspace's apps also wait for their health checks.** marimo, code-server and File Browser start in the
  background and come up after the startup script ends. Before, the apps unlocked when the script finished, so
  marimo could still be starting.
  * Now the initializing panel stays until every app has answered its health check: "Starting the apps", how many
    are up, which are still starting.
  * The card's border stays "initializing" too.
  * An app that fails its checks (unhealthy) does not hold the others back.
* **The initializing panel's progress bar shows real progress.** It follows the whole start, by milestones: the agent
  connecting, each startup script finishing, each app answering its health check.
  * Between milestones it creeps toward the next one but never past it. It shows a percentage, and its striped,
    sheened fill moves smoothly; the sheen stops with Animations off or reduced motion.
  * Before, it showed only the current step's share, for example stuck at 50% for the whole main startup script.
* **The outdated-agent badge follows the theme.** The image holds both variants side by side, and each theme shows its
  half.
* **The Viewed box** in the announcement banner has a very faint background of thin diagonal zebra lines. It also
  shows the "seen and accounted for" badge where "at" was: the fork's `static/announcement-viewed.png`, trimmed from
  `development/coder-fork/design/acknowledge.png`.
* **The desktop app is "Desktop"**, with Coder's own desktop icon. It was "Desktop (noVNC)", and its noVNC logo was
  loaded from the internet, so it showed broken offline.

* **tkinter windows in marimo show in the notebook.** A cell that opens a tkinter window shows it in its own
  output, live and interactive (clicks and typing work there), not on the desktop. Before, it failed with
  `no display name and no $DISPLAY environment variable`.
  * How: each workspace has a second, private X display (`:2`: no desktop, no window manager) for notebook windows,
    sized to the window it shows. The cell's output is a minimal noVNC view of it, through the hidden app
    `marimo-windows` (websockify on 127.0.0.1:6902).
  * **Stop works at once and closes the window, like its close button.** The cell ends normally with
    "Window closed (Stop)." instead of an interrupt traceback.
    * Before, `mainloop()` waited in Tk's C event loop, where Python only notices marimo's interrupt at the next
      window event.
    * Before, an interrupted or failed cell also left its window open but dead.
    * Now, in a marimo kernel, `mainloop()` runs the window's events from Python. This is `coder_marimo_tk.py`,
      which the template puts in each user's `~/.marimo-venv`.
  * Other libraries' GUI windows (with `DISPLAY`) show on the workspace desktop (Desktop app).
  * The desktop's websockify is now checked by its port, because `pgrep -f` matched the startup script itself.
* `minikube-support trust-ca` also covers sandboxed browsers. The snap and flatpak builds of Chromium, Brave and
  Chrome don't use the machine's certificate store, and neither did `trust-ca`.
  * Why it matters: in a browser that doesn't trust the cluster's CA, pages load only past a warning, and the
    dashboard's live connections fail. That gives "Websocket connection for dynamic parameters unexpectedly closed"
    when creating a workspace, and "Unable to retrieve latest inbox notifications".
  * Flatpak browsers now get the CA (verified with flatpak Chromium 155).
  * Recent snap Chromium/Brave builds find the CA in their database but don't take its trust from there.
    `trust-ca` now says so and explains how to import the CA once in the browser's certificate manager.

## 1.18.0 – 2026-10-11

Coder `2.0.0-platform.15` (fork `0a8293071`). Workspace image `ghcr.io/user187x/coder-workspace:1.6.0`.

* **Announcement banner.**
  * The text is centred; the Confirm button stays at the right.
  * A **Viewed** box sits just left of the Confirm button, at the same size. It shows the date and time the user
    first saw the announcement, as the admins' report records it, in small text: "Viewed" and the date spread
    edge to edge, the time below at the right.
    A user who doesn't confirm still sees that the view is on record, and the admins see it too.
    * The platform service's `/api/me` now returns `viewedAt` and `ackedAt`.
* **The typewriter effect no longer flickers with repeat.**
  * Each letter's 1 ms CSS animation was repeated forever, so every letter blinked.
  * With repeat, a timer now types the message, holds it for 3 s, clears it and types it again.
  * Without repeat it still types once.
* **Navbar.**
  * The workspaces menu reads **Resources** next to its icon, and its "Workspace" item is now "Workspaces".
  * The **Admin** menu has an icon before its label, like the other navbar items. The image holds both variants
    side by side (the white gear on a black disc for the dark theme, the outlined gear for the light theme), and
    each theme shows its half.
  * The images are the fork's `site/static/admin-icon.svg` and `site/static/agent-outdated.png`.
* **The workspace card shows the workspace's name** instead of its agent's ("main"). When a workspace has several
  agents, each card adds its agent's name after it.
* **"Unable to retrieve latest inbox notifications" no longer appears for a passing hiccup.**
  * Before, one failed attempt of the inbox's live connection showed the message for good, even though the inbox
    still loaded. A sign-in redirect or a Coder restart cutting connections was enough, often right after a fresh
    sign-in.
  * Now the connection reconnects by itself, with backoff, and fetches what it missed.
  * The message appears only after three failures in a row, and goes away once the connection is back.
* **An outdated workspace agent** shows the update badge image instead of the word "Outdated". Its popover, with
  the versions and "Update workspace", is unchanged.
* **marimo stays in its window.** "Create a new notebook", notebook links and "open file" no longer open a new
  browser window.
  * They navigate the same window. Embedded in the workspace's app panel, that means the panel itself.
  * Ctrl/Cmd/Shift-click and middle-click still open a new tab.
  * The image adds `marimo/same-window.js` to marimo's page.

## 1.17.0 – 2026-10-11

Coder `2.0.0-platform.14` (fork `84b706a1f`). Workspace image `ghcr.io/user187x/coder-workspace:1.5.0`.

* **The desktop has a web browser: Firefox.** XFCE's "Web Browser" and the dock's globe launcher open it at once.
  * It is Mozilla's own `.deb`; Ubuntu's `firefox` package only installs a snap, which cannot run in a container.
  * Its policies suit an offline network (`desktop/firefox-policies.json` in the image): no updates, telemetry,
    studies, Pocket, sponsored content, welcome or terms-of-use pages, and no online content on the new-tab page.
    New tabs and the start page are blank, and a workspace restart does not bring up "Restore Session".
  * It trusts the system CA store through p11-kit, so the cluster's own CA works.
  * The pod gets a 1 GiB in-memory `/dev/shm`; with the default 64 MiB, Firefox tabs crash.
* **A faster desktop over VNC.**
  * XFCE's compositor is off. That is the image default; desktops set up earlier get it turned off once, and a
    later change in Window Manager Tweaks is kept.
  * The VNC server updates at 60 frames per second.
  * noVNC uses the cheapest compression (`compression=0`), which costs less CPU at both ends.
  * The pointer is drawn by your own browser, so its speed is your mouse's. What remains is the round trip through
    Coder's proxy for what happens on the desktop.
* **marimo starts in its dark theme.** It is set once per user; a later change in marimo's settings is kept.
* **App panels fit and resize.**
  * A File Browser panel first grows to fit its page, so it has no scroll bar.
  * Every app panel has a handle at its bottom right. Drag it, or use the up and down arrow keys, to set the height;
    the width always stays the card's.
  * The height is remembered.
* **The top bar can be rearranged.** Workspaces, the latency menu and HUD can sit on either side of the announcement
  banner.
  * Start it by right-clicking one of them, or with the user menu's "Rearrange top bar".
  * The banner shows as a dashed placeholder, and the buttons swap places around it: drag them, or use the left and
    right arrow keys.
  * Done (or Escape) ends it, and Reset restores the default order.
  * The order is remembered per browser.
* `minikube-support`: a rebuilt image under the same tag now reaches every node. `minikube image load` skipped a
  node that was running containers of the old build; such a node now gets the image loaded directly.
* **No Favorite.** The workspace page has no Favorite button, and the workspaces list no longer shows the star.
* **No links to Coder's online documentation.** The deployment is air-gapped, and Coder publishes no offline (PDF)
  edition of its docs, so these links would be dead.
  * Removed: docs links and "View docs" buttons, the filters' "Learn more" items, help-popover links, the agents'
    "Troubleshoot" and "View docs to troubleshoot" links, and "Install Coder Desktop".
  * Sentences that pointed to the docs are reworded.
  * A "Documentation" support link is never shown.
  * A CSS rule hides any docs link still produced elsewhere, such as in a template README.

## 1.16.0 – 2026-10-11

Coder `2.0.0-platform.13` (fork `237423e43`).

* **The terminal opens in tmux with btop.** The Terminal panel's custom command now defaults to
  `tmux new-session -A -s monitor "btop"`.
  * Terminals open at the same time share one `monitor` session; the first one starts it.
  * The bundled tmux configuration sets `destroy-unattached on`, so the session ends when its last terminal closes.
    The next terminal starts btop fresh.
  * Everyone gets the new default once, because it is stored under a new key. A user can change it afterwards.
* **"Animations" switch in the user menu** (on by default, remembered per browser). Off:
  * the agent cards go back to Coder's stock solid borders (green when ready);
  * the initializing title stops fading;
  * every animated GIF in the dashboard shows only its first frame (logo, avatars, the initializing animation).
    Cross-origin pictures are the exception, because they can't be copied.
* **The agent card's drop-down panels can be reordered.**
  * Drag a panel by the grip at the left of its header. With the keyboard: focus the grip, press Space, use the arrow
    keys, then Space again.
  * The order is remembered per workspace and agent, in this browser.
* **App panels.** A folder tab with a round **+** sits at the top right of the top panel, whichever panel that is. It
  adds a panel with one of the workspace's apps embedded, as the Terminal is: File Browser, Desktop (noVNC), marimo
  or code-server.
  * The app loads when its panel first opens. Collapsing the panel keeps the app as it is.
  * The bin removes the panel and closes the app.
  * The app's box can be resized from its bottom-right corner.
  * The + and the app panels wait until the workspace is ready.

## 1.15.0 – 2026-10-11

Coder `2.0.0-platform.12` (fork `2bd982755`).

* **A workspace's apps open only once the whole workspace is ready.** That means the agent is connected and its
  startup scripts are done, including the terminal's tmux, code-server and the desktop.
  * Until then, the workspace page shows none of the following, and the workspaces list shows no app buttons:
    * the app buttons, VS Code, Terminal, SSH and port forwarding;
    * the Terminal drop-down, which shows "Opens when the workspace is ready".
  * A start that failed or timed out also opens them, so one broken module never locks the user out. The card's
    border turns orange for that.
* **"WORKSPACE INITIALIZING" panel.** While a workspace starts, it takes the place of the apps and of Coder's
  "Workspace agent is connecting" message. The design is `development/coder-fork/design/workspace-initializing.html`,
  which was `example.html`.
  * Its look: warning stripes above and below, the brick animation tinted orange, and a title that slowly fades in
    and out. The animation is the fork's `site/static/loading-brix.gif`.
  * What it says:
    * the current step (agent connecting, preparing, running the startup scripts);
    * a progress bar with how many startup scripts have finished and which are still running;
    * the elapsed time.
* The workspace issue indicator no longer has **Troubleshooting** buttons (the template's docs link).

## 1.14.0 – 2026-10-11

Workspace image `ghcr.io/user187x/coder-workspace:1.4.0`.

* **marimo works with no network at all.** It used to be `pip install`ed from the internet at every workspace start
  (offline: no marimo), with only its bare core, so its features asked to install packages and failed.
  * The workspace image now has everything in `/opt/marimo`, pinned in
    `development/workspace-image/marimo/requirements.lock`:
    * marimo 0.24.2 with its extras: SQL cells (duckdb, polars, pyarrow, sqlglot), charts (altair, vegafusion,
      vl-convert), formatting (ruff), Jupyter import (nbformat), sandbox/`uv`, the AI assistant's client, and the
      language server (python-lsp-server, python-lsp-ruff);
    * a data stack: numpy, pandas, scipy, scikit-learn, statsmodels, sympy, networkx, matplotlib, seaborn, plotly,
      anywidget, openpyxl, xlsxwriter, pillow, lxml, beautifulsoup4, requests, httpx, sqlalchemy, tabulate, jinja2;
    * `tkinter` (`python3-tk`).
  * Each user gets `~/.marimo-venv`, which sees all of `/opt/marimo` and takes the packages they add. It replaces the
    old internet-installed venv on the first start. With no network, pip and uv give up at once instead of hanging.
  * A package manager marimo is set to that is not installed (pixi, rye, poetry) falls back to `uv`.
  * The image build checks all of it with networking switched off: it runs every cell of
    `marimo/smoke_test.py` and serves marimo. To add packages, list them in `marimo/requirements.in` and rebuild.
  * The image grows from 3.6 to 6.0 GB (the Python packages are 1.8 GB).
* Docs: the known air-gap gap is now only code-server and File Browser, which are still downloaded at workspace start.

## 1.13.1 – 2026-10-11

* **Default logo: the Minecraft creeper** (`helm-chart/defaults/logo.gif`, a 200x200 animated GIF). A new installation
  starts with it; an existing one keeps the logo it has (an uploaded one, or the previous default) until an admin
  changes it in Customize.

## 1.13.0 – 2026-10-11

Workspace image `ghcr.io/user187x/coder-workspace:1.3.0`.

* **Desktop (noVNC) background by default:** XFCE's dark "cp-dark" wallpaper, zoomed to fill the screen. It's a system
  default (`/etc/xdg/xfce4/xfconf/.../xfce4-desktop.xml`, for TigerVNC's monitor `VNC-0`), so a wallpaper a user
  picks in Desktop settings still wins. It used to be XFCE's built-in plain default. Takes effect after a workspace
  update.

## 1.12.2 – 2026-10-11

* **Desktop (noVNC) locked down;** the "you are exposing your VNC server to all users" warning is gone.
  * TigerVNC's X server now starts directly (not through `vncserver`) and listens on no TCP port, only on a Unix socket
    that only the workspace user can open (`/tmp/coder-vnc/rfb.sock`: directory 0700, socket 0600).
  * websockify serves noVNC on `127.0.0.1:6901` only (it listened on every interface, so the password-less desktop was
    reachable from the cluster network) and connects to that socket. The only way in is Coder's app proxy.
  * The X display has an authorization cookie (`~/.Xauthority`).
  * Takes effect when a workspace restarts.

## 1.12.1 – 2026-10-11

* **No gap when Coder rolls out:** the old Coder pod keeps serving for 15 s after it is taken out of the Service
  (`preStop` sleep), so Traefik stops sending it requests before Coder closes its server. Until now those requests got
  a 502, which showed as the health warning `EWS01` during a deploy. The new pod already started first and had to be
  ready before the old one stopped. Tested with a restart under a request every 0.2 s: all 766 returned 200.

## 1.12.0 – 2026-10-11

Coder image `2.0.0-platform.11-airgap` (fork `92daaa856`).

* **Agent terminal drop-down:**
  * When the terminal terminates (its shell exits, the connection drops, or End session), the drop-down rolls back up
    and starts over: the next open is a new session.
  * "End session when closed" is a slider toggle.
  * The custom command is on by default and runs btop.
  * Controls in three groups: the text size buttons and size in a small oval on the left, the custom command centred,
    the session controls on the right.
  * The terminal's columns x rows show as a very faint overlay pinned to its top-right corner.
  * The pop-out window has a button that opens the session in a separate browser window (Coder's terminal page, same
    session).
* **btop refreshes every 100 ms** instead of its default 2000. The workspace start sets it for users without a btop
  configuration, or whose configuration still has btop's default; a value someone chose is left alone.

## 1.11.0 – 2026-10-11

Workspace image `ghcr.io/user187x/coder-workspace:1.2.0` (Coder image unchanged: `2.0.0-platform.10-airgap`).

* **k9s and kubectl in every workspace:** a new self-contained package, `k8s-devtools` 1.0.0
  (`development/workspace-image/packages/`, 48 MB), built by `development/tools/build-k8s-devtools.sh` from the
  developer's own machine:
  * the exact custom k9s binary (v0.50.18, commit `6dbf571c…-patched`) and its configuration: settings, aliases, the
    copy-secret plugin (with `k9s-copy-secret` on PATH) and all skins. Per-machine state (`clusters/`,
    `screen-dumps/`, `benchmarks/`, backups) is left out. `k9s` gives users without a k9s configuration the bundled
    one on first run; an existing one is never touched;
  * the exact kubectl binary (v1.36.0);
  * bash completion for both, now enabled for every interactive shell (`/etc/bash.bashrc`; the default `~/.bashrc` is
    unchanged).
* kubectl and k9s need a cluster to talk to, via a kubeconfig in `~/.kube/config` or `KUBECONFIG`. Workspaces get no
  cluster access of their own (no extra permissions). minikube is not included: running it inside a workspace needs a
  privileged pod or Sysbox nodes.

## 1.10.2 – 2026-10-11

Coder image `2.0.0-platform.10-airgap` (fork `f6ebdb50e`).

* **Workspace card borders:** only just noticeable, and slower.
  * Every layer is at 45% strength.
  * The ready ring is 2 px wide and takes 9 s per turn (it was 4 px and 2 s).
  * The electric border runs at a third of its pace (18 s per cycle) with less than half the displacement, and a fainter
    sheen and glow.
  * Strength and ring speed are the CSS variables `--fx-strength` and `--fx-ring-turn` in `agentBorder.css`.

## 1.10.1 – 2026-10-11

* **Workspaces button icon:** green. The navbar's icon is now the chart's `files/ui-updates/workspaces-icon.png`, which
  Traefik serves at `/workspaces-icon.png` from the platform service, so changing it takes a chart upgrade, not a new
  Coder image. The fork's built-in copy is green too (fork `542e5e2b5`, in the next Coder image). The new icon was first
  committed in `3a89b21` (with a FORK.md note).

## 1.10.0 – 2026-10-11

Coder image `2.0.0-platform.9-airgap` (fork `019d9745b`) and workspace image `ghcr.io/user187x/coder-workspace:1.1.0`.

* **Workspace image 1.1.0:** btop, bat (`bat` links to Ubuntu's `batcat`), vim, eza, fzf, grc, jq and yq installed (tmux
  already was). yq is Ubuntu's package: the jq wrapper for YAML (kislyuk/yq, jq syntax), not the Go yq.
* **Default `~/.bashrc`** (`development/workspace-image/shell/bashrc`): every workspace start installs it for users who
  have no `~/.bashrc`, or still Ubuntu's stock one; an edited `~/.bashrc` is left alone. Also installed when missing:
  `~/.profile` (so login shells read `~/.bashrc`) and `~/.config/vim/vimrc`, which the new `.bashrc` points Vim at
  (`VIMINIT`): Vim's defaults, then `~/.vimrc` if there is one.
* **Agent terminal:** a "Custom command" toggle. It's off by default; turned on, its command (btop until changed) is
  editable, new sessions run it first and continue in the shell when it exits, and "Run" starts one at once. The text
  size buttons are Font Awesome's plus-circle / minus-circle.

## 1.9.0 – 2026-10-10

Coder image `2.0.0-platform.8-airgap` (fork `a68ffe61e`).

* **Workspace page:** the agent card's solid green border is replaced by an animated border that shows the workspace's
  state:
  * **Initializing** (starting, agent connecting, startup scripts running): the electric border of
    `update-todos/workspace-loadding.html`, in green.
  * **Pending or a problem** (build queued, a startup script or module failed or timed out, the agent timed out
    connecting, a dev container error): the same border in fiery orange.
  * **Failed** (failed build, or the agent of a running workspace disconnected): the same border in red.
  * **Ready**: the rotating green border of `update-todos/workspace-border.html`.

  Stopped, stopping and deleted workspaces keep Coder's border. Nothing in the layout moves between states, the card
  (its logs, its terminal) is never remounted, and with reduced motion nothing animates.

## 1.8.0 – 2026-10-10

Coder image `2.0.0-platform.7-airgap` (fork `d7a068929`).

* **Breadcrumb:** Admin Settings > *category* names the category open (Health, Authentication, Customize, ...) instead
  of a fixed "General", which stays for pages outside the categories.
* **Fixed: the end of long pages was hidden** behind the deployment stats bar and the Chats dock (e.g. "Source Code
  Pro", the last terminal font in Customize). Every page that scrolls ends with room to spare; full-height pages (the
  workspace page) are unchanged.
* **HUD:**
  * Fixed: Highest CPU / Highest memory showed "—" without metrics-server, even with workspaces running. They fall back
    to what the running workspaces were built with, then to what their pods request (the hint says which).
  * Fixed: a tie quietly showed one account. It now shows the first account and the number tied ("admin +1 tied").
  * Every figure opens what is behind it:
    * the top account's workspace (CPU, memory, GPUs, ping, time online)
    * that account's workspaces (most workspaces, most failures)
    * the failing workspace
    * the running workspaces, User Quota, or Workspace Health
  * Moving figures to the top is a **Sort** menu at the top right of the totals bar, with Reset order.
* **SSH key from the .p12:**
  * Settings > Certificates can make the certificate's private key your Git SSH key: at upload ("Also use its private
    key as my SSH key"), or with "Use as SSH key..." (Settings > SSH keys > "Use P12..." leads there).
  * The key is decrypted in the browser, both AES and the legacy 3DES of many CAs. The password is asked for only when
    the file has one, is never sent, stored or logged, and is cleared once the key is out.
  * Coder stores the key as it stores the keys it generates (new endpoint `PUT /api/v2/users/{user}/gitsshkey/import`:
    RSA 2048+, ECDSA P-256/384/521, Ed25519).

## 1.7.0 – 2026-10-10

Coder image `2.0.0-platform.6-airgap` (fork `a65bc39b7`) and a new workspace image `ghcr.io/user187x/coder-workspace:1.0.0`.

* **Fixed: `core.<pid>` files in every workspace home.** The template installed `xfce4-goodies` at every start, which
  pulls in light-locker; the screen locker needs a display manager, crashed at each desktop start, and with unlimited
  core dumps left a ~45 MB `core.<pid>` each time. The workspace image leaves light-locker (and LightDM) out and does not
  start the XFCE screensaver; the pod starts the agent with `ulimit -c 0`; the startup script removes the core dumps
  earlier workspaces left (only `core.<digits>` files that are ELF core dumps).
* **Workspace image** (`development/workspace-image`, `tools/build-workspace-image.sh`): enterprise-base with the desktop
  preinstalled (no `apt-get` at start, so it also works offline), JetBrains Mono Nerd Font as the desktop's and its
  terminal's font and the default monospace, and tmux 3.8 with the bundled configuration (`tmux-installation.deb`; set
  up for each user without one). `codercom/enterprise-base` is no longer shipped. The startup script starts the desktop
  first, and a failed marimo download (no internet) no longer stops the rest of it.
* **Web terminal:** JetBrains Mono Nerd Font is bundled and the default terminal font. The agent terminal drop-down has
  a Pop out button (a floating window with the same session; closing it leaves the drop-down collapsed) and text size
  buttons that start at the largest size fitting 80x24 (btop's minimum) and show the columns x rows.
* **Global Workspace Scheduler** (Health > User Quota): require start and stop times on every workspace (each user's, or
  one schedule for all), a maximum active time, ignore admins, exclude users. Enforced by Coder on create and on every
  schedule change; users fix existing workspaces in a dialog before going on; the create form asks for the schedule.
* **Monitoring:** a security posture panel - zero-trust checks (privileged and dormant accounts, password sign-in, MFA in
  Keycloak, API tokens, shared ports and apps, pod isolation, network policies, image provenance) and runtime signals
  (restarts and OOM kills, failed workspaces, disconnected or outdated agents, workspaces that never stop).
* **HUD** (navbar, `/hud`, admins): totals, top accounts and health figures updated in place every 10 s, with changes
  and sparklines; click a figure to move it to the top.
* `minikube-support`: every custom image is loaded (`minikube ssh` read the rest of the image list from stdin, so only
  the first one was), and a build is recognised by its creation time and layers rather than the image ID, which differs
  between Docker's containerd image store and the nodes' (images were reloaded, and their pods restarted, every time).

## 1.6.0 – 2026-10-10

* Coder image `2.0.0-platform.5-airgap` (fork commit `ea80521c2`). The Admin menu starts with Settings and an add row
  (+) only (no more fixed Accounts / Health links). The + opens General's categories not linked yet (Accounts, Super
  Intelligence, Announcement, Authentication, Classification, Customize, Health, Monitoring, Network, Notifications,
  OAuth2 Applications, Observability, Overview, Persistence, Security); each quick link shows its category on the left
  and a remove button and a drag handle on the right (drag, or Enter + arrow keys + Enter, to reorder). Saved per user
  at once; as many links as there are categories (server limit raised from 8 to 32).
* `minikube-support`: an image rebuilt under the same tag is reloaded (compared by image ID), and `./make --deploy`
  restarts the Deployments that run it.

## 1.5.0 – 2026-10-10

* No nginx: the workspace template's icons (`/__icons/<file>`) are served by the platform service
  (`coder-ui-updates`) from the icons ConfigMap, and Traefik's Gateway routes `/__icons/` to it
  (`coder-workspace.icons.backend`). The nginx Deployment and Service and the `nginxinc/nginx-unprivileged` image are
  gone (one pod and ~24 MB fewer). Responses: images only, plain file names, `Cache-Control: public, max-age=3600`,
  ETag, and SVGs sandboxed by a Content-Security-Policy. Upgrading removes the old Deployment and Service.

## 1.4.0 – 2026-10-10

* `./make --deploy`: guided install for Minikube or EKS. Asks the domain (derives `coder.`, `*.`, `keycloak.`),
  creates self-signed certificates or checks the ones you bring (pair, chain, hostnames, expiry; DER and
  encrypted keys converted), then installs the prerequisites, Secrets and chart. Answers are remembered.
* Minikube: the sudo steps (tunnel, `/etc/hosts`, trusting the CA) are planned and agreed to up front; sudo asks
  for the password once, then and there, and the steps run at the end unattended. Steps already done are skipped;
  without sudo the exact commands are printed.
* `./make --deploy` writes a log (`.deploy/deploy.log`, follow it with `tail -F`) and, when a step fails, names the
  failing command and the log instead of exiting silently. Fixed: the deploy exited silently (exit 1) before
  starting the tunnel when no tunnel was running yet.
* `minikube-support`: profile, `local-path` storage as default (minikube's hostpath class breaks CloudNativePG
  replicas on multi-node clusters), image loading, CoreDNS rewrite of `*.<domain>` to Traefik, a development
  Keycloak with client `coder`, `minikube-expose-services` / `minikube-sync-hosts` (profile-aware versions of the
  ~/devbox functions; own `/etc/hosts` block), `trust-ca`. Traefik keeps the in-cluster IP `coder.<domain>` already
  resolves to, so existing DNS keeps working.
* `x509-support`: CA (with keyUsage, accepted by strict TLS clients), wildcard server certificate, CA bundle,
  client certificates, and the checks above. Adapted from ~/devbox/data/util/x509/pki-toolkit.
* Python images default to `docker.io/library/python` (no longer the reference cluster's Harbor).
* `install.sh`: works with Helm 4 (no `--show-resources`), quieter output.

## 1.3.0 – 2026-10-10

* Default admin `admin` / `admin@admin.com` / password `admin` (`global.firstUser`), created on a fresh database. The
  bootstrap Job sets the password once (Coder's API rejects weak passwords, so it writes the hash directly); later
  syncs never reset it. Change it after the first sign-in.
* Password sign-in on by default (`global.disablePasswordAuth: "false"`): the admin uses its password, everyone else
  Keycloak. `"true"` keeps 1.2.0's Keycloak-only behaviour (admin = the Keycloak account with `firstUser.email`).
* The template-push token is minted on every sync, so pushes no longer depend on the admin's password.
* Banner: the unreleased coder-banner 0.4.3 (optional `gateway.sectionName`, `nodeSelector`), starting blank
  (hidden, no message) until an owner publishes one.
* Development baseline in git: `development/tools/build-bundle.sh`; private material kept outside the repository.

## 1.2.0 – 2026-10-10

Verified end to end in an air gap (DEPLOY.md on a kind cluster with egress blocked; 19/19 sign-in, add-on and
workspace checks).

* Coder image `2.0.0-platform.4-airgap`: the fork's image plus a Terraform provider mirror. Without it every template
  push and workspace build fails offline.
* With password sign-in disabled, the bootstrap Job links the first owner to the Keycloak account with
  `global.firstUser.email` and mints a short-lived token for the template push (Coder blocks every password login).
* `agent-audit.tf` fixed (`%{` escaped) and made optional (`coder-workspace.agentAudit`, off).
* Every `checksum/*` annotation quoted (an all-digit hash was rejected by Argo CD).
* `install.sh` uses server-side apply (the 1 MB default-logo ConfigMap exceeded the annotation limit).
* Python from `docker.io/library/python` instead of the private Harbor.
* Gateway API v1.5.1 CRDs added to `prereqs/`; Argo CD Application uses server-side diff (HTTPRoutes stayed OutOfSync).
* Every image bundled: `images/custom/` (the fork's image, on no public registry) and `images/public/{chart,prereqs,platform}/`,
  pinned by digest in `images.txt`; `mirror-images.sh push/save/verify/list`; a `-slim` tarball without the public images.

## 1.1.0 – 2026-10-09

* `global:` settings (domain, hosts, registry, pull secrets, Gateway, OIDC, first user, secret names) drive every
  component, so the Argo CD Application can set them; templated values in the vendored subcharts ("platform patch").
* Argo CD Application (`argocd/coder-airgapped-application.yaml`) installing the chart from an OCI registry.

## 1.0.0 – 2026-10-09

* First capture of the deployment running on minikube (Argo CD `coder` + `coder-banner`, Helm `coder-workspace`,
  `coder-ui-updates`) as one umbrella chart, render-verified against the live objects.

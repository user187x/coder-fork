// The Deployment-settings views coder-ui-updates adds to Coder (served at /__coder-ui/views.js).
//
// The rewritten Coder router renders an empty <div class="coder-ui-view"> for /deployment/<view> and calls
// mount(div, {view, title}) from this module. That div sits in Coder's own layout (breadcrumbs, Deployment sidebar,
// content column); this fills it with a Coder-style page header and the view itself, in a same-origin frame so its
// styles and Coder's never mix:
//
//   announcement    the coder-banner admin page (/__banner/admin). It sends `frame-ancestors 'none'`, so it is
//                   fetched (with the user's Coder session) and loaded as a srcdoc frame; its scripts run unchanged
//                   under Coder's CSP. announcement-embed.css fits it to Coder's look, admin-embed.js rearranges it.
//   classification  /__coder-ui/classification.html  the top/bottom classification marking
//   keycloak        /__coder-ui/keycloak.html        Keycloak <-> Coder sign-in: discovery, suggestions, fixes
//   customize       /__coder-ui/logo.html            the logo (Coder's own Appearance settings follow it, rendered by Coder)
//   network         /__coder-ui/network.html         the live cluster map, above Coder's own Network settings
//   agents          /agents                          Coder Agents itself, embedded: the frame's own dashboard.js hides its
//                                                    navbar, marking and announcement (the page around it has them)
//
// Each frame gets Coder's theme tokens and fonts (kept in step when the user switches theme), grows with its content,
// and has its #toast shown in the dashboard's viewport.

const ADMIN_URL = '/__banner/admin';
const asset = (name) => new URL(name, import.meta.url).pathname;
const VIEWS = {
  announcement: { description: 'Show an announcement across the Coder dashboard and see who has confirmed reading it.' },
  classification: { src: asset('classification.html'), description: 'Mark every Coder page with its classification, in a bar at the very top and the very bottom.' },
  keycloak: { src: asset('keycloak.html'), description: "Keycloak sign-in for Coder: what is configured, what is missing, and one click to fix it." },
  customize: { src: asset('logo.html'), description: 'The logo shown everywhere in Coder (navigation bar, sign-in and set-up pages, browser tab icon), and your appearance: theme and terminal font.' },
  network: { src: asset('network.html'), keepTitle: true, description: 'A live map of the Kubernetes cluster Coder runs on: nodes, their addresses and load, and the workspaces on each. Refreshes every 5 seconds.' },
  agents: { src: '/agents', embed: true, description: 'Coder Agents: AI agents that work in your workspaces. Chats open right here.' },
  monitoring: { src: asset('monitoring.html'), description: 'Every user and their running workspaces. Open one to follow its logs live.' },
  certificates: { src: asset('certificates.html'), description: 'Your .p12 certificate, installed into every workspace you start as ~/.cert/<your username>.p12.' },
  'workspace-health': { src: asset('workspace-health.html'), description: 'Workspaces whose last build failed or whose agents are unhealthy. Select one to open it.' },
};

// Frames navigate the dashboard without a full reload: react-router follows history changes it is told about.
window.__cuiNavigate = (url) => {
  window.history.pushState({}, '', url);
  window.dispatchEvent(new PopStateEvent('popstate', { state: {} }));
};
// Coder theme tokens (HSL triplets on <html>) handed to the frames as --coder-<name>.
const TOKENS = [
  'surface-primary', 'surface-secondary', 'surface-tertiary', 'surface-invert-primary',
  'content-primary', 'content-secondary', 'content-disabled', 'content-invert', 'content-link',
  'content-success', 'content-warning', 'content-destructive', 'border-default', 'border-secondary',
];

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text) node.textContent = text;
  return node;
}

// Same markup and utility classes as Coder's SettingsHeader, so the heading matches the other Deployment pages.
function header(title, description) {
  const group = el('hgroup', 'flex flex-col justify-between items-start gap-2 pb-6 sm:flex-row');
  const col = el('div', 'text-sm flex flex-col gap-2 flex-1');
  const row = el('div', 'flex flex-row gap-2 items-center');
  row.append(el('h1', 'm-0 flex items-center gap-2 leading-tight text-3xl font-semibold', title));
  col.append(row, el('p', 'm-0 text-content-secondary font-medium leading-normal', description));
  group.append(col);
  return group;
}

function notice(text) {
  return el('p', 'm-0 text-sm text-content-secondary', text);
}

function themeCss() {
  const root = document.documentElement;
  const style = getComputedStyle(root);
  const vars = TOKENS.map((t) => `--coder-${t}: ${style.getPropertyValue(`--${t}`).trim()};`).join(' ');
  const font = getComputedStyle(document.body).fontFamily.replace(/[<>]/g, '');
  const scheme = root.classList.contains('light') ? 'light' : 'dark';
  return `:root { ${vars} --coder-font: ${font}; color-scheme: ${scheme}; }`;
}

// Coder's own @font-face rules (absolute /assets/ URLs), so the embedded page uses the same typeface.
function fontFaces() {
  const rules = [];
  for (const sheet of document.styleSheets) {
    let list;
    try { list = sheet.cssRules; } catch { continue; } // cross-origin sheet
    for (const rule of list) if (rule instanceof CSSFontFaceRule) rules.push(rule.cssText);
  }
  return rules.join('\n');
}

// The frame element and the page inside it must use the same color-scheme, or the browser paints an opaque canvas.
function frameScheme(frame) {
  frame.style.colorScheme = document.documentElement.classList.contains('light') ? 'light' : 'dark';
}

function syncTheme(frame) {
  frameScheme(frame);
  const doc = frame.contentDocument;
  const style = doc && doc.getElementById('coder-ui-theme');
  if (style) style.textContent = themeCss();
}

// Grow the frame with its content (the page scrolls, never the frame).
function autoSize(frame) {
  const doc = frame.contentDocument;
  let queued = false;
  const fit = () => {
    queued = false;
    const body = doc.body;
    if (!body) return;
    // The emoji picker and toasts can overflow the body: include descendants' extent.
    let bottom = body.scrollHeight;
    for (const pop of doc.querySelectorAll('.emoji-pop:not([hidden]), [data-cui-overflow]:not([hidden])')) {
      bottom = Math.max(bottom, pop.getBoundingClientRect().bottom + doc.defaultView.scrollY);
    }
    frame.style.height = `${Math.ceil(bottom) + 4}px`;
  };
  const queue = () => { if (!queued) { queued = true; requestAnimationFrame(fit); } };
  new doc.defaultView.ResizeObserver(queue).observe(doc.body);
  new doc.defaultView.MutationObserver(queue).observe(doc.body, { subtree: true, childList: true, attributes: true, attributeFilter: ['hidden', 'open', 'class'] });
  fit();
}

// The admin page's toast is `position: fixed` inside the frame, i.e. at the frame's bottom edge, which can be below the
// fold (announcement-embed.css hides it). Show the same message pinned to the dashboard's viewport instead.
function relayToast(frame, root) {
  const source = frame.contentDocument.getElementById('toast');
  if (!source) return;
  // A child of the view's own div, so React removes it together with the view when the user navigates away.
  const toast = el('div', 'text-sm font-medium');
  toast.setAttribute('role', 'status');
  toast.setAttribute('aria-live', 'polite');
  toast.hidden = true;
  root.append(toast);
  // Above the bottom classification bar, if one is shown (classification.js sets its height on <html>).
  const base = 'position:fixed;right:24px;bottom:calc(24px + var(--coder-classification-h, 0px));z-index:50;max-width:min(420px,calc(100vw - 48px));'
    + 'padding:12px 16px;border:1px solid;border-radius:8px;box-shadow:0 8px 24px rgb(0 0 0 / .25);color:hsl(var(--content-primary));';
  const copy = () => {
    const error = source.classList.contains('error');
    toast.textContent = source.textContent;
    toast.hidden = source.hidden;
    toast.style.cssText = base + (error
      ? 'background:hsl(var(--surface-destructive));border-color:hsl(var(--border-destructive))'
      : 'background:hsl(var(--surface-secondary));border-color:hsl(var(--border-default))');
  };
  const watch = new frame.contentWindow.MutationObserver(copy);
  watch.observe(source, { attributes: true, childList: true, characterData: true, subtree: true });
}

// The frame is ready: theme, size and toast; keep following Coder's theme until the view is gone.
function wire(frame, root) {
  frame.addEventListener('load', () => {
    const doc = frame.contentDocument;
    if (!doc || !doc.body) return;
    let style = doc.getElementById('coder-ui-theme');
    if (!style) {
      style = doc.createElement('style');
      style.id = 'coder-ui-theme';
      doc.head.append(style);
    }
    if (!doc.getElementById('coder-ui-fonts')) {
      const fonts = doc.createElement('style');
      fonts.id = 'coder-ui-fonts';
      fonts.textContent = fontFaces();
      doc.head.prepend(fonts);
    }
    syncTheme(frame);
    autoSize(frame);
    relayToast(frame, root);
  });
  const themeWatch = new MutationObserver(() => {
    if (!root.isConnected) { themeWatch.disconnect(); return; }
    syncTheme(frame);
  });
  themeWatch.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] });
}

function frameFor(title) {
  const frame = el('iframe', 'block w-full border-0');
  frame.title = title;
  frame.style.cssText = 'display:block;width:100%;border:0;min-height:320px;background:transparent';
  frameScheme(frame);
  return frame;
}

// A whole Coder page (Agents) in a frame: it fills the rest of the window instead of growing with its content (the
// page keeps its own scrolling panes), and follows the theme through Coder's own appearance setting.
function embedFrame(title, src) {
  const frame = frameFor(title);
  frame.dataset.cuiEmbed = '1';
  frame.dataset.cuiEmbedPath = src;
  frame.allow = 'clipboard-read; clipboard-write';
  frame.style.minHeight = '560px';
  frame.style.border = '1px solid hsl(var(--border-default))';
  frame.style.borderRadius = '8px';
  const fit = () => {
    if (!frame.isConnected) { window.removeEventListener('resize', fit); return; }
    const top = frame.getBoundingClientRect().top + window.scrollY;
    frame.style.height = `calc(100vh - ${Math.round(top) + 40}px - var(--coder-classification-h, 0px))`;
  };
  window.addEventListener('resize', fit);
  requestAnimationFrame(fit);
  frame.src = src;
  return frame;
}

export async function mount(root, { view = 'announcement', title = 'Announcement', keepTitle = false } = {}) {
  const spec = { ...(VIEWS[view] || VIEWS.announcement), ...(keepTitle ? { keepTitle: true } : {}) };
  const top = () => header(title, spec.description);
  if (!spec.keepTitle) document.title = `${title} - Coder`;
  root.replaceChildren(top(), notice('Loading…'));

  if (spec.embed) {
    root.replaceChildren(top(), embedFrame(title, spec.src));
    return;
  }
  if (spec.src) {
    const frame = frameFor(title);
    wire(frame, root);
    frame.src = spec.src;
    root.replaceChildren(top(), frame);
    return;
  }

  let res;
  try {
    res = await fetch(ADMIN_URL, { credentials: 'same-origin', redirect: 'manual', cache: 'no-store' });
  } catch {
    root.replaceChildren(top(), notice('The banner service could not be reached. Reload the page to try again.'));
    return;
  }
  if (res.type === 'opaqueredirect' || res.status === 401) {
    root.replaceChildren(top(), notice('Your Coder session has expired. Sign in again to edit the banner.'));
    return;
  }
  if (res.status === 403) {
    root.replaceChildren(top(), notice('Only Coder admins can change the announcement banner.'));
    return;
  }
  if (!res.ok) {
    root.replaceChildren(top(), notice(`The banner service is not available right now (HTTP ${res.status}). Is the coder-banner app running?`));
    return;
  }
  const head = `<link rel="stylesheet" href="${asset('announcement-embed.css')}"><style id="coder-ui-fonts">${fontFaces()}</style>`
    + `<style id="coder-ui-theme">${themeCss()}</style>`;
  const html = (await res.text())
    .replace('<html', '<html data-coder-embed')
    .replace('</head>', () => `${head}</head>`)
    .replace('</body>', () => `<script type="module" src="${asset('admin-embed.js')}"></script></body>`);
  const frame = frameFor(`${title}: announcement banner editor`);
  wire(frame, root);
  frame.srcdoc = html;
  root.replaceChildren(top(), frame);
}
